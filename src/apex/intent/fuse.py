"""Fusion: every intent source's candidates -> one Intent Model, with conflicts called out.

- The same claim from several sources becomes one property carrying all of them. Hints are
  compared in canonical form (spacing, `&&`/`||`, `result`/`__return__` normalized); claims that
  are equivalent but written differently stay separate, which costs a duplicate, never a wrong
  merge.
- A model's formalization of an English item fills in that item's hint; the item keeps its text
  and its location, so the human reviews the sentence they wrote next to the Lean it became.
- Two examples with the same arguments and different results conflict.
- An example conflicts with a property when, evaluated on the example's arguments and result
  (and the preconditions hold), the property's hint is false: the sources disagree, so the verdict
  is `contradiction` and neither is checked against the code until a person decides.
- IDs are stable across runs: a property keeps its ID as long as its hint (or, before it has one,
  its text) is unchanged, so approvals and Spec.lean theorem names keep pointing at the same claim.
"""

import ast
import contextlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal

from apex.analyze.purity import PurityResult
from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.intent import (
    Conflict,
    FunctionRef,
    IntentModel,
    Precondition,
    Property,
    Purity,
    Signature,
    Source,
)
from apex.intent.candidates import Candidate, Example, Mined
from apex.intent.english import TEXT_ONLY_CONFIDENCE
from apex.intent.hints import (
    HintError,
    HintEvalError,
    evaluate,
    holds,
    parse_condition,
    parse_hint,
    result_name,
)


def module_functions(info: FunctionInfo) -> set[str]:
    return {n.name for n in info.module_tree.body if isinstance(n, ast.FunctionDef)}


def fits(value: Any, ty: PyType) -> bool:
    match ty.kind:
        case "int":
            return isinstance(value, int) and not isinstance(value, bool)
        case "bool":
            return isinstance(value, bool)
        case "list":
            return isinstance(value, list) and all(fits(v, ty.args[0]) for v in value)
        case "tuple":
            return (
                isinstance(value, tuple)
                and len(value) == len(ty.args)
                and all(fits(v, t) for v, t in zip(value, ty.args, strict=True))
            )
        case "option":
            return value is None or fits(value, ty.args[0])
    return False


def _norm_text(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".")


def _combine(confidences: list[float]) -> float:
    miss = 1.0
    for c in confidences:
        miss *= 1.0 - c
    return round(1.0 - miss, 3)


class _Group:
    prop_kind: Literal["postcondition", "invariant", "equivalence", "termination", "other"]

    def __init__(self, c: Candidate, hint: str | None) -> None:
        self.kind = c.kind
        self.text = c.text
        self.hint = hint
        self.prop_kind = c.prop_kind
        self.sources: list[Source] = [c.source]
        self.confidences = [c.confidence]
        self.proposed_by = c.proposed_by

    def add(self, c: Candidate) -> None:
        if c.source not in self.sources:
            self.sources.append(c.source)
            self.confidences.append(c.confidence)
        self.proposed_by = self.proposed_by or c.proposed_by

    @property
    def key(self) -> str:
        return f"hint:{self.hint}" if self.hint else f"text:{_norm_text(self.text)}"


def _canonical(c: Candidate, params: list[str], helpers: set[str]) -> tuple[str | None, str]:
    """(canonical hint or None, text) with an unusable hint dropped and explained."""
    if not c.hint:
        return None, c.text
    try:
        if c.kind == "precondition":
            return ast.unparse(parse_condition(c.hint, params, helpers)), c.text
        return parse_hint(c.hint, params, helpers).text, c.text
    except HintError as e:
        return None, f"{c.text} (hint `{c.hint}` rejected: {e})"


def _group(cands: list[Candidate], params: list[str], helpers: set[str]) -> list[_Group]:
    # 1. A formalization fills in the hint of the English item it points at.
    by_item: dict[str, Candidate] = {}
    rest: list[Candidate] = []
    for c in cands:
        if c.item and c.proposed_by is None:
            by_item[c.item] = c
    for c in cands:
        if c.item and c.proposed_by is not None and c.item in by_item:
            target = by_item[c.item]
            if target.hint is None:
                # The formalization also decides what the sentence is: "lo must not exceed hi"
                # is a requirement on the inputs even without a "Requires:" prefix.
                target.kind = c.kind
                target.hint = c.hint
                target.confidence = c.confidence
                target.proposed_by = c.proposed_by
                continue
            c.source = target.source  # a second formalization of the same sentence
        if not (c.item and c.proposed_by is None):
            rest.append(c)
    ordered = [*by_item.values(), *rest]

    # 2. Merge identical claims.
    groups: dict[tuple[str, str], _Group] = {}
    for c in ordered:
        hint, text = _canonical(c, params, helpers)
        if hint is None and c.hint:
            c = replace(c, text=text, hint=None, confidence=min(c.confidence, TEXT_ONLY_CONFIDENCE))
        g = _Group(c, hint)
        if (c.kind, g.key) in groups:
            groups[c.kind, g.key].add(c)
        else:
            groups[c.kind, g.key] = g
    return list(groups.values())


def _ids(previous: IntentModel | None, params: list[str], helpers: set[str]) -> dict[str, str]:
    """Stable IDs: previous property/precondition keys -> their IDs."""
    out: dict[str, str] = {}
    if previous is None:
        return out
    for p in [*previous.properties, *previous.preconditions]:
        if isinstance(p, Property) and p.kind == "example":
            continue
        kind = "precondition" if isinstance(p, Precondition) else "property"
        out[f"{kind}:text:{_norm_text(p.text)}"] = p.id
        if p.formal_hint:
            c = Candidate(kind=kind, text=p.text, hint=p.formal_hint, source=Source(type="spec"))
            hint, _ = _canonical(c, params, helpers)
            if hint:
                out[f"{kind}:hint:{hint}"] = p.id
    return out


def _assign(groups: list[_Group], prefix: str, kind: str, prev: dict[str, str]) -> list[str]:
    used = {v for v in prev.values() if v.startswith(prefix)}
    taken: set[str] = set()
    ids: list[str | None] = []
    for g in groups:
        pid = prev.get(f"{kind}:{g.key}") or prev.get(f"{kind}:text:{_norm_text(g.text)}")
        if pid in taken:
            pid = None
        if pid:
            taken.add(pid)
        ids.append(pid)
    k, out = 1, []
    for pid in ids:
        if pid is None:
            while f"{prefix}{k}" in used | taken:
                k += 1
            pid = f"{prefix}{k}"
            taken.add(pid)
        out.append(pid)
    return out


def fuse(
    info: FunctionInfo,
    purity: PurityResult,
    params: list[tuple[str, PyType]] | None,
    returns: PyType | None,
    mined: Mined,
    *,
    previous: IntentModel | None = None,
    load_helpers: Callable[[], dict[str, Any]] | None = None,
) -> tuple[IntentModel, dict[str, Example]]:
    names = [n for n, _ in params] if params else [a.arg for a in info.node.args.args]
    helpers = module_functions(info)

    examples = mined.examples
    if params is not None and returns is not None:
        examples = [
            e
            for e in examples
            if len(e.args) == len(params)
            and all(fits(a, t) for a, (_, t) in zip(e.args, params, strict=True))
            and fits(e.expected, returns)
        ]

    prev = _ids(previous, names, helpers)
    groups = _group(mined.candidates, names, helpers)
    pre_groups = [g for g in groups if g.kind == "precondition"]
    prop_groups = [g for g in groups if g.kind == "property"]
    preconditions = [
        Precondition(
            id=cid,
            text=g.text,
            formal_hint=g.hint,
            sources=g.sources,
            proposed_by=g.proposed_by,
        )
        for cid, g in zip(_assign(pre_groups, "C", "precondition", prev), pre_groups, strict=True)
    ]
    properties = [
        Property(
            id=pid,
            kind=g.prop_kind,
            text=g.text,
            formal_hint=g.hint,
            sources=g.sources,
            confidence=_combine(g.confidences),
            proposed_by=g.proposed_by,
        )
        for pid, g in zip(_assign(prop_groups, "P", "property", prev), prop_groups, strict=True)
    ]

    # Examples: E1..En in source order (docstring first, then tests), as scaffold numbers them.
    conflicts: list[Conflict] = []
    example_by_id: dict[str, Example] = {}
    seen: dict[str, tuple[str, Example]] = {}
    for k, ex in enumerate(examples, start=1):
        eid = f"E{k}"
        example_by_id[eid] = ex
        key = repr(ex.args)
        if key in seen and seen[key][1].expected != ex.expected:
            other_id, other = seen[key]
            conflicts.append(
                Conflict(
                    property_ids=[other_id, eid],
                    description=f"{other.text} contradicts {ex.text}",
                    sources=[other.source, ex.source],
                )
            )
        seen.setdefault(key, (eid, ex))
        properties.append(
            Property(
                id=eid,
                kind="example",
                text=ex.text,
                formal_hint=ex.text,
                sources=[ex.source],
                confidence=1.0,
            )
        )

    conflicts += _example_conflicts(
        properties, preconditions, example_by_id, names, helpers, load_helpers
    )

    intent = IntentModel(
        function=FunctionRef(
            qualname=f"{info.module}.{info.qualname}", file=str(info.path), sha256=info.sha256
        ),
        purity=Purity(**{"class": purity.class_, "reasons": purity.reasons}),
        signature=Signature(
            params=[(n, t.python()) for n, t in params]
            if params
            else [
                (a.arg, ast.unparse(a.annotation) if a.annotation else "?")
                for a in info.node.args.args
            ],
            returns=returns.python()
            if returns
            else (ast.unparse(info.node.returns) if info.node.returns else "?"),
        ),
        properties=properties,
        preconditions=preconditions,
        conflicts=conflicts,
    )
    return intent, example_by_id


def _example_conflicts(
    properties: list[Property],
    preconditions: list[Precondition],
    examples: dict[str, Example],
    names: list[str],
    helpers: set[str],
    load_helpers: Callable[[], dict[str, Any]] | None,
) -> list[Conflict]:
    res = result_name(names)
    pres = []
    for c in preconditions:
        if c.formal_hint:
            with contextlib.suppress(HintError):
                pres.append(parse_condition(c.formal_hint, names, helpers))
    ns: dict[str, Any] | None = None
    out: list[Conflict] = []
    for p in properties:
        if p.kind == "example" or not p.formal_hint:
            continue
        try:
            hint = parse_hint(p.formal_hint, names, helpers)
        except HintError:
            continue
        needs_ns = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in helpers
            for e in (*hint.premises, hint.conclusion, *pres)
            for n in ast.walk(e)
        )
        if needs_ns and ns is None:
            try:
                ns = load_helpers() if load_helpers else {}
            except Exception:
                ns = {}
        for eid, ex in examples.items():
            env = {**dict(zip(names, ex.args, strict=True)), res: ex.expected}
            try:
                if not all(evaluate(c, env, ns) for c in pres):
                    continue
                ok = holds(hint, env, ns)
            except HintEvalError:
                continue
            if ok is False:
                src = p.sources[0] if p.sources else None
                where = f" ({src.type} {src.loc})" if src and src.loc else ""
                out.append(
                    Conflict(
                        property_ids=[p.id, eid],
                        description=f"{p.id}{where} says `{hint.text}`, but {eid} "
                        f"({ex.source.type} {ex.source.loc}) asserts {ex.text}",
                        sources=[*p.sources[:1], ex.source],
                    )
                )
    return out
