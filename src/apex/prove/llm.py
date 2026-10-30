"""Engine-mode proof search: a model proposes, Lean answers, the errors go back to the model.

What the model can change is deliberately narrow. It returns a proof (the text after `:=`), helper
lemmas named `<ID>_...`, and extra Mathlib imports. The statement is never part of its answer:
the new spec text is rebuilt around the old statement, re-parsed, and refused unless every
existing declaration keeps its exact statement and no declaration other than the requested
lemmas appears. So a model can fail to prove something, but it cannot change what is claimed.

A candidate that compiles here has only passed the fast loop. It is written into the spec and
then goes through the cold verdict path (`compile_and_audit`, axiom audit, fidelity check) like
any proof a person wrote; nothing in this module can produce a verdict.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apex.lean.compiler import LeanMessage, run_lean
from apex.lean.guard import ALLOWED_IMPORT_ROOTS, scan
from apex.lean.project import LeanProject
from apex.llm.providers import Provider, ProviderError
from apex.theorems.spec import PROPERTY_NAME, Spec, SpecDecl, SpecError, parse_spec, render_decl

PROOF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proof", "lemmas", "imports"],
    "properties": {
        "proof": {
            "type": "string",
            "description": "What follows `:=` in the theorem, usually a tactic block starting "
            "with `by`. Lines after the first are indented.",
        },
        "lemmas": {
            "type": "string",
            "description": "Helper lemmas with complete proofs, placed before the theorem, or "
            "empty. Each name starts with the theorem's ID and an underscore.",
        },
        "imports": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extra Mathlib modules the proof needs, or empty.",
        },
    },
}

SYSTEM = """\
You write Lean 4 proofs for Apex, a verifier for Python functions. Apex has translated a Python \
function into a Lean model and drafted a theorem about it; a person approves the statement, and \
you only supply the proof. Your answer is a proposal: Apex compiles it cold, audits its axioms \
and discards it if anything is wrong, so a proof that does not compile is worth nothing.

Rules:
- Never change the theorem statement. You return only the proof (the text after `:=`), helper \
lemmas, and imports.
- No `sorry`, `admit`, `native_decide`, `axiom`, `unsafe`, `implemented_by`, `#eval`, macros, \
custom syntax or `set_option debug.*`. They are rejected before Lean runs.
- Helper lemmas must be `theorem` or `lemma` declarations with complete proofs, each named \
`<ID>_<something>` where <ID> is the theorem's name (for example `P3_step`). They may be \
`@[simp]`. Nothing else may appear in `lemmas`: no `def`, `instance`, `notation`, `open`, \
`namespace` or `variable`.
- Every line of the proof after the first must be indented. Write the proof so it does not \
depend on hypothesis names Lean generates (use `intro` with your own names).
- Imports are Mathlib modules (`Mathlib.Tactic.Ring`, `Mathlib.Data.List.Basic`, ...). Prefer \
none: core Lean already has `omega`, `simp`, `decide`, `induction`, `cases`, `split`.
- The model's definitions live in the namespace shown and can be unfolded by name (`simp [f]`, \
`unfold f`). Python's `//`, `%`, `abs`, `range` and `sum` are `Apex.pyFloorDiv`, `Apex.pyMod`, \
`Apex.pyAbs`, `Apex.pyRange` and `Apex.pySum`, defined in the prelude shown.
- When Lean's errors from a previous attempt are shown, read the goal state and fix the cause \
rather than retrying the same tactics."""

MAX_ERRORS_SHOWN = 4
_IMPORT = re.compile(r"^[A-Z][A-Za-z0-9_']*(\.[A-Z][A-Za-z0-9_']*)*$")


@dataclass
class Candidate:
    proof: str
    lemmas: str = ""
    imports: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    candidate: Candidate
    errors: list[str]
    """Empty when the candidate passed the fast check."""


@dataclass
class ProofSearch:
    name: str
    proposed_by: str
    closed: Candidate | None = None
    attempts: list[Attempt] = field(default_factory=list)
    stopped: str | None = None
    """Why the search ended without a proof before using every attempt (provider error, budget)."""

    @property
    def last_error(self) -> str | None:
        if self.stopped:
            return self.stopped
        return self.attempts[-1].errors[0] if self.attempts and self.attempts[-1].errors else None


@dataclass
class ProofContext:
    """Everything one theorem's proof search needs, shared by all attempts."""

    namespace: str
    model: str
    """The Lean model's definitions (without namespace wrapper)."""
    spec_text: str
    spec: Spec
    header: str
    """`import Apex.Prelude` + the spec's imports + options."""
    prelude: str
    project: LeanProject
    timeout_s: float
    tried_tactics: list[str] = field(default_factory=list)


def splice(spec_text: str, spec: Spec, decl: SpecDecl, cand: Candidate) -> str:
    """The spec with `cand` as the proof of `decl`, its lemmas before it and its imports on top.

    Raises ValueError unless the result parses and keeps every existing statement verbatim, so a
    candidate can only ever add the lemmas it names.
    """
    lemmas = cand.lemmas.strip()
    new_decl = render_decl(decl, cand.proof).rstrip("\n")
    block = f"{lemmas}\n\n{new_decl}" if lemmas else new_decl
    old = decl.text.rstrip("\n")
    if spec_text.count(old) != 1:
        raise ValueError(f"cannot locate {decl.name} in the spec")
    text = spec_text.replace(old, block, 1)
    missing = [m for m in dict.fromkeys(cand.imports) if m not in spec.imports]
    if missing:
        text = "".join(f"import {m}\n" for m in missing) + text
    try:
        new = parse_spec(text)
    except SpecError as e:
        raise ValueError(
            "the answer does not fit in a spec file (are all proof lines after the first "
            f"indented?): {'; '.join(e.problems)}"
        ) from e
    before = {d.name: d.statement_sha256 for d in spec.decls}
    after = {d.name: d.statement_sha256 for d in new.decls}
    for name, sha in before.items():
        if after.get(name) != sha:
            raise ValueError(f"the answer changed or removed the statement of {name}")
    for name in set(after) - set(before):
        if PROPERTY_NAME.match(name) or not name.startswith(f"{decl.name}_"):
            raise ValueError(
                f"lemma `{name}` must be named `{decl.name}_<something>` (and is not a property)"
            )
    return text


def _validate(cand: Candidate, decl: SpecDecl) -> list[str]:
    problems = []
    for m in cand.imports:
        if not _IMPORT.match(m) or m.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
            problems.append(f"import `{m}` is not a module of the pinned libraries")
    for f in scan(cand.lemmas + "\n" + cand.proof):
        problems.append(f"`{f.token}` is not allowed: {f.reason}")
    if not cand.proof.strip():
        problems.append("the proof is empty")
    return problems


def fast_check(ctx: ProofContext, decl: SpecDecl, cand: Candidate) -> list[str]:
    """Errors from one cold compile of the candidate (no axiom audit: not a verdict)."""
    problems = _validate(cand, decl)
    if problems:
        return problems
    try:
        new_spec = parse_spec(splice(ctx.spec_text, ctx.spec, decl, cand))
    except ValueError as e:
        return [str(e)]
    target = new_spec.get(decl.name)
    assert target is not None
    lemmas = [d for d in new_spec.helpers if d.name not in {h.name for h in ctx.spec.helpers}]
    extra = "".join(f"import {m}\n" for m in new_spec.imports if m not in ctx.spec.imports)
    head = f"{extra}{ctx.header}\nnamespace {ctx.namespace}\n\n{ctx.model}\n" + "".join(
        h.text + "\n" for h in ctx.spec.helpers
    )
    first_line = head.count("\n") + 1
    body = "".join(d.text + "\n" for d in lemmas) + target.text
    text = f"{head}{body}\nend {ctx.namespace}\n"
    run = run_lean(text, ctx.project, ctx.timeout_s)
    if run.code is None:
        return [f"Lean did not finish within {ctx.timeout_s:.0f}s; find a cheaper proof"]
    errors = [m for m, _ in run.messages if m.severity == "error"]
    if not errors:
        return []
    if any(m.line < first_line for m in errors):
        # The model or the existing helpers fail: no answer can help, so say so plainly.
        return ["the Lean model or existing spec lemmas do not compile: " + errors[0].message]
    return [_describe(m, first_line, body) for m in errors[:MAX_ERRORS_SHOWN]]


def _describe(m: LeanMessage, first_line: int, body: str) -> str:
    lines = body.splitlines()
    k = m.line - first_line
    where = f"line {k + 1} of your answer" if 0 <= k < len(lines) else f"line {m.line}"
    src = f"\n  at: {lines[k].strip()}" if 0 <= k < len(lines) else ""
    return f"{where}: {m.message.strip()}{src}"


def build_prompt(ctx: ProofContext, decl: SpecDecl, attempts: list[Attempt]) -> str:
    parts = [
        f"Prelude (namespace Apex, imported):\n{ctx.prelude.strip()}",
        f"Lean model (namespace {ctx.namespace}):\n{ctx.model.strip()}",
    ]
    if ctx.spec.helpers:
        parts.append(
            "Lemmas already proved in the spec (you may use them):\n"
            + "\n".join(h.text.strip() for h in ctx.spec.helpers)
        )
    if ctx.spec.imports:
        parts.append("Already imported: " + ", ".join(ctx.spec.imports))
    parts.append(f"Theorem to prove (ID {decl.name}; do not change the statement):\n{decl.text}")
    if ctx.tried_tactics:
        parts.append(
            "These one-line proofs already failed:\n"
            + "\n".join(f"    {t}" for t in ctx.tried_tactics)
        )
    for k, a in enumerate(attempts[-2:], start=max(1, len(attempts) - 1)):
        c = a.candidate
        shown = f"proof:\n{c.proof}"
        if c.lemmas.strip():
            shown = f"lemmas:\n{c.lemmas.strip()}\n{shown}"
        if c.imports:
            shown = f"imports: {', '.join(c.imports)}\n{shown}"
        parts.append(f"Attempt {k} was rejected.\n{shown}\nLean said:\n" + "\n".join(a.errors))
    return "\n\n".join(parts)


def search_proof(
    provider: Provider,
    ctx: ProofContext,
    decl: SpecDecl,
    *,
    attempts: int,
    deadline: float | None = None,
    check: Callable[[ProofContext, SpecDecl, Candidate], list[str]] = fast_check,
) -> ProofSearch:
    """Up to `attempts` proposals for one theorem, each checked by Lean, errors fed back."""
    search = ProofSearch(name=decl.name, proposed_by=provider.name)
    for _ in range(attempts):
        if deadline is not None and time.monotonic() > deadline:
            search.stopped = "the run's time budget ran out"
            break
        try:
            data = provider.complete_json(
                SYSTEM, build_prompt(ctx, decl, search.attempts), PROOF_SCHEMA
            )
            cand = Candidate(
                proof=str(data["proof"]),
                lemmas=str(data.get("lemmas") or ""),
                imports=[str(m) for m in data.get("imports") or []],
            )
        except ProviderError as e:
            search.stopped = f"{provider.name}: {e}"
            break
        except (KeyError, TypeError) as e:
            search.attempts.append(
                Attempt(Candidate(proof=""), [f"malformed answer from {provider.name}: {e}"])
            )
            continue
        errors = check(ctx, decl, cand)
        search.attempts.append(Attempt(cand, errors))
        if not errors:
            search.closed = cand
            break
    return search
