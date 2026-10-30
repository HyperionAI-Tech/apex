"""Refutation: search the real Python for an input that breaks a property.

Runs alongside proving, and needs no Lean model, so it also covers functions the translator cannot
model. Three searches, cheapest first, per property with a formal hint:

1. examples: run the function on each mined example and compare;
2. Hypothesis: generated inputs filtered by the preconditions and premises;
3. CrossHair: symbolic execution on the real Python (in a subprocess, under a time budget).

Whatever finds an input, Apex replays it in plain Python with the same evaluator before reporting
it: a counterexample is only ever an input on which this process watched the real function break
the property. That is why `refuted` may come from outside Lean, and why it can never mean `proven`.

The domain is total correctness on the preconditions: an exception raised for an input that
satisfies every precondition and premise counts as breaking the property. The one exception is
running out of resources. An input on which the Python hits its recursion limit (stack depth,
which Lean does not model either) or runs past the per-call time budget is skipped with a note,
never reported: Apex cannot tell slow from non-terminating, and a counterexample must be an input
it watched break the property. After a few slow calls a search stops calling the function.
"""

import ast
import contextlib
import copy
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.errors import Flaky, Unsatisfiable
from pydantic import BaseModel, Field

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.intent import IntentModel
from apex.core.report import Counterexample
from apex.fidelity.bridge import (
    CALL_TIMEOUT_S,
    MAX_SLOW,
    RECURSION_LIMIT,
    SUPPRESSED_HEALTH_CHECKS,
    TIMED_OUT,
    CallTimeout,
    FidelityError,
    call_user,
    load_module,
    strategy_for,
    unbounded_int_digits,
)
from apex.intent.candidates import Example
from apex.intent.fuse import module_functions
from apex.intent.hints import Hint, HintError, HintEvalError, evaluate, parse_condition, parse_hint
from apex.translate.literals import normalize

HYPOTHESIS_EXAMPLES = 300
# Mostly small integers, where boundary bugs live, and some large ones. Unbounded integers would
# let a correct but loop-heavy function (a `while` up to n) stall the search.
_CROSSHAIR_LINE = re.compile(
    r":\d+: error: (?P<err>.*?) when calling (?P<call>apex_check_\w+\(.*\))"
)


class PropertyRefutation(BaseModel):
    id: str
    refuted: bool
    counterexample: Counterexample | None = None
    searched_by: list[str] = Field(default_factory=list)
    note: str | None = None


class RefuteResult(BaseModel):
    target: str
    properties: list[PropertyRefutation] = Field(default_factory=list)


@dataclass
class _Check:
    pid: str
    hint: Hint
    pres: list[ast.expr]
    searched: list[str] = field(default_factory=list)
    found: Counterexample | None = None
    note: str | None = None
    slow: int = 0
    max_slow: int = MAX_SLOW
    """Slow calls after which this check stops calling the function (a campaign sets it high)."""
    calls: int = 0
    """Calls into the function that passed the preconditions."""
    skipped: int = 0
    """Calls left out at a resource limit (recursion depth, time)."""


def _call(fn: Any, args: tuple[Any, ...]) -> tuple[Any, str | None]:
    try:
        return call_user(fn, copy.deepcopy(args)), None
    except CallTimeout:
        return None, TIMED_OUT
    except RecursionError:
        return None, RECURSION_LIMIT
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _violation(
    c: _Check,
    fn: Any,
    names: list[str],
    args: tuple[Any, ...],
    ns: dict[str, Any],
    by: Literal["crosshair", "hypothesis", "example", "fuzz"],
) -> Counterexample | None:
    """Replay `args` on the real function: a counterexample if (and only if) it breaks `c`."""
    env = dict(zip(names, args, strict=True))
    if c.slow >= c.max_slow:
        return None
    try:
        if not all(evaluate(p, env, ns) for p in c.pres):
            return None
        if not all(evaluate(p, env, ns) for p in c.hint.premises if not _mentions(p, c.hint)):
            return None
    except HintEvalError:
        return None
    value, error = _call(fn, args)
    c.calls += 1
    inputs = {k: repr(v) for k, v in env.items()}
    if error in (RECURSION_LIMIT, TIMED_OUT):
        c.slow += error == TIMED_OUT
        c.skipped += 1
        c.note = _skip_note(error, inputs)
        return None
    if error is not None:
        return Counterexample(
            inputs=inputs, expected=c.hint.text, actual=f"raises {error}", found_by=by
        )
    env[c.hint.result] = value
    try:
        if all(evaluate(p, env, ns) for p in c.hint.premises) and not evaluate(
            c.hint.conclusion, env, ns
        ):
            return Counterexample(
                inputs=inputs, expected=c.hint.text, actual=f"returns {value!r}", found_by=by
            )
    except HintEvalError:
        return None
    return None


def _skip_note(error: str, inputs: dict[str, str]) -> str:
    shown = ", ".join(f"{k}={v}" for k, v in inputs.items())
    if error == TIMED_OUT:
        return f"skipped inputs on which the function ran past {CALL_TIMEOUT_S}s (e.g. {shown})"
    return f"skipped inputs that exceed Python's recursion limit (e.g. {shown})"


def _mentions(e: ast.expr, h: Hint) -> bool:
    return any(isinstance(n, ast.Name) and n.id == h.result for n in ast.walk(e))


def _example_check(pid: str, ex: Example, fn: Any, names: list[str]) -> PropertyRefutation:
    value, error = _call(fn, ex.args)
    inputs = {k: repr(v) for k, v in zip(names, ex.args, strict=True)}
    if error is None and normalize(value) == normalize(ex.expected):
        return PropertyRefutation(id=pid, refuted=False, searched_by=["example"])
    if error in (RECURSION_LIMIT, TIMED_OUT):
        return PropertyRefutation(
            id=pid, refuted=False, searched_by=["example"], note=_skip_note(error, inputs)
        )
    return PropertyRefutation(
        id=pid,
        refuted=True,
        searched_by=["example"],
        counterexample=Counterexample(
            inputs=inputs,
            expected=repr(ex.expected),
            actual=f"raises {error}" if error else f"returns {value!r}",
            found_by="example",
        ),
    )


def _hypothesis(
    c: _Check, fn: Any, params: list[tuple[str, PyType]], ns: dict[str, Any], n: int
) -> None:
    names = [p for p, _ in params]
    found: list[Counterexample] = []

    class _Broken(Exception):
        pass

    @settings(
        max_examples=n,
        database=None,
        derandomize=True,
        deadline=None,
        suppress_health_check=SUPPRESSED_HEALTH_CHECKS,
    )
    @given(st.tuples(*(strategy_for(t) for _, t in params)))
    def search(args: tuple[Any, ...]) -> None:
        cx = _violation(c, fn, names, args, ns, "hypothesis")
        if cx is not None:
            found.append(cx)  # Hypothesis replays the shrunk example last
            raise _Broken

    c.searched.append("hypothesis")
    try:
        search()
    except _Broken:
        c.found = found[-1]
    except Unsatisfiable:
        pass
    except Flaky:  # the function itself is nondeterministic: no reproducible counterexample
        c.note = "the function gave different results for the same input; not refuted"


def _crosshair_file(
    checks: list[_Check], info: FunctionInfo, params: list[tuple[str, PyType]], module: str
) -> str:
    sig = ", ".join(f"{n}: {t.python()}" for n, t in params)
    call = f"_apex_target({', '.join(n for n, _ in params)})"
    lines = [
        f"import {module} as _apex_m",
        f"_apex_target = _apex_m.{info.qualname}",
        *(f"{h} = _apex_m.{h}" for h in sorted(module_functions(info))),
        "",
    ]
    for c in checks:
        lines += [f"def apex_check_{c.pid}({sig}) -> None:", "    assert True"]
        lines += [f"    assert {ast.unparse(p)}" for p in c.pres]
        lines += [
            f"    assert {ast.unparse(p)}" for p in c.hint.premises if not _mentions(p, c.hint)
        ]
        lines += [f"    {c.hint.result} = {call}"]
        lines += [f"    assert {ast.unparse(p)}" for p in c.hint.premises if _mentions(p, c.hint)]
        lines += [f"    assert {ast.unparse(c.hint.conclusion)}", ""]
    return "\n".join(lines)


def _crosshair(
    checks: list[_Check],
    info: FunctionInfo,
    params: list[tuple[str, PyType]],
    fn: Any,
    ns: dict[str, Any],
    budget_s: float,
) -> None:
    if not checks:
        return
    names = [p for p, _ in params]
    by_id = {c.pid: c for c in checks}
    module_dir = Path(info.path).resolve().parent
    with tempfile.TemporaryDirectory(prefix="apex-refute-") as tmp:
        name = f"apex_refute_{uuid.uuid4().hex[:8]}"
        (Path(tmp) / f"{name}.py").write_text(_crosshair_file(checks, info, params, info.module))
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(module_dir), tmp])}
        cmd = [
            sys.executable,
            "-m",
            "crosshair",
            "check",
            "--analysis_kind=asserts",
            f"--per_condition_timeout={budget_s:g}",
            name,
        ]
        for c in checks:
            c.searched.append("crosshair")
        try:
            out = subprocess.run(
                cmd,
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=budget_s * len(checks) + 60,
            )
        except subprocess.TimeoutExpired:
            for c in checks:
                c.note = "CrossHair ran out of time"
            return
    for line in out.stdout.splitlines():
        m = _CROSSHAIR_LINE.search(line)
        if not m:
            continue
        try:
            call = ast.parse(m["call"], mode="eval").body
            assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            args = tuple(ast.literal_eval(a) for a in call.args)
        except (SyntaxError, ValueError, AssertionError):
            continue
        c = by_id.get(call.func.id.removeprefix("apex_check_"))
        if c is None or c.found is not None or len(args) != len(names):
            continue
        # CrossHair proposes; plain Python decides.
        c.found = _violation(c, fn, names, args, ns, "crosshair")


@dataclass
class CheckTarget:
    """The real function, its module's helpers, and one check per property with a hint."""

    fn: Any
    ns: dict[str, Any]
    checks: list[_Check]
    examples: list[str]
    """Example properties, which are checked by running the one input they state."""
    skipped: dict[str, PropertyRefutation]
    """Properties with nothing to check, each with the reason."""


def load_checks(
    info: FunctionInfo, intent: IntentModel, names: list[str], ids: list[str] | None = None
) -> CheckTarget:
    """Import the module fresh and build a check per property (`ids`, default all). Shared by
    refutation and fuzzing, so both filter by the same preconditions and replay the same way."""
    module = load_module(info)
    fn = getattr(module, info.qualname)
    ns = {h: getattr(module, h) for h in module_functions(info) if hasattr(module, h)}
    helpers = set(ns)
    conflicted = {pid for c in intent.conflicts for pid in c.property_ids}
    pres: list[ast.expr] = []
    for c in intent.preconditions:
        if c.formal_hint:
            with contextlib.suppress(HintError):
                pres.append(parse_condition(c.formal_hint, names, helpers))
    out = CheckTarget(fn=fn, ns=ns, checks=[], examples=[], skipped={})
    for p in intent.properties:
        if ids is not None and p.id not in ids:
            continue
        if p.id in conflicted:
            out.skipped[p.id] = PropertyRefutation(
                id=p.id, refuted=False, note="intent sources conflict; not checked"
            )
        elif p.kind == "example":
            out.examples.append(p.id)
        elif p.formal_hint:
            try:
                out.checks.append(_Check(p.id, parse_hint(p.formal_hint, names, helpers), pres))
            except HintError as e:
                out.skipped[p.id] = PropertyRefutation(id=p.id, refuted=False, note=str(e))
        else:
            out.skipped[p.id] = PropertyRefutation(
                id=p.id, refuted=False, note="no formal hint to check"
            )
    return out


@unbounded_int_digits()
def refute(
    info: FunctionInfo,
    intent: IntentModel,
    params: list[tuple[str, PyType]],
    examples: dict[str, Example],
    *,
    ids: list[str] | None = None,
    crosshair_ids: set[str] | None = None,
    budget_s: float = 10.0,
    hypothesis_n: int = HYPOTHESIS_EXAMPLES,
) -> RefuteResult:
    """Search for counterexamples to the properties `ids` (default: all with a hint).

    CrossHair runs only for `crosshair_ids` (default: the same set); the cheaper searches run for
    all. Conflicted properties are skipped: the sources disagree, so there is nothing to check yet.
    """
    names = [p for p, _ in params]
    try:
        target = load_checks(info, intent, names, ids)
    except FidelityError as e:
        return RefuteResult(
            target=f"{info.path}::{info.qualname}",
            properties=[
                PropertyRefutation(id=p.id, refuted=False, note=str(e)) for p in intent.properties
            ],
        )
    fn, ns, checks = target.fn, target.ns, target.checks
    results = dict(target.skipped)
    for pid in target.examples:
        results[pid] = (
            _example_check(pid, examples[pid], fn, names)
            if pid in examples
            else PropertyRefutation(id=pid, refuted=False, note="the example's input is unknown")
        )

    for c in checks:
        # Examples double as cheap targeted inputs for every property.
        for ex in examples.values():
            if c.found is None and len(ex.args) == len(names):
                c.found = _violation(c, fn, names, ex.args, ns, "example")
        if c.found is None:
            _hypothesis(c, fn, params, ns, hypothesis_n)
    wanted = crosshair_ids if crosshair_ids is not None else {c.pid for c in checks}
    _crosshair(
        [c for c in checks if c.found is None and c.pid in wanted], info, params, fn, ns, budget_s
    )
    for c in checks:
        results[c.pid] = PropertyRefutation(
            id=c.pid,
            refuted=c.found is not None,
            counterexample=c.found,
            searched_by=c.searched,
            note=c.note,
        )
    order = [p.id for p in intent.properties]
    return RefuteResult(
        target=f"{info.path}::{info.qualname}",
        properties=sorted(results.values(), key=lambda r: order.index(r.id)),
    )
