"""`apex explain` and `apex suggest`: plain English and concrete next steps over a report.

Deterministic: everything here is read off the report (and the stored Intent Model, for the
hint behind a counterexample). Nothing is softened; the verdict is always stated as Apex gave it.
"""

import ast
import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from apex.core.intent import IntentModel
from apex.core.report import FunctionResult, PropertyResult, Report, function_verdict
from apex.core.verdict import ReasonCode, Verdict
from apex.intent.hints import BUILTINS, HintError, parse_hint, result_name

MEANING: dict[Verdict, str] = {
    Verdict.PROVEN: "Lean proved this statement about a model of the function, a person approved "
    "that the statement says what the function should do, and the model agreed with the real "
    "Python on generated inputs.",
    Verdict.PROVEN_UNREVIEWED: "Lean proved this statement, but no person has approved that the "
    "statement says what the function should do, so it does not count as proven yet.",
    Verdict.PROVEN_CONDITIONAL: "Lean proved this statement only under assumptions the source "
    "does not state.",
    Verdict.REFUTED: "A concrete input (or, for concurrent code, a recorded run) makes the real "
    "Python break this property. Apex watched it happen on the real code, so this is an observed "
    "fact, not a guess.",
    Verdict.FIDELITY_FAILED: "The model Apex checked (the Lean model, or the TLA+ spec) and the "
    "real Python disagree, so any result about the model was about the wrong thing and was "
    "discarded.",
    Verdict.CONTRADICTION: "Two sources of intent disagree. Nothing about this property was "
    "checked against the code until a person decides which source is right.",
    Verdict.UNPROVEN: "Apex found neither a proof nor a counterexample. This is a gap, not a "
    "pass and not a failure.",
    Verdict.OUT_OF_SCOPE: "Apex cannot model this function faithfully, so it makes no claim "
    "about it either way.",
    Verdict.FUZZED: "Lean could not decide this property, so Apex fuzzed the real Python: a "
    "recorded campaign found no counterexample. That is evidence, not proof; inputs it never "
    "tried can still break the property.",
    Verdict.MODEL_CHECKED: "Lean cannot model this concurrent code, so Apex checked a TLA+ spec of "
    "it: TLC explored every state within the configured bounds without a violation, and recorded "
    "runs of the real code matched the spec. That is evidence about a bounded abstraction, not "
    "proof: larger bounds or schedules the runs never hit can still break it.",
}


class Advice(BaseModel):
    function: str
    target: str
    """`path.py::name`, ready to paste into the next command."""
    property: str | None = None
    verdict: Verdict
    meaning: str
    explanation: str
    suggestions: list[str] = Field(default_factory=list)
    snippet: str | None = None
    """A regression test that replays the counterexample, for refuted properties."""


class AdviceList(BaseModel):
    schema_version: Literal["1"] = "1"
    kind: Literal["explain", "suggest"]
    items: list[Advice] = Field(default_factory=list)


def target_of(f: FunctionResult, root: Path) -> str:
    path = Path(f.file)
    if path.is_absolute():
        with contextlib.suppress(ValueError):  # another drive on Windows
            path = Path(os.path.relpath(path, root))
    return f"{path.as_posix()}::{f.qualname.split('.', 1)[-1]}"


def _narrative(f: FunctionResult, p: PropertyResult) -> str:
    c = p.counterexample
    if c is None:
        return ""
    name = f.qualname.split(".", 1)[-1]
    if c.found_by == "stress":
        return (
            f"In run {c.inputs.get('run', '?')} of the harness, the real {name} {c.actual}, "
            f"which breaks the TLA+ invariant {c.expected}."
        )
    args = ", ".join(f"{k}={v}" for k, v in c.inputs.items())
    if p.verdict is Verdict.FIDELITY_FAILED:
        return f"On {name}({args}) the {c.expected}, but the {c.actual}."
    if p.id.startswith("E"):
        return f"{name}({args}) {c.actual}, but the example expects {c.expected}."
    return f"{name}({args}) {c.actual}, which breaks `{c.expected}` (found by {c.found_by})."


def regression_test(f: FunctionResult, p: PropertyResult, params: list[str] | None) -> str | None:
    """A pytest test that fails exactly while the counterexample still breaks the property."""
    c = p.counterexample
    if c is None or p.verdict is not Verdict.REFUTED or c.found_by == "stress":
        return None  # a stress run is an interleaving, not an input a test can replay
    module, name = f.qualname.split(".", 1)
    names = params if params is not None else list(c.inputs)
    if set(names) != set(c.inputs):
        names = list(c.inputs)
    res = result_name(names)
    imports = {name}
    if p.id.startswith("E"):
        check = f"assert {res} == {c.expected}"
    else:
        try:
            hint = parse_hint(c.expected or "", names, _called_names(c.expected or ""))
        except HintError:
            return None
        parts = [*(f"not ({ast.unparse(e)})" for e in hint.premises), ast.unparse(hint.conclusion)]
        check = (
            "assert " + " or ".join(f"({x})" for x in parts)
            if hint.premises
            else (f"assert {ast.unparse(hint.conclusion)}")
        )
        imports |= _called_names(c.expected or "") - BUILTINS
    lines = [
        f"from {module} import {', '.join(sorted(imports))}",
        "",
        "",
        f"def test_{name}_{p.id}_apex_counterexample() -> None:",
        f"    # Found by {c.found_by}; property {p.id}: {c.expected}",
        *(f"    {k} = {c.inputs[k]}" for k in names),
        f"    {res} = {name}({', '.join(names)})",
        f"    {check}",
    ]
    return "\n".join(lines) + "\n"


def _called_names(text: str) -> set[str]:
    out = set()
    for part in text.replace("->", "\n").splitlines():
        try:
            tree = ast.parse(part.strip(), mode="eval")
        except SyntaxError:
            continue
        out |= {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
    return out


def _suggestions(f: FunctionResult, p: PropertyResult | None, target: str) -> list[str]:
    if p is None:
        assert f.gap is not None
        return list(f.gap.next_steps)
    v = p.verdict
    if v is Verdict.PROVEN:
        return []
    if v is Verdict.PROVEN_UNREVIEWED:
        return [
            "Read the Lean statement and check that it says what the function should do.",
            "If it does, approve it in your own terminal: "
            f"`apex review {target} --approve {p.id}`.",
            "If it does not, fix the intent (spec, docstring or test) rather than the statement.",
        ]
    if v is Verdict.REFUTED:
        return [
            "If the property is the intent, fix the code: the test below replays the input.",
            "If the code is right and the property is wrong, fix the spec, docstring or test it "
            "came from; do not weaken the theorem.",
        ]
    if v is Verdict.FIDELITY_FAILED:
        return [
            "If Python raises on this input, guard it (return None) or state a precondition.",
            "Otherwise this is an Apex translator bug: report the function and the input.",
        ]
    steps = list(p.gap.next_steps) if p.gap else []
    if p.gap and p.gap.reason_code is ReasonCode.BUDGET_EXHAUSTED:
        steps.append(
            f"Let a model try: `apex prove {target}` with model_provider set in apex.toml, or "
            "write the proof in the spec and run `apex verify`."
        )
    return steps


def advise(
    report: Report,
    root: Path,
    kind: Literal["explain", "suggest"],
    *,
    only: str | None = None,
    properties: list[str] | None = None,
    intent_for: Callable[[FunctionResult], IntentModel | None] | None = None,
) -> AdviceList:
    """One entry per property (or per function judged as a whole), filtered by target / IDs.

    `explain` covers every verdict; `suggest` only those with something to do.
    """
    out = AdviceList(kind=kind)
    for f in report.functions:
        target = target_of(f, root)
        if only and only not in (target, f.qualname, f.qualname.split(".", 1)[-1]):
            continue
        intent = intent_for(f) if intent_for else None
        params = [n for n, _ in intent.signature.params] if intent else None
        whole = function_verdict(f)
        if whole is not None and f.gap is not None and not properties:
            out.items.append(
                Advice(
                    function=f.qualname,
                    target=target,
                    verdict=whole,
                    meaning=MEANING[whole],
                    explanation=f"{f.gap.reason_code}: {f.gap.explanation}",
                    suggestions=_suggestions(f, None, target),
                )
            )
        for p in f.properties:
            if properties and p.id not in properties:
                continue
            if kind == "suggest" and p.verdict is Verdict.PROVEN:
                continue
            explanation = _narrative(f, p) or (
                f"{p.gap.reason_code}: {p.gap.explanation}" if p.gap else ""
            )
            if p.lean_statement and p.verdict is not Verdict.REFUTED:
                explanation = (explanation + f"\nStatement: {p.lean_statement}").strip()
            out.items.append(
                Advice(
                    function=f.qualname,
                    target=target,
                    property=p.id,
                    verdict=p.verdict,
                    meaning=MEANING[p.verdict],
                    explanation=explanation,
                    suggestions=_suggestions(f, p, target) if kind == "suggest" else [],
                    snippet=regression_test(f, p, params) if kind == "suggest" else None,
                )
            )
    return out
