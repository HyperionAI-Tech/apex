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
satisfies every precondition and premise counts as breaking the property.
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
from hypothesis.errors import Unsatisfiable
from pydantic import BaseModel, Field

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.intent import IntentModel
from apex.core.report import Counterexample
from apex.fidelity.bridge import (
    SUPPRESSED_HEALTH_CHECKS,
    FidelityError,
    load_module,
    strategy_for,
)
from apex.intent.candidates import Example
from apex.intent.fuse import module_functions
from apex.intent.hints import Hint, HintError, HintEvalError, evaluate, parse_condition, parse_hint
from apex.translate.literals import normalize

HYPOTHESIS_EXAMPLES = 300
# Mostly small integers, where boundary bugs live, and some large ones. Unbounded integers would
# let a correct but loop-heavy function (a `while` up to n) stall the search.
_INTS = st.one_of(
    st.integers(min_value=-16, max_value=16),
    st.integers(min_value=-(2**16), max_value=2**16),
    st.integers(min_value=-(2**40), max_value=2**40),
)
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


def _call(fn: Any, args: tuple[Any, ...]) -> tuple[Any, str | None]:
    try:
        return fn(*copy.deepcopy(args)), None
    except RecursionError:
        return None, "RecursionError"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _violation(
    c: _Check,
    fn: Any,
    names: list[str],
    args: tuple[Any, ...],
    ns: dict[str, Any],
    by: Literal["crosshair", "hypothesis", "example"],
) -> Counterexample | None:
    """Replay `args` on the real function: a counterexample if (and only if) it breaks `c`."""
    env = dict(zip(names, args, strict=True))
    try:
        if not all(evaluate(p, env, ns) for p in c.pres):
            return None
        if not all(evaluate(p, env, ns) for p in c.hint.premises if not _mentions(p, c.hint)):
            return None
    except HintEvalError:
        return None
    value, error = _call(fn, args)
    inputs = {k: repr(v) for k, v in env.items()}
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


def _mentions(e: ast.expr, h: Hint) -> bool:
    return any(isinstance(n, ast.Name) and n.id == h.result for n in ast.walk(e))


def _example_check(pid: str, ex: Example, fn: Any, names: list[str]) -> PropertyRefutation:
    value, error = _call(fn, ex.args)
    inputs = {k: repr(v) for k, v in zip(names, ex.args, strict=True)}
    if error is None and normalize(value) == normalize(ex.expected):
        return PropertyRefutation(id=pid, refuted=False, searched_by=["example"])
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
    @given(st.tuples(*(strategy_for(t, _INTS) for _, t in params)))
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
        module = load_module(info)
    except FidelityError as e:
        return RefuteResult(
            target=f"{info.path}::{info.qualname}",
            properties=[
                PropertyRefutation(id=p.id, refuted=False, note=str(e)) for p in intent.properties
            ],
        )
    fn = getattr(module, info.qualname)
    ns = {h: getattr(module, h) for h in module_functions(info) if hasattr(module, h)}
    helpers = set(ns)
    conflicted = {pid for c in intent.conflicts for pid in c.property_ids}
    pres: list[ast.expr] = []
    for c in intent.preconditions:
        if c.formal_hint:
            with contextlib.suppress(HintError):
                pres.append(parse_condition(c.formal_hint, names, helpers))

    results: dict[str, PropertyRefutation] = {}
    checks: list[_Check] = []
    for p in intent.properties:
        if ids is not None and p.id not in ids:
            continue
        if p.id in conflicted:
            results[p.id] = PropertyRefutation(
                id=p.id, refuted=False, note="intent sources conflict; not checked"
            )
        elif p.kind == "example" and p.id in examples:
            results[p.id] = _example_check(p.id, examples[p.id], fn, names)
        elif p.formal_hint:
            try:
                checks.append(_Check(p.id, parse_hint(p.formal_hint, names, helpers), pres))
            except HintError as e:
                results[p.id] = PropertyRefutation(id=p.id, refuted=False, note=str(e))
        else:
            results[p.id] = PropertyRefutation(
                id=p.id, refuted=False, note="no formal hint to check"
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
