"""The deterministic verbs. Every surface (CLI, MCP, skill) calls these and nothing else.

Each verb accepts candidates from outside (a spec file written by a human, an agent in host mode,
or Apex's own LLM client in engine mode) and checks them. None of them calls a model.
"""

import fnmatch
import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from apex import __version__
from apex.analyze.purity import PurityResult, classify
from apex.analyze.target import FunctionInfo, load_function
from apex.core.config import ApexConfig
from apex.core.hashing import sha256_text
from apex.core.intent import Conflict, IntentModel, Property
from apex.core.report import (
    FunctionResult,
    FuzzEvidence,
    Gap,
    LeanError,
    ProofArtifact,
    PropertyResult,
    Provenance,
    Report,
    run_verdicts,
)
from apex.core.verdict import FAILURE_VERDICTS, ReasonCode, Verdict, exit_code_for
from apex.fidelity.bridge import FidelityError, check_fidelity
from apex.fidelity.fuzz import Engine, FuzzError, FuzzResult, fuzz
from apex.fidelity.fuzz import replay as replay_fuzz
from apex.fidelity.refute import RefuteResult, refute
from apex.intent.candidates import Example, Mined
from apex.intent.docstring import mine_docstring
from apex.intent.english import SpecItem, mine_english
from apex.intent.fuse import fuse, module_functions
from apex.intent.hints import HintError, parse_hint
from apex.intent.proposals import (
    ProposalCheck,
    ProposalSet,
    check_proposals,
    merge,
    to_candidates,
)
from apex.intent.source import mine_source
from apex.intent.tests import mine_tests
from apex.js import SAFE_INTEGER_PRECONDITION
from apex.lean.compiler import LeanMessage, TheoremOutcome, compile_and_audit, run_lean
from apex.lean.project import LeanProject
from apex.prove.tactics import probe
from apex.theorems.draft import DraftError, draft_property, formal_preconditions
from apex.theorems.review import ReviewStore
from apex.theorems.spec import Spec, SpecDecl, SpecError, parse_spec, render_decl
from apex.tla.bundle import Bundle, BundleError, load_bundle
from apex.tla.check import TlaCheckResult, check_bundle
from apex.tla.conform import HarnessError
from apex.tla.tlc import TlaUnavailable
from apex.translate.lean import FunctionSig, LeanModel, TranslationError, signature, translate
from apex.translate.literals import LiteralError, to_lean

if TYPE_CHECKING:
    from apex.core.outputs import ReviewStatus
    from apex.llm.providers import Provider

FILE_HEADER = "import Apex.Prelude\n"
OPTIONS = "set_option autoImplicit false\nset_option relaxedAutoImplicit false\n"


FIDELITY_GAP = Gap(
    reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
    explanation="The Lean model disagrees with the Python, so the proof is about the wrong "
    "function and is discarded.",
    next_steps=[
        "If Python raises here, guard the input and return None, or add a precondition.",
        "Otherwise report it as an Apex translator bug.",
    ],
)


NO_INTENT_GAP = Gap(
    reason_code=ReasonCode.SPEC_AMBIGUOUS,
    explanation="No intent found: nothing states what this function should do, so there is "
    "nothing to prove or refute.",
    next_steps=[
        "Add doctests or `post:` lines to the docstring, tests in test_<module>.py, or an entry "
        "in <module>.spec.md.",
    ],
)


class ApexToolError(RuntimeError):
    """Bad input or a broken environment. Maps to exit code 3, never to a verdict."""


# ---- workspace -------------------------------------------------------------------------------


@dataclass
class Workspace:
    root: Path
    config: ApexConfig

    @classmethod
    def discover(cls, cwd: Path | None = None) -> "Workspace":
        cwd = (cwd or Path.cwd()).resolve()
        config, path = ApexConfig.discover(cwd)
        return cls(root=path.parent if path else cwd, config=config)

    @property
    def apex_dir(self) -> Path:
        return self.root / ".apex"

    def _fn_dir(self, base: str, info: FunctionInfo) -> Path:
        return self.apex_dir / base / info.module / info.qualname

    def model_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("lean", info) / "Model.lean"

    def spec_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("lean", info) / "Spec.lean"

    def proofs_dir(self, info: FunctionInfo) -> Path:
        return self._fn_dir("proofs", info)

    def intent_path(self, info: FunctionInfo) -> Path:
        """The last fused Intent Model. Rewritten on every run; kept for stable property IDs."""
        return self._fn_dir("intent", info) / "intent.json"

    def proposals_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("intent", info) / "proposals.json"

    def inputs_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("intent", info) / "inputs.json"

    def provenance_path(self, info: FunctionInfo) -> Path:
        """Who proposed each proof in the spec, keyed by a hash of the proof text."""
        return self._fn_dir("intent", info) / "provenance.json"

    def proof_provenance(self, info: FunctionInfo) -> dict[str, dict[str, str]]:
        path = self.provenance_path(info)
        return json.loads(path.read_text()) if path.is_file() else {}

    def record_proofs(self, info: FunctionInfo, by: str, proofs: dict[str, str]) -> None:
        data = self.proof_provenance(info)
        for pid, proof in proofs.items():
            data[pid] = {"proof_by": by, "proof_sha256": _proof_sha(proof)}
        path = self.provenance_path(info)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def tla_dir(self, info: FunctionInfo) -> Path:
        """The function's TLA+ bundle (Spec.tla, Spec.cfg, harness.py) and its last check."""
        return self._fn_dir("tla", info)

    def tla_result(self, info: FunctionInfo) -> "TlaCheckResult | None":
        path = self.tla_dir(info) / "evidence.json"
        return TlaCheckResult.model_validate_json(path.read_text()) if path.is_file() else None

    def fuzz_path(self, info: FunctionInfo) -> Path:
        """The latest fuzzing campaign per property (`apex fuzz`)."""
        return self._fn_dir("fuzz", info) / "evidence.json"

    def fuzz_evidence(self, info: FunctionInfo) -> dict[str, FuzzEvidence]:
        path = self.fuzz_path(info)
        if not path.is_file():
            return {}
        data = json.loads(path.read_text())
        return {pid: FuzzEvidence.model_validate(e) for pid, e in data.items()}

    def record_fuzz(self, info: FunctionInfo, campaigns: list[FuzzEvidence]) -> None:
        data = self.fuzz_evidence(info) | {e.property_id: e for e in campaigns}
        path = self.fuzz_path(info)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({k: v.model_dump(mode="json") for k, v in sorted(data.items())}, indent=2)
            + "\n"
        )

    def english_specs(self, info: FunctionInfo) -> list[Path]:
        """English specs for a function: `<module>.spec.md` beside it, plus any registered."""
        out = []
        auto = info.path.resolve().with_name(f"{info.path.stem}.spec.md")
        if auto.is_file():
            out.append(auto)
        if self.inputs_path(info).is_file():
            data = json.loads(self.inputs_path(info).read_text())
            out += [(self.root / p).resolve() for p in data.get("english", [])]
        return [p for p in dict.fromkeys(out) if p.is_file()]

    def register_english(self, info: FunctionInfo, path: Path) -> None:
        path = path.resolve()
        if not path.is_file():
            raise ApexToolError(f"no such English spec: {path}")
        data = {"english": []}
        if self.inputs_path(info).is_file():
            data = json.loads(self.inputs_path(info).read_text())
        rel = os.path.relpath(path, self.root)
        if rel not in data["english"]:
            data["english"].append(rel)
        self.inputs_path(info).parent.mkdir(parents=True, exist_ok=True)
        self.inputs_path(info).write_text(json.dumps(data, indent=2) + "\n")

    def stored_proposals(self, info: FunctionInfo) -> ProposalSet | None:
        path = self.proposals_path(info)
        return ProposalSet.model_validate_json(path.read_text()) if path.is_file() else None

    @property
    def review_store(self) -> ReviewStore:
        return ReviewStore(self.root / self.config.review_store)

    @property
    def last_report(self) -> Path:
        return self.apex_dir / "last_report.json"


def function_id(info: FunctionInfo) -> str:
    return f"{info.module}.{info.qualname}"


def _proof_sha(proof: str) -> str:
    return sha256_text(" ".join(proof.split()))


# ---- analyze ---------------------------------------------------------------------------------


class AnalyzeResult(BaseModel):
    intent: IntentModel
    in_scope: bool
    gap: Gap | None = None
    pure_core: str | None = None
    lean_model: str | None = None
    spec_items: list[str] = []
    """English spec items (`S1: ...`), which proposals refer to by ID."""
    needs_formalizing: list[str] = []
    """Properties that are text only: a model or agent must propose a formal hint."""


@dataclass
class _Analysis:
    info: FunctionInfo
    purity: PurityResult
    model: LeanModel | None
    gap: Gap | None
    intent: IntentModel
    examples: dict[str, Example]
    sig: FunctionSig | None
    """The signature in the supported types, even when the body cannot be modelled."""
    spec_items: list[SpecItem] = field(default_factory=list)
    english_files: list[Path] = field(default_factory=list)

    @property
    def params(self) -> list[str]:
        return [n for n, _ in self.sig.params] if self.sig else []

    @property
    def helpers(self) -> set[str]:
        return module_functions(self.info)


def _purity_gap(purity: PurityResult) -> Gap:
    first = purity.findings[0]
    steps = []
    if purity.pure_core:
        steps.append("Verify the extracted pure core instead (the side effects are separable).")
    if first.reason_code is ReasonCode.FLOAT_SEMANTICS:
        steps.append("Use integers (e.g. fixed-point cents) or a float-aware tool such as Gappa.")
    elif first.reason_code is ReasonCode.NEEDS_CONCURRENCY_TOOL:
        steps.append("Model the concurrency protocol in TLA+; verify the pure step function here.")
    else:
        steps.append(
            "Move the computation into a pure function that takes its inputs as "
            "arguments and returns its result, and verify that."
        )
    return Gap(
        reason_code=first.reason_code,
        explanation="The function is not pure: " + "; ".join(purity.reasons) + ".",
        next_steps=steps,
    )


def _translation_gap(e: TranslationError) -> Gap:
    steps = ["Rewrite the construct using the supported subset (see docs/LIMITS.md)."]
    if e.reason_code is ReasonCode.NONTERMINATION:
        steps = ["Use a for loop over a list or range(); recursion is not supported yet."]
    elif e.reason_code is ReasonCode.FLOAT_SEMANTICS:
        steps = ["Use integers (e.g. fixed-point) for exact arithmetic, or a float-aware tool."]
    return Gap(
        reason_code=e.reason_code, explanation=f"Cannot model faithfully: {e}.", next_steps=steps
    )


def _analyze(target: str, ws: "Workspace | None" = None) -> _Analysis:
    """Stage 1: purity, the Lean model, and every intent source fused into one Intent Model.

    With a workspace, registered English specs and stored proposals are included, and the fused
    model is saved so property IDs stay stable across runs.
    """
    info = load_function(target)
    purity = classify(info.node, info.module_tree)
    model, gap = None, None
    js = info.language == "js"
    if purity.class_ != "pure":
        gap = _purity_gap(purity)
    elif js and not info.typed:
        gap = Gap(
            reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
            explanation=f"`{info.qualname}` has untyped parameters or result; Apex needs a type "
            "for each to model it.",
            next_steps=[
                "Add TypeScript types, or JSDoc `@param {number} x` and `@returns {number}`."
            ],
        )
    elif js and not info.exported:
        gap = Gap(
            reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
            explanation=f"`{info.qualname}` is not exported, so Apex cannot run it: refutation, "
            "fuzzing and the fidelity check every proof needs all run the real code.",
            next_steps=[f"Export `{info.qualname}` from {info.path.name}."],
        )
    else:
        try:
            model = translate(info)
        except TranslationError as e:
            gap = _translation_gap(e)
    sig = model.target_sig if model else None
    if sig is None:
        try:
            sig = signature(info.node)
        except TranslationError:
            sig = None

    params = [n for n, _ in sig.params] if sig else [a.arg for a in info.node.args.args]
    helpers = module_functions(info)
    from apex.js.intent import js_hint, mine_js_tests, mine_jsdoc

    if js:
        mined = mine_jsdoc(info) + mine_js_tests(info) + mine_source(info)
    else:
        mined = mine_docstring(info) + mine_tests(info) + mine_source(info)
    items: list[SpecItem] = []
    english = ws.english_specs(info) if ws else []
    for path in english:
        m, its = mine_english(path, info.qualname, params, helpers, convert=js_hint if js else None)
        mined, items = mined + m, items + its
    previous = None
    if ws is not None:
        if (stored := ws.stored_proposals(info)) is not None:
            spec_name = english[0].name if english else None
            mined = mined + Mined([], to_candidates(stored, items, spec_name, info.path.name))
        if ws.intent_path(info).is_file():
            previous = IntentModel.model_validate_json(ws.intent_path(info).read_text())

    intent, examples = fuse(
        info,
        purity,
        sig.params if sig else None,
        sig.returns if sig else None,
        mined,
        previous=previous,
    )
    if ws is not None:
        ws.intent_path(info).parent.mkdir(parents=True, exist_ok=True)
        ws.intent_path(info).write_text(intent.model_dump_json(indent=2, by_alias=True) + "\n")
    return _Analysis(info, purity, model, gap, intent, examples, sig, items, english)


def analyze(
    target: str, ws: "Workspace | None" = None, english: Path | None = None
) -> AnalyzeResult:
    if english is not None:
        if ws is None:
            raise ApexToolError("--english needs a workspace")
        ws.register_english(load_function(target), english)
    a = _analyze(target, ws)
    return AnalyzeResult(
        intent=a.intent,
        in_scope=a.gap is None,
        gap=a.gap,
        pure_core=a.purity.pure_core,
        lean_model=a.model.text if a.model else None,
        spec_items=[it.id + ": " + it.text for it in a.spec_items],
        needs_formalizing=[
            p.id for p in a.intent.properties if p.kind != "example" and not p.formal_hint
        ],
    )


# ---- intent proposals (the LLM step, from either Apex's client or the host agent) -------------


def submit_proposals(
    target: str, ws: "Workspace", proposals: ProposalSet, *, replace: bool = False
) -> ProposalCheck:
    """Check proposed hints against the real signature and store the valid ones."""
    a = _analyze(target, ws)
    result = check_proposals(proposals, a.params, a.helpers, a.spec_items)
    stored = None if replace else ws.stored_proposals(a.info)
    path = ws.proposals_path(a.info)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merge(stored, result.accepted).model_dump_json(indent=2) + "\n")
    _analyze(target, ws)  # refresh the fused intent with the new proposals
    return result


def propose_intent(
    target: str, ws: "Workspace", *, provider: str | None = None, from_source: bool = False
) -> ProposalCheck:
    """Engine mode for stage 1: a model formalizes the English and docstring intent.

    The result goes through exactly the checks `apex intent submit` applies to a host agent.
    """
    from apex.intent.propose import propose
    from apex.llm.providers import ProviderError, get_provider

    a = _analyze(target, ws)
    try:
        llm = get_provider(ws.config, provider)
        proposals = propose(llm, a.info, a.intent, a.spec_items, from_source=from_source)
    except ProviderError as e:
        raise ApexToolError(str(e)) from e
    return submit_proposals(target, ws, proposals)


# ---- scaffold --------------------------------------------------------------------------------


def model_file(model: LeanModel) -> str:
    return (
        f"{FILE_HEADER}{OPTIONS}\n-- Generated by Apex {__version__} from the Python source. "
        f"Do not edit: it is regenerated on every run.\n\nnamespace {model.namespace}\n\n"
        f"{model.text}\nend {model.namespace}\n"
    )


def _check_model(model: LeanModel, project: LeanProject) -> Gap | None:
    run = run_lean(model_file(model), project, timeout_s=300)
    errors = [m for m, _ in run.messages if m.severity == "error"]
    if run.code is None:
        return Gap(
            reason_code=ReasonCode.BUDGET_EXHAUSTED,
            explanation="The Lean model did not compile within 300s.",
            next_steps=["Simplify the function or split it into smaller ones."],
        )
    if not errors:
        return None
    text = errors[0].message
    if re.search(r"termination|decreasing|structural recursion", text, re.I):
        return Gap(
            reason_code=ReasonCode.NONTERMINATION,
            explanation=f"Lean could not show the recursion terminates: {text.splitlines()[0]}",
            next_steps=[
                "Recurse on a strictly smaller argument (e.g. n - 1 with a "
                "base case for n <= 0), or use a for loop."
            ],
        )
    return Gap(
        reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
        explanation=f"The generated Lean model does not compile: {text.splitlines()[0]}",
        next_steps=["Report this as an Apex translator bug with the function source."],
    )


def _example_theorem(ex: Example, pid: str, model: LeanModel) -> str | None:
    sig = model.target_sig
    try:
        args = " ".join(to_lean(a, t) for a, (_, t) in zip(ex.args, sig.params, strict=True))
        expected = to_lean(ex.expected, sig.returns)
    except LiteralError:
        return None
    return (
        f"/-- {pid}: {ex.text}  [{ex.source.type} {ex.source.loc}] -/\n"
        f"theorem {pid} : {model.def_names[-1]} {args} = {expected} := by\n  sorry\n"
    )


def _drafts(a: _Analysis, floor: float, only: set[str] | None = None) -> list[tuple[str, str]]:
    """(id, text) for every property: a drafted theorem, or a comment saying why there is none."""
    assert a.model is not None
    conflicted = {pid: c for c in a.intent.conflicts for pid in c.property_ids}
    out = []
    for prop in a.intent.properties:
        if only is not None and prop.id not in only:
            continue
        if prop.id in conflicted:
            out.append((prop.id, f"-- {prop.id} conflicts: {conflicted[prop.id].description}\n"))
            continue
        if prop.kind == "example":
            ex = a.examples.get(prop.id)
            thm = _example_theorem(ex, prop.id, a.model) if ex else None
            out.append((prop.id, thm or f"-- TODO {prop.id}: {prop.text}\n"))
            continue
        why = None
        if not prop.formal_hint:
            why = "no formal hint yet (`apex intent propose` or `apex intent submit`)"
        elif prop.confidence < floor:
            why = f"confidence {prop.confidence} is below the floor {floor}"
        else:
            try:
                out.append((prop.id, draft_property(prop, a.intent, a.model, a.helpers).text))
                continue
            except DraftError as e:
                why = str(e)
        hint = f"\n--   hint: {prop.formal_hint}" if prop.formal_hint else ""
        out.append((prop.id, f"-- TODO {prop.id}: {prop.text}{hint}\n--   ({why})\n"))
    return out


def spec_skeleton(a: _Analysis, floor: float) -> str:
    info = a.info
    out = [
        f"/-\nApex spec for {info.path}::{info.qualname}",
        "Model: Model.lean in this folder (generated; read it, do not edit it).",
        "",
        "Each property is a theorem named after its ID (P1, P2, ...; E1, ... are examples).",
        "Statements drafted from formal hints are only drafts: they need human approval in a",
        "terminal (`apex review`) before they count as `proven`. Proofs may be anything Lean",
        "accepts except sorry/admit/native_decide/axioms. Only imports and theorem/lemma",
        "declarations are allowed in this file.",
    ]
    if a.intent.preconditions:
        out += ["", "Preconditions (assumed by every drafted statement):"]
        out += [
            f"  {c.id}: {c.formal_hint or c.text}"
            + ("" if c.formal_hint else "  (no hint: not assumed)")
            for c in a.intent.preconditions
        ]
    out.append("-/\n")
    out += [text for _, text in _drafts(a, floor)]
    if not a.intent.properties:
        out.append("-- TODO P1: state the first property of the function here.\n")
    return "\n".join(out)


class ScaffoldResult(BaseModel):
    model_path: str | None = None
    spec_path: str | None = None
    spec_written: bool = False
    """False when a spec already existed; Apex never rewrites one, it only appends."""
    appended: list[str] = []
    """Drafted theorems appended to an existing spec (properties it did not declare yet)."""
    model_compiles: bool = False
    gap: Gap | None = None


def scaffold(target: str, ws: Workspace, project: LeanProject | None = None) -> ScaffoldResult:
    a = _analyze(target, ws)
    if a.model is None:
        return ScaffoldResult(gap=a.gap)
    project = project or LeanProject.locate()
    gap = _check_model(a.model, project)
    model_path, spec_path = ws.model_path(a.info), ws.spec_path(a.info)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(model_file(a.model))
    floor = ws.config.confidence_floor
    written, appended = False, []
    if not spec_path.exists():
        spec_path.write_text(spec_skeleton(a, floor))
        written = True
    else:
        declared = {d.name for d in load_spec(spec_path).decls}
        new = [
            (pid, text)
            for pid, text in _drafts(a, floor, {p.id for p in a.intent.properties} - declared)
            if text.startswith("/--")
        ]
        if new:
            with spec_path.open("a") as f:
                f.write("\n" + "\n".join(text for _, text in new))
            appended = [pid for pid, _ in new]
    return ScaffoldResult(
        model_path=str(model_path),
        spec_path=str(spec_path),
        spec_written=written,
        appended=appended,
        model_compiles=gap is None,
        gap=gap,
    )


# ---- verify ----------------------------------------------------------------------------------


def verdict_file(model: LeanModel, spec: Spec, decl: SpecDecl | None) -> str:
    imports = "".join(f"import {m}\n" for m in spec.imports)
    helpers = "".join(h.text + "\n" for h in spec.helpers)
    target = decl.text if decl else ""
    return (
        f"{FILE_HEADER}{imports}{OPTIONS}\nnamespace {model.namespace}\n\n{model.text}\n"
        f"{helpers}{target}\nend {model.namespace}\n"
    )


MAX_LEAN_ERRORS = 5


def _lean_errors(
    messages: list[LeanMessage],
    model: LeanModel,
    spec: Spec,
    decl: SpecDecl,
    spec_path: Path,
    lean_file: str | None,
) -> list[LeanError]:
    """Map errors in a verdict file back to the spec file the author edits, where possible."""
    spec_text = spec_path.read_text() if spec_path.is_file() else ""
    vtext = verdict_file(model, spec, decl)
    blocks: list[tuple[int, int, int]] = []  # (verdict line, number of lines, spec line)
    for d in [*spec.helpers, decl]:
        body = d.text.rstrip("\n")
        v_at, s_at = vtext.find(body), spec_text.find(body)
        if v_at >= 0 and s_at >= 0:
            blocks.append(
                (
                    vtext[:v_at].count("\n") + 1,
                    body.count("\n") + 1,
                    spec_text[:s_at].count("\n") + 1,
                )
            )
    out = []
    for m in messages[:MAX_LEAN_ERRORS]:
        where = next(((v, sl) for v, n, sl in blocks if v <= m.line < v + n and sl), None)
        if where:
            file, ln = str(spec_path), where[1] + (m.line - where[0])
        else:
            file, ln = lean_file or "verdict file", m.line
        out.append(LeanError(file=file, line=ln, column=m.column, message=m.message, goal=m.goal))
    return out


def load_spec(path: Path) -> Spec:
    if not path.is_file():
        raise ApexToolError(f"no spec file at {path}; run `apex scaffold` first")
    try:
        return parse_spec(path.read_text())
    except SpecError as e:
        raise ApexToolError(f"invalid spec {path}: " + "; ".join(e.problems)) from e


_DOC_HINT = re.compile(r"\[hint: (?P<h>.*?); ")


def _same_claim(decl: SpecDecl, prop: Property, a: _Analysis) -> bool:
    """Whether a spec theorem was drafted from this intent property (its doc says which).

    Refutation checks the property's hint on the real Python; the counterexample is only pinned on
    a theorem that states that same hint, never on a hand-written theorem that shares an ID.
    """
    if not decl.doc:
        return False
    if prop.kind == "example":
        ex = a.examples.get(prop.id)
        return ex is not None and f"{prop.id}: {ex.text} " in decl.doc + " "
    m = _DOC_HINT.search(decl.doc)
    if not m or not prop.formal_hint:
        return False
    try:
        return (
            parse_hint(m["h"], a.params, a.helpers).text
            == parse_hint(prop.formal_hint, a.params, a.helpers).text
        )
    except HintError:
        return False


def _provenance(
    decl: SpecDecl, prop: Property | None, proof_by: dict[str, dict[str, str]], a: _Analysis
) -> Provenance:
    """Which model (if any) proposed the hint behind this statement, and the proof. Debug only."""
    statement_by = prop.proposed_by if prop and _same_claim(decl, prop, a) else None
    rec = proof_by.get(decl.name)
    by = rec["proof_by"] if rec and rec.get("proof_sha256") == _proof_sha(decl.proof) else None
    return Provenance(statement_by=statement_by, proof_by=by if decl.has_proof else None)


def _conflict_result(pid: str, c: Conflict, base: dict) -> PropertyResult:
    return PropertyResult(
        **base,
        verdict=Verdict.CONTRADICTION,
        gap=Gap(
            reason_code=ReasonCode.SPEC_CONFLICT,
            explanation=c.description,
            next_steps=["Decide which source is right and fix the other."],
        ),
    )


def _undeclared_result(prop: Property, floor: float, spec_path: Path) -> PropertyResult:
    if not prop.formal_hint:
        return PropertyResult(
            id=prop.id,
            verdict=Verdict.UNPROVEN,
            gap=Gap(
                reason_code=ReasonCode.SPEC_AMBIGUOUS,
                explanation=f"Not machine-checkable yet: {prop.text}",
                next_steps=[
                    "Add a formal hint in backticks to the spec item, or run `apex intent "
                    "propose` (model) / `apex intent submit` (agent) to formalize it.",
                ],
            ),
        )
    if prop.confidence < floor:
        return PropertyResult(
            id=prop.id,
            verdict=Verdict.UNPROVEN,
            gap=Gap(
                reason_code=ReasonCode.SPEC_AMBIGUOUS,
                explanation=f"Confidence {prop.confidence} is below the floor {floor}: "
                f"{prop.text} (`{prop.formal_hint}`)",
                next_steps=["Confirm the intent in a spec, docstring or test to raise it."],
            ),
        )
    return PropertyResult(
        id=prop.id,
        verdict=Verdict.UNPROVEN,
        gap=Gap(
            reason_code=ReasonCode.NO_STATEMENT,
            explanation=f"No Lean statement yet for: {prop.text}",
            next_steps=[f"Run `apex scaffold` to append the drafted theorem to {spec_path.name}."],
        ),
    )


_SIDE_EFFECTS = {ReasonCode.IMPURE_IO, ReasonCode.NEEDS_CONCURRENCY_TOOL}


def safe_to_run(purity: PurityResult) -> bool:
    """Whether refutation and fuzzing may call the real code thousands of times: no I/O, global
    state or concurrency. Float arithmetic or an unsupported construct (a generator, a `while`
    loop) keeps a function out of Lean's reach but makes it no less safe to run."""
    return not any(f.reason_code in _SIDE_EFFECTS for f in purity.findings)


def _apply_fuzz(results: dict[str, PropertyResult], a: _Analysis, ws: Workspace) -> None:
    """Recorded campaigns, where still current (same function, same hint): a clean one turns a
    property Lean could not decide into `fuzzed`; a counterexample, replayed again here, refutes."""
    evidence = ws.fuzz_evidence(a.info)
    if not evidence or a.sig is None or not safe_to_run(a.purity):
        return
    hints = {p.id: p.formal_hint for p in a.intent.properties}
    for pid, p in list(results.items()):
        e = evidence.get(pid)
        if e is None or e.function_sha256 != a.info.sha256 or e.hint != hints.get(pid):
            continue
        if e.found is not None:
            if p.verdict in FAILURE_VERDICTS:
                continue
            cx = replay_fuzz(a.info, a.intent, a.sig.params, e)
            if cx is not None:
                results[pid] = PropertyResult(
                    id=p.id,
                    verdict=Verdict.REFUTED,
                    lean_statement=p.lean_statement,
                    review=p.review,
                    counterexample=cx,
                    provenance=p.provenance,
                    fuzz=e,
                )
        elif e.executions > 0 and p.verdict in (Verdict.UNPROVEN, Verdict.OUT_OF_SCOPE):
            results[pid] = p.model_copy(update={"verdict": Verdict.FUZZED, "fuzz": e})


def _tla_results(info: FunctionInfo, ws: Workspace) -> list[PropertyResult]:
    """One property per invariant of the function's TLA+ bundle, from its last `apex tla check`.

    Precedence: the invariant observed breaking in a real run (`refuted`) > a recorded trace the
    spec does not allow (`fidelity_failed`) > TLC could not check (`unproven`) > a TLC violation
    no run reproduced (`unproven`) > `model_checked`. Stale evidence (the code or any bundle file
    changed) counts as not checked.
    """
    if not ws.tla_dir(info).is_dir():
        return []
    try:
        bundle = load_bundle(ws.tla_dir(info))
    except BundleError:
        return []
    fid = function_id(info)
    last = ws.tla_result(info)
    fresh = (
        last is not None
        and last.spec_sha256 == bundle.sha256
        and all(r.evidence.function_sha256 == info.sha256 for r in last.invariants.values())
    )
    target = f"{info.path}::{info.qualname}"
    out = []
    for pid in bundle.property_ids:
        review = ws.review_store.status(fid, info.sha256, pid, bundle.sha256)
        r = last.invariants.get(pid) if fresh and last else None
        if r is None:
            why = "changed since the last check" if last is not None else "not checked yet"
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.UNPROVEN,
                    review=review,
                    gap=Gap(
                        reason_code=ReasonCode.NEEDS_CONCURRENCY_TOOL,
                        explanation=f"The TLA+ bundle or the code is {why}.",
                        next_steps=[f"Run `apex tla check {target}`."],
                    ),
                )
            )
            continue
        e = r.evidence
        if r.observed is not None:
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.REFUTED,
                    review=review,
                    counterexample=r.observed,
                    model_check=e,
                )
            )
        elif e.rejected_trace is not None:
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.FIDELITY_FAILED,
                    review=review,
                    model_check=e,
                    gap=Gap(
                        reason_code=ReasonCode.NEEDS_CONCURRENCY_TOOL,
                        explanation="A recorded run of the real code is not a behavior of the "
                        "TLA+ spec, so nothing TLC says about the spec applies to this code.",
                        next_steps=[
                            "Compare the rejected trace with the spec: fix the spec (or the "
                            "harness's observe()) if the code is right, the code if not."
                        ],
                    ),
                )
            )
        elif r.error is not None:
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.UNPROVEN,
                    review=review,
                    model_check=e,
                    gap=Gap(
                        reason_code=ReasonCode.MODEL_CHECK_FAILED,
                        explanation=r.error,
                        next_steps=["Fix the bundle and run `apex tla check` again."],
                    ),
                )
            )
        elif e.tlc_trace is not None:
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.UNPROVEN,
                    review=review,
                    model_check=e,
                    gap=Gap(
                        reason_code=ReasonCode.SPEC_VIOLATION_NOT_REPRODUCED,
                        explanation=f"TLC found a {len(e.tlc_trace)}-step behavior of the spec "
                        f"that breaks {e.invariant}; {e.runs} runs of the real code never "
                        "showed it.",
                        next_steps=[
                            "Read TLC's trace (in --json): if the code can take it, add a "
                            f"check_{e.invariant} to the harness or run more schedules; if not, "
                            "the spec allows more than the code does."
                        ],
                    ),
                )
            )
        else:
            out.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.MODEL_CHECKED,
                    review=review,
                    model_check=e,
                    gap=Gap(
                        reason_code=ReasonCode.NEEDS_CONCURRENCY_TOOL,
                        explanation="Checked within the bounds in Spec.cfg, against a spec the "
                        "recorded runs conform to: evidence, not a proof about the code.",
                        next_steps=[]
                        if review.status == "approved"
                        else [f"Review the bundle and run `apex review {target} --approve {pid}`."],
                    ),
                )
            )
    return out


def tla_check_target(
    target: str, ws: Workspace, *, runs: int = 200, run_timeout_s: float = 10.0
) -> TlaCheckResult:
    """TLC on the function's TLA+ bundle, then its real runs against the spec; recorded for
    `verify`."""
    info = load_function(target)
    directory = ws.tla_dir(info)
    try:
        bundle = load_bundle(directory)
        result = check_bundle(
            bundle,
            target,
            Path(info.path),
            info.sha256,
            runs=runs,
            run_timeout_s=run_timeout_s,
        )
    except (BundleError, HarnessError, TlaUnavailable) as e:
        raise ApexToolError(str(e)) from e
    (directory / "evidence.json").write_text(result.model_dump_json(indent=2))
    return result


def tla_draft_target(
    target: str, ws: Workspace, *, provider: str | None = None, force: bool = False
) -> Path:
    """A model drafts the function's TLA+ bundle (pending review)."""
    from apex.llm.providers import ProviderError, get_provider
    from apex.tla.draft import DraftError, draft_bundle

    info = load_function(target)
    a = _analyze(target, ws)
    intent = [f"- {p.text}" for p in a.intent.properties]
    try:
        llm = get_provider(ws.config, provider)
        return draft_bundle(llm, info, intent, ws.tla_dir(info), force=force)
    except (ProviderError, DraftError, TlaUnavailable) as e:
        raise ApexToolError(str(e)) from e


def _apply_refutation(results: dict[str, PropertyResult], refuted: RefuteResult) -> None:
    for r in refuted.properties:
        if r.refuted and r.id in results and r.counterexample is not None:
            p = results[r.id]
            results[r.id] = PropertyResult(
                id=p.id,
                verdict=Verdict.REFUTED,
                lean_statement=p.lean_statement,
                review=p.review,
                counterexample=r.counterexample,
                provenance=p.provenance,
            )


def _verify_function(
    a: _Analysis, ws: Workspace, spec_path: Path, project: LeanProject, fidelity_n: int
) -> FunctionResult:
    info = a.info
    fid = function_id(info)
    result = FunctionResult(
        qualname=fid,
        file=str(info.path),
        sha256=info.sha256,
        line=info.lineno,
        language="js" if info.language == "js" else "python",
        purity=a.purity.class_,
        pure_core=a.purity.pure_core,
    )
    spec = load_spec(spec_path) if spec_path.is_file() else Spec()
    declared = {d.name: d for d in spec.properties}
    props = {p.id: p for p in a.intent.properties}
    wanted = list(declared) + [pid for pid in props if pid not in declared]
    conflicted = {pid: c for c in a.intent.conflicts for pid in c.property_ids}
    floor = ws.config.confidence_floor
    if not wanted:
        result.gap = a.gap or NO_INTENT_GAP
        return result
    refutable = [
        pid
        for pid in wanted
        if pid in props
        and pid not in conflicted
        and (pid not in declared or _same_claim(declared[pid], props[pid], a))
    ]

    def run_refutation(crosshair_ids: set[str]) -> RefuteResult | None:
        if not safe_to_run(a.purity) or a.sig is None or not refutable:
            return None
        return refute(
            info,
            a.intent,
            a.sig.params,
            a.examples,
            ids=refutable,
            crosshair_ids=crosshair_ids,
            budget_s=ws.config.refute_budget_s,
        )

    if a.gap is None and a.model is not None:
        a.gap = _check_model(a.model, project)
    if a.gap is not None or a.model is None:
        result.gap = a.gap
        oos: dict[str, PropertyResult] = {}
        for pid in wanted:
            oos[pid] = (
                _conflict_result(pid, conflicted[pid], {"id": pid})
                if pid in conflicted
                else PropertyResult(id=pid, verdict=Verdict.OUT_OF_SCOPE, gap=a.gap)
            )
        # No model to prove things about, but the real Python can still be refuted or fuzzed.
        if refuted := run_refutation(set(refutable)):
            _apply_refutation(oos, refuted)
        _apply_fuzz(oos, a, ws)
        result.properties = [oos[pid] for pid in wanted]
        return result

    model = a.model
    store = ws.review_store
    proof_by = ws.proof_provenance(info)
    compiled: dict[str, PropertyResult] = {}
    for decl in spec.properties:
        review = store.status(fid, info.sha256, decl.name, decl.statement_sha256)
        base = {
            "id": decl.name,
            "lean_statement": decl.full_statement,
            "review": review,
            "provenance": _provenance(decl, props.get(decl.name), proof_by, a),
        }
        if decl.name in conflicted:
            compiled[decl.name] = _conflict_result(decl.name, conflicted[decl.name], base)
            continue
        if not decl.has_proof:
            compiled[decl.name] = PropertyResult(
                **base,
                verdict=Verdict.UNPROVEN,
                gap=Gap(
                    reason_code=ReasonCode.BUDGET_EXHAUSTED,
                    explanation="No proof has been found or submitted yet.",
                    next_steps=[
                        "Run `apex prove --tactics-only`, or write a "
                        "proof in the spec and run `apex verify`."
                    ],
                ),
            )
            continue
        res = compile_and_audit(
            verdict_file(model, spec, decl),
            [f"{model.namespace}.{decl.name}"],
            project=project,
            timeout_s=ws.config.proof_budget_s,
            keep_dir=ws.proofs_dir(info) / decl.name,
        )
        if res.status == "tool_error":
            raise ApexToolError(res.tool_error or "Lean failed")
        check = res.theorems[0]
        if check.outcome is TheoremOutcome.PROVED:
            assert check.audit is not None and res.lean_file is not None
            proof = ProofArtifact(
                path=str(Path(res.lean_file).relative_to(ws.root)),
                sha256=res.source_sha256,
                axioms=check.audit.axioms,
            )
            verdict = Verdict.PROVEN if review.status == "approved" else Verdict.PROVEN_UNREVIEWED
            compiled[decl.name] = PropertyResult(**base, verdict=verdict, proof=proof)
        else:
            timeout = check.outcome is TheoremOutcome.TIMEOUT
            first = next((m for m in res.errors), None)
            detail = check.reasons[0] if check.reasons else "rejected"
            steps = ["Read the Lean error and goal state below, fix the proof, re-run verify."]
            if first and first.goal:
                steps.append(f"Goal: {first.goal}")
            compiled[decl.name] = PropertyResult(
                **base,
                verdict=Verdict.UNPROVEN,
                gap=Gap(
                    reason_code=(
                        ReasonCode.BUDGET_EXHAUSTED if timeout else ReasonCode.PROOF_REJECTED
                    ),
                    explanation=detail,
                    next_steps=steps,
                ),
                lean_errors=_lean_errors(res.errors, model, spec, decl, spec_path, res.lean_file),
            )

    # Stage 6: a proof only stands if the model it is about matches the real Python.
    if any(p.proof for p in compiled.values()):
        try:
            pres = formal_preconditions(
                a.intent.preconditions,
                [n for n, _ in model.target_sig.params],
                module_functions(info),
            )
            result.fidelity = check_fidelity(
                info, model, project=project, n=fidelity_n, preconditions=pres
            )
        except FidelityError as e:
            raise ApexToolError(f"fidelity check could not run: {e}") from e
        if result.fidelity.agree and info.language == "js" and "number" in info.numeric:
            # JS `number` is a double: the Lean model computes with exact integers, so a proof
            # transfers only while every intermediate value is a safe integer.
            for pid, p in compiled.items():
                if p.proof is not None:
                    compiled[pid] = p.model_copy(
                        update={
                            "verdict": Verdict.PROVEN_CONDITIONAL,
                            "added_preconditions": [SAFE_INTEGER_PRECONDITION],
                            "gap": Gap(
                                reason_code=ReasonCode.FLOAT_SEMANTICS,
                                explanation="Proven for JavaScript `number` arithmetic while "
                                "every intermediate value is a safe integer.",
                                next_steps=[
                                    "Use `bigint` for exact integer arithmetic, and the proof "
                                    "is unconditional."
                                ],
                            ),
                        }
                    )
        if not result.fidelity.agree:
            m = result.fidelity.mismatch
            for pid, p in compiled.items():
                if p.proof is not None:
                    compiled[pid] = p.model_copy(
                        update={
                            "verdict": Verdict.FIDELITY_FAILED,
                            "proof": None,
                            "counterexample": m,
                            "gap": FIDELITY_GAP,
                        }
                    )

    for pid in wanted:
        if pid in compiled:
            continue
        if pid in conflicted:
            compiled[pid] = _conflict_result(pid, conflicted[pid], {"id": pid})
        else:
            compiled[pid] = _undeclared_result(props[pid], floor, spec_path)

    # Refutation runs on the real Python. The cheap searches check every property, proven ones
    # included; CrossHair's budget goes to the ones Lean has not settled.
    settled = {pid for pid, p in compiled.items() if p.proof is not None}
    if refuted := run_refutation(set(refutable) - settled):
        _apply_refutation(compiled, refuted)
    _apply_fuzz(compiled, a, ws)
    result.properties = [compiled[pid] for pid in wanted]
    return result


def verify(
    targets: list[str],
    ws: Workspace,
    *,
    spec_paths: dict[str, Path] | None = None,
    project: LeanProject | None = None,
    fidelity_n: int = 200,
    write_report: bool = True,
    mode: str = "host",
    provider: str | None = None,
    deadline: float | None = None,
) -> Report:
    project = project or LeanProject.locate()
    functions = []
    for target in targets:
        _check_deadline(deadline)
        a = _analyze(target, ws)
        spec_path = (spec_paths or {}).get(target) or ws.spec_path(a.info)
        fn = _verify_function(a, ws, spec_path, project, fidelity_n)
        if tla := _tla_results(a.info, ws):
            seen = {p.id for p in fn.properties}
            fn.properties += [p for p in tla if p.id not in seen]
        functions.append(fn)
    report = Report(
        apex_version=__version__,
        mode=mode,  # type: ignore[arg-type]
        provider=provider,
        lean_toolchain=project.toolchain,
        mathlib_rev=project.mathlib_rev,
        functions=functions,
        exit_code=int(exit_code_for(run_verdicts(functions), ws.config.fail_on)),
    )
    if write_report:
        ws.last_report.parent.mkdir(parents=True, exist_ok=True)
        ws.last_report.write_text(report.model_dump_json(indent=2))
    return report


class RunTimeout(ApexToolError):
    """The whole run exceeded its time budget (exit code 3)."""


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise RunTimeout("the run exceeded its time budget (--timeout)")


# ---- prove --tactics-only --------------------------------------------------------------------


TACTICS_PROVENANCE = "apex:tactics"


class ProveResult(BaseModel):
    spec_path: str
    closed: dict[str, str]
    """Property -> proof written into the spec (tactic tiers, or a model in engine mode)."""
    open: list[str]
    duration_s: float
    proposed_by: str | None = None
    """Engine mode: the provider whose proofs were tried."""
    attempts: dict[str, int] = {}
    """Engine mode: model proposals tried per property."""
    errors: dict[str, str] = {}
    """Engine mode: why each still-open property's last attempt failed."""
    skipped: dict[str, str] = {}
    """Engine mode: properties no model was asked to prove, and why (already refuted)."""


def prove_tactics(
    target: str, ws: Workspace, *, spec_path: Path | None = None, project: LeanProject | None = None
) -> ProveResult:
    a = _analyze(target, ws)
    if a.model is None:
        raise ApexToolError(f"{target} is out of scope: {a.gap.explanation if a.gap else ''}")
    project = project or LeanProject.locate()
    spec_path = spec_path or ws.spec_path(a.info)
    spec = load_spec(spec_path)
    targets = [d for d in spec.properties if not d.has_proof]
    if not targets:
        return ProveResult(spec_path=str(spec_path), closed={}, open=[], duration_s=0.0)
    header = FILE_HEADER + "".join(f"import {m}\n" for m in spec.imports) + OPTIONS
    res = probe(
        header,
        a.model.text,
        a.model.namespace,
        spec,
        a.model.def_names,
        targets,
        project,
        timeout_s=max(ws.config.proof_budget_s * len(targets), 120),
    )
    text = spec_path.read_text()
    for decl in targets:
        if proof := res.closed.get(decl.name):
            text = text.replace(decl.text.rstrip("\n"), render_decl(decl, proof).rstrip("\n"), 1)
    spec_path.write_text(text)
    ws.record_proofs(a.info, TACTICS_PROVENANCE, res.closed)
    return ProveResult(
        spec_path=str(spec_path),
        closed=res.closed,
        open=[d.name for d in targets if d.name not in res.closed],
        duration_s=round(res.duration_s, 2),
    )


# ---- refute ----------------------------------------------------------------------------------


def refute_target(target: str, ws: Workspace, ids: list[str] | None = None) -> RefuteResult:
    a = _analyze(target, ws)
    if not safe_to_run(a.purity):
        raise ApexToolError(f"{target} has side effects; refutation would run them")
    if a.sig is None:
        raise ApexToolError(f"{target}: parameter or return types are outside the MVP subset")
    return refute(
        a.info,
        a.intent,
        a.sig.params,
        a.examples,
        ids=ids,
        budget_s=ws.config.refute_budget_s,
    )


def fuzz_target(
    target: str,
    ws: Workspace,
    *,
    ids: list[str] | None = None,
    budget_s: float | None = None,
    engine: Engine | None = None,
    max_executions: int | None = None,
) -> FuzzResult:
    """Run a fuzzing campaign per property and record it; `verify` reads the record."""
    a = _analyze(target, ws)
    if not safe_to_run(a.purity):
        raise ApexToolError(f"{target} has side effects; fuzzing would run them")
    if a.sig is None:
        raise ApexToolError(f"{target}: parameter or return types are outside the MVP subset")
    try:
        result = fuzz(
            a.info,
            a.intent,
            a.sig.params,
            ids=ids,
            budget_s=budget_s if budget_s is not None else ws.config.fuzz_budget_s,
            engine=engine or ws.config.fuzz_engine,
            max_executions=max_executions,
        )
    except FuzzError as e:
        raise ApexToolError(str(e)) from e
    ws.record_fuzz(a.info, result.properties)
    return result


# ---- prove (engine mode) ---------------------------------------------------------------------


def prove_llm(
    target: str,
    ws: Workspace,
    llm: "Provider",
    *,
    spec_path: Path | None = None,
    project: LeanProject | None = None,
    deadline: float | None = None,
) -> ProveResult:
    """A model proposes proofs for the spec's open theorems; Lean's errors go back to it.

    Proofs that pass the fast check are written into the spec, so the verdict still comes from
    `verify`'s cold compile and axiom audit, exactly as for a proof a person wrote.
    """
    from apex.prove.llm import ProofContext, ProofSearch, search_proof, splice
    from apex.prove.tactics import tiers

    a = _analyze(target, ws)
    if a.model is None:
        raise ApexToolError(f"{target} is out of scope: {a.gap.explanation if a.gap else ''}")
    project = project or LeanProject.locate()
    spec_path = spec_path or ws.spec_path(a.info)
    spec = load_spec(spec_path)
    started = time.monotonic()
    refuted = _quick_refuted(a, spec, ws)
    targets = [d for d in spec.properties if not d.has_proof and d.name not in refuted]
    if not targets:
        return ProveResult(
            spec_path=str(spec_path),
            closed={},
            open=sorted(refuted),
            duration_s=round(time.monotonic() - started, 2),
            proposed_by=llm.name,
            skipped={pid: "refuted on the real Python" for pid in sorted(refuted)},
        )
    text = spec_path.read_text()
    ctx = ProofContext(
        namespace=a.model.namespace,
        model=a.model.text,
        spec_text=text,
        spec=spec,
        header=FILE_HEADER + "".join(f"import {m}\n" for m in spec.imports) + OPTIONS,
        prelude=(project.root / "Apex" / "Prelude.lean").read_text(),
        project=project,
        timeout_s=ws.config.proof_budget_s,
    )

    def_names = a.model.def_names

    def run(decl: SpecDecl) -> "ProofSearch":
        tried = replace(ctx, tried_tactics=tiers(def_names, decl))
        return search_proof(llm, tried, decl, attempts=ws.config.retries + 1, deadline=deadline)

    with ThreadPoolExecutor(max_workers=max(1, ws.config.llm_parallel)) as pool:
        searches = list(pool.map(run, targets))

    closed: dict[str, str] = {}
    errors: dict[str, str] = {}
    for search in searches:
        current = parse_spec(text)
        decl = current.get(search.name)
        if search.closed is None or decl is None:
            errors[search.name] = search.last_error or "no proof found"
            continue
        try:
            text = splice(text, current, decl, search.closed)
        except ValueError as e:  # cannot happen after a passing fast check, but never guess
            errors[search.name] = str(e)
            continue
        closed[search.name] = search.closed.proof
    spec_path.write_text(text)
    final = {d.name: d.proof for d in parse_spec(text).decls}
    ws.record_proofs(a.info, llm.name, {n: final[n] for n in closed})
    return ProveResult(
        spec_path=str(spec_path),
        closed=closed,
        open=[d.name for d in targets if d.name not in closed] + sorted(refuted),
        duration_s=round(time.monotonic() - started, 2),
        proposed_by=llm.name,
        attempts={s.name: len(s.attempts) for s in searches},
        errors=errors,
        skipped={pid: "refuted on the real Python" for pid in sorted(refuted)},
    )


def _quick_refuted(a: _Analysis, spec: Spec, ws: Workspace) -> set[str]:
    """Spec theorems whose claim the cheap searches (examples, Hypothesis) already break.

    No model time is spent proving a statement the real Python violates; `verify` still runs the
    full refutation and gives the verdict.
    """
    if a.purity.class_ != "pure" or a.sig is None:
        return set()
    props = {p.id: p for p in a.intent.properties}
    conflicted = {pid for c in a.intent.conflicts for pid in c.property_ids}
    ids = [
        d.name
        for d in spec.properties
        if not d.has_proof
        and d.name in props
        and d.name not in conflicted
        and _same_claim(d, props[d.name], a)
    ]
    if not ids:
        return set()
    res = refute(
        a.info,
        a.intent,
        a.sig.params,
        a.examples,
        ids=ids,
        crosshair_ids=set(),
        budget_s=ws.config.refute_budget_s,
    )
    return {r.id for r in res.properties if r.refuted}


# ---- check (engine mode: the full pipeline over files and packages) ---------------------------

_SKIP_DIRS = {".git", ".apex", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
_SOURCE_SUFFIXES = {".py", ".ts", ".mts", ".cts", ".js", ".mjs", ".cjs"}


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or ".test." in name  # clamp.test.ts (Jest, Vitest, node:test)
        or ".spec." in name  # clamp.spec.ts; English specs are .spec.md and not code anyway
        or "__tests__" in path.parts
    )


def discover_targets(
    paths: list[str], ws: Workspace, *, include_private: bool = False
) -> list[str]:
    """`path::fn` targets for every function to check.

    Directories are walked for Python and JavaScript / TypeScript files, skipping test files,
    type declarations (`.d.ts`), the config's `ignore` globs and tool directories. Private
    functions (`_name`) are skipped unless asked for: they are checked as part of the public
    functions that call them.
    """
    from apex.analyze.target import TargetError, top_level_functions

    out: list[str] = []
    for raw in paths:
        if "::" in raw:
            out.append(raw)
            continue
        path = Path(raw)
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(
                f
                for f in path.rglob("*")
                if f.suffix in _SOURCE_SUFFIXES
                and not f.name.endswith(".d.ts")
                and not _SKIP_DIRS & set(f.relative_to(path).parts[:-1])
                and not _is_test_file(f)
                and not _ignored(f, ws)
            )
        else:
            raise ApexToolError(f"no such file or directory: {raw}")
        for f in files:
            try:
                names = top_level_functions(f)
            except TargetError as e:
                raise ApexToolError(str(e)) from e
            out += [f"{f}::{n}" for n in names if include_private or not n.startswith("_")]
    return list(dict.fromkeys(out))


def _ignored(path: Path, ws: Workspace) -> bool:
    try:
        rel = path.resolve().relative_to(ws.root).as_posix()
    except ValueError:
        return False
    return any(fnmatch.fnmatch(rel, pat) for pat in ws.config.ignore)


class CheckStep(BaseModel):
    target: str
    step: str
    detail: str


@dataclass
class CheckRun:
    report: Report
    steps: list[CheckStep]


def check(
    paths: list[str],
    ws: Workspace,
    *,
    provider: str | None = None,
    use_llm: bool = True,
    include_private: bool = False,
    timeout_s: float | None = None,
    project: LeanProject | None = None,
    fidelity_n: int = 200,
    on_step: Callable[[CheckStep], None] | None = None,
    fuzz_s: float | None = None,
) -> CheckRun:
    """Engine mode: analyze, formalize, scaffold, prove (tactics, then a model) and verify.

    Without a model provider the LLM steps are skipped and every deterministic step still runs,
    so `apex check` is useful (and honest) with `model_provider = "none"`. With `fuzz_s`, every
    property Lean leaves open (all of them, for an out-of-scope function) gets a fuzzing
    campaign of that many seconds before the verdicts are computed.
    """
    from apex.llm.providers import ProviderError, get_provider

    deadline = time.monotonic() + timeout_s if timeout_s else None
    targets = discover_targets(paths, ws, include_private=include_private)
    if not targets:
        raise ApexToolError("no functions to check under " + ", ".join(paths))
    llm = None
    if use_llm and (provider or ws.config.model_provider not in ("none", "host")):
        try:
            llm = get_provider(ws.config, provider)
        except ProviderError as e:
            raise ApexToolError(str(e)) from e
    project = project or LeanProject.locate()
    project.lean_command()  # fail fast (exit 3) before any work if Lean is missing
    steps: list[CheckStep] = []

    def note(target: str, step: str, detail: str) -> None:
        s = CheckStep(target=target, step=step, detail=detail)
        steps.append(s)
        if on_step:
            on_step(s)

    def maybe_fuzz(target: str, a: _Analysis, open_ids: list[str] | None) -> None:
        if not fuzz_s or not safe_to_run(a.purity) or a.sig is None or open_ids == []:
            return
        _check_deadline(deadline)
        try:
            res = fuzz_target(target, ws, ids=open_ids, budget_s=fuzz_s)
        except ApexToolError as e:
            note(target, "fuzz", f"skipped: {e}")
            return
        found = [e.property_id for e in res.properties if e.found]
        clean = [e.property_id for e in res.properties if not e.found and e.executions]
        note(
            target,
            "fuzz",
            f"{res.engine}: counterexample for {', '.join(found) or 'none'}; "
            f"clean {', '.join(clean) or 'none'}",
        )

    for target in targets:
        _check_deadline(deadline)
        a = _analyze(target, ws)
        needs_hints = any(p.kind != "example" and not p.formal_hint for p in a.intent.properties)
        if llm is not None and needs_hints and a.purity.class_ == "pure" and a.sig is not None:
            try:
                proposed = propose_intent(target, ws, provider=provider)
            except ApexToolError as e:
                note(target, "formalize", f"skipped: {e}")
            else:
                acc = proposed.accepted
                note(
                    target,
                    "formalize",
                    f"{llm.name}: accepted {len(acc.properties)} properties and "
                    f"{len(acc.preconditions)} preconditions, rejected "
                    f"{len(proposed.rejected)}",
                )
            a = _analyze(target, ws)
        if a.model is None:
            note(target, "analyze", f"out of scope ({a.gap.reason_code if a.gap else '?'})")
            maybe_fuzz(target, a, None)
            continue
        _check_deadline(deadline)
        sc = scaffold(target, ws, project)
        if sc.gap is not None:
            note(target, "scaffold", f"{sc.gap.reason_code}: {sc.gap.explanation}")
            maybe_fuzz(target, a, None)
            continue
        _check_deadline(deadline)
        tac = prove_tactics(target, ws, project=project)
        if tac.closed or tac.open:
            note(
                target,
                "tactics",
                f"closed {', '.join(tac.closed) or 'none'}; open {', '.join(tac.open) or 'none'}",
            )
        still_open = list(tac.open)
        if llm is not None and tac.open:
            _check_deadline(deadline)
            res = prove_llm(target, ws, llm, project=project, deadline=deadline)
            still_open = list(res.open)
            note(
                target,
                "llm-prove",
                f"{llm.name} closed {', '.join(res.closed) or 'none'}; "
                f"open {', '.join(still_open) or 'none'}",
            )
        maybe_fuzz(target, a, still_open)
    report = verify(
        targets,
        ws,
        project=project,
        fidelity_n=fidelity_n,
        mode="engine",
        provider=llm.name if llm else None,
        deadline=deadline,
    )
    return CheckRun(report, steps)


# ---- review status (read-only; approval is CLI + TTY only) -----------------------------------


@dataclass
class Reviewable:
    """A statement a person can approve: a Lean theorem, or a TLA+ invariant with its bundle."""

    id: str
    kind: Literal["lean", "tla"]
    english: str | None
    statement: str
    """The exact text approval is recorded for."""
    shown: str
    """What the reviewer reads (for TLA+: the invariant, its bounds and the bundle's files)."""
    statement_sha256: str


def reviewables(
    info: FunctionInfo, ws: Workspace, spec_path: Path | None = None
) -> list[Reviewable]:
    spec_path = spec_path or ws.spec_path(info)
    out = []
    if spec_path.is_file():
        for d in load_spec(spec_path).properties:
            out.append(
                Reviewable(
                    d.name, "lean", d.doc, d.full_statement, d.full_statement, d.statement_sha256
                )
            )
    if ws.tla_dir(info).is_dir():
        try:
            b = load_bundle(ws.tla_dir(info))
        except BundleError as e:
            raise ApexToolError(str(e)) from e
        rel = os.path.relpath(b.dir, ws.root)
        for inv, pid in zip(b.invariants, b.property_ids, strict=True):
            out.append(
                Reviewable(
                    pid,
                    "tla",
                    f"TLA+ invariant {inv}; approving it approves the whole bundle in {rel}",
                    b.statement(inv),
                    _tla_summary(b, inv, rel),
                    b.sha256,
                )
            )
    return out


def _tla_summary(b: Bundle, inv: str, rel: str) -> str:
    lines = b.spec.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^{inv}\s*==", ln)), None)
    definition = []
    if start is not None:
        definition.append(lines[start])
        for ln in lines[start + 1 :]:
            if not ln.startswith((" ", "\t")) or not ln.strip():
                break
            definition.append(ln)
    bounds = ", ".join(f"{k} = {v}" for k, v in b.constants.items()) or "none"
    return (
        "\n".join(definition or [f"{inv} (definition not found in Spec.tla)"])
        + f"\n  bounds: {bounds}\n  read all of {rel}/Spec.tla, Spec.cfg and harness.py "
        f"(bundle sha256 {b.sha256[:12]})"
    )


def review_status(target: str, ws: Workspace, spec_path: Path | None = None) -> "ReviewStatus":
    from apex.core.outputs import ReviewEntry, ReviewStatus

    info = load_function(target)
    spec_path = spec_path or ws.spec_path(info)
    fid = function_id(info)
    entries = []
    for r in reviewables(info, ws, spec_path):
        st = ws.review_store.status(fid, info.sha256, r.id, r.statement_sha256)
        entries.append(
            ReviewEntry(
                id=r.id,
                kind=r.kind,
                english=r.english,
                lean=r.statement if r.kind == "lean" else None,
                tla=r.shown if r.kind == "tla" else None,
                status=st.status,
                reviewer=st.reviewer,
            )
        )
    return ReviewStatus(target=target, spec_path=str(spec_path), statements=entries)
