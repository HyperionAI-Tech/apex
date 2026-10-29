"""Stage 2, deterministic: draft a Lean theorem statement from a formal hint.

The hint language is a subset of the Python the translator already models, so the statement is a
mechanical rendering of the hint over the Lean model, not a model's guess. It is still only a
draft: a human approves it with `apex review` before it can be `proven`.

    hint   lo <= hi -> lo <= r <= hi
    lean   theorem P1 (x lo hi : Int) (h1 : lo ≤ hi) :
             (lo ≤ clamp x lo hi ∧ clamp x lo hi ≤ hi) := by sorry

A premise `r is not None` on an Optional result unwraps it, so the conclusion can talk about the
value: `∀ v, f args = some v → ...[r := v]`.
"""

import ast
import copy
from dataclasses import dataclass

from apex.core.intent import IntentModel, Precondition, Property
from apex.intent.hints import Hint, HintError, parse_condition, parse_hint
from apex.translate.lean import LeanModel, PropTranslator, TranslationError, lean_ident


class DraftError(ValueError):
    pass


@dataclass
class Draft:
    id: str
    text: str
    """The full theorem with a doc comment and a `sorry` proof, ready for Spec.lean."""


def _binders(params: list[tuple[str, str]]) -> str:
    """Group consecutive parameters of the same type: `(x lo hi : Int)`."""
    groups: list[tuple[list[str], str]] = []
    for name, ty in params:
        if groups and groups[-1][1] == ty:
            groups[-1][0].append(name)
        else:
            groups.append(([name], ty))
    return " ".join(f"({' '.join(names)} : {ty})" for names, ty in groups)


def _is_not_none(e: ast.expr, name: str) -> bool:
    return (
        isinstance(e, ast.Compare)
        and isinstance(e.left, ast.Name)
        and e.left.id == name
        and len(e.ops) == 1
        and isinstance(e.ops[0], ast.IsNot)
        and isinstance(e.comparators[0], ast.Constant)
        and e.comparators[0].value is None
    )


class _Subst(ast.NodeTransformer):
    def __init__(self, name: str, replacement: ast.expr) -> None:
        self.name, self.replacement = name, replacement

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return copy.deepcopy(self.replacement) if node.id == self.name else node


def _bare(text: str) -> str:
    """Drop one pair of parentheses that wraps the whole expression."""
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for i, c in enumerate(text):
        depth += c == "("
        depth -= c == ")"
        if depth == 0 and i < len(text) - 1:
            return text
    return text[1:-1]


def _names(e: ast.expr) -> set[str]:
    return {n.id for n in ast.walk(e) if isinstance(n, ast.Name)}


def _fresh(base: str, taken: set[str]) -> str:
    k = 1
    while f"{base}{k}" in taken:
        k += 1
    taken.add(f"{base}{k}")
    return f"{base}{k}"


def draft_statement(
    pid: str,
    hint: Hint,
    preconditions: list[ast.expr],
    model: LeanModel,
    doc: str,
) -> Draft:
    sig = model.target_sig
    fn = model.def_names[-1]
    py_fn = next(n for n in model.sigs if lean_ident(n) == fn)
    names = [n for n, _ in sig.params]
    env = dict(sig.params)
    taken = set(names) | {hint.result}
    tr = PropTranslator(model.sigs)
    call = ast.Call(
        func=ast.Name(id=py_fn, ctx=ast.Load()),
        args=[ast.Name(id=n, ctx=ast.Load()) for n in names],
        keywords=[],
    )
    call_text = f"({fn}{''.join(' ' + lean_ident(n) for n in names)})"

    premises = list(hint.premises)
    value = ""
    unwrap = sig.returns.kind == "option" and any(_is_not_none(p, hint.result) for p in premises)
    if unwrap:
        premises = [p for p in premises if not _is_not_none(p, hint.result)]
        value = _fresh("v", taken)
        replacement: ast.expr = ast.Name(id=value, ctx=ast.Load())
        env = {**env, value: sig.returns.args[0]}
    else:
        replacement = call

    # Bind only the parameters the claim is about; a precondition comes along only when the
    # claim mentions everything it constrains (`sum_list([]) == 0` needs no `xs`, no `lo <= hi`).
    used = set().union(*(_names(e) for e in [*premises, hint.conclusion]))
    if hint.result in used or unwrap:
        used |= set(names)
    preconditions = [c for c in preconditions if _names(c) & set(names) <= used]
    hyps: list[str] = []
    try:
        for p in [*preconditions, *premises]:
            p = ast.fix_missing_locations(_Subst(hint.result, replacement).visit(copy.deepcopy(p)))
            hyps.append(f"({_fresh('h', taken)} : {_bare(tr.translate(p, env))})")
        concl = _Subst(hint.result, replacement).visit(copy.deepcopy(hint.conclusion))
        conclusion = _bare(tr.translate(ast.fix_missing_locations(concl), env))
    except TranslationError as e:
        raise DraftError(f"{pid}: the hint is outside the Lean subset: {e}") from e

    binders = [_binders([(lean_ident(n), _bare(t.lean())) for n, t in sig.params if n in used])]
    if unwrap:
        v = lean_ident(value)
        binders.append(f"({v} : {sig.returns.args[0].lean()})")
        hyps.append(f"({_fresh('h', taken)} : {call_text} = some {v})")
    head = "".join(f" {b}" for b in [*binders, *hyps] if b)
    doc = " ".join(doc.split()).replace("-/", "- /")
    return Draft(pid, f"/-- {doc} -/\ntheorem {pid}{head} :\n    {conclusion} := by\n  sorry\n")


def formal_preconditions(
    pres: list[Precondition], params: list[str], helpers: set[str]
) -> list[ast.expr]:
    out = []
    for c in pres:
        if c.formal_hint:
            try:
                out.append(parse_condition(c.formal_hint, params, helpers))
            except HintError:
                continue  # fusion already reported it; an unusable precondition is not assumed
    return out


def draft_property(
    prop: Property, intent: IntentModel, model: LeanModel, helpers: set[str]
) -> Draft:
    if not prop.formal_hint:
        raise DraftError(f"{prop.id} has no formal hint")
    params = [n for n, _ in model.target_sig.params]
    try:
        hint = parse_hint(prop.formal_hint, params, helpers)
    except HintError as e:
        raise DraftError(f"{prop.id}: {e}") from e
    pres = formal_preconditions(intent.preconditions, params, helpers)
    srcs = ", ".join(f"{s.type} {s.loc}" if s.loc else s.type for s in prop.sources)
    by = f"; hint by {prop.proposed_by}" if prop.proposed_by else ""
    doc = f"{prop.id}: {prop.text}  [hint: {hint.text}; {srcs}{by}]"
    return draft_statement(prop.id, hint, pres, model, doc)
