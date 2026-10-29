"""Hypothesis bridge: is the Lean model the same function as the real Python?

A proof is only about the Lean model, so before Apex reports it, the model is run (Lean's
compiler, via `#eval`) and the real Python is run on the same generated inputs. Any disagreement
discards the proof as `fidelity_failed`. This is conservative on purpose:

- a Python exception where the Lean model returns a value is a disagreement (the model is total,
  the Python is not, so theorems about the model can overclaim);
- a Python function that mutates its arguments disagrees (the model has no side effects).
"""

import ast
import copy
import importlib.util
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, reject, settings
from hypothesis import strategies as st

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.report import Counterexample, FidelitySummary
from apex.intent.hints import HintEvalError, evaluate
from apex.lean.project import LeanProject
from apex.translate.lean import LeanModel, leading_asserts
from apex.translate.literals import normalize, to_lean

DEFAULT_EXAMPLES = 200
_CHUNK = 50
_MARK = "APEX-FIDELITY"


SUPPRESSED_HEALTH_CHECKS = [
    HealthCheck.too_slow,
    HealthCheck.filter_too_much,
    HealthCheck.data_too_large,
    HealthCheck.large_base_example,
]


class FidelityError(RuntimeError):
    """The bridge itself could not run (not a disagreement). Maps to a tool error."""


def strategy_for(ty: PyType, ints: st.SearchStrategy[int] | None = None) -> st.SearchStrategy[Any]:
    ints = ints if ints is not None else st.integers(min_value=-(2**40), max_value=2**40)
    match ty.kind:
        case "int":
            return ints
        case "bool":
            return st.booleans()
        case "list":
            return st.lists(strategy_for(ty.args[0], ints), max_size=12)
        case "tuple":
            return st.tuples(*(strategy_for(a, ints) for a in ty.args))
        case "option":
            return st.none() | strategy_for(ty.args[0], ints)
    raise FidelityError(f"no strategy for {ty.kind}")


def generate_inputs(
    params: list[PyType], n: int, accept: Callable[[tuple[Any, ...]], bool] | None = None
) -> list[tuple[Any, ...]]:
    """Up to `n` distinct inputs; with `accept`, only inputs it accepts (e.g. preconditions)."""
    collected: list[tuple[Any, ...]] = []

    @settings(
        max_examples=n,
        database=None,
        derandomize=True,
        deadline=None,
        suppress_health_check=SUPPRESSED_HEALTH_CHECKS,
    )
    @given(st.tuples(*(strategy_for(t) for t in params)))
    def collect(args: tuple[Any, ...]) -> None:
        if accept is not None and not accept(args):
            reject()
        collected.append(args)

    collect()
    unique = {repr(a): a for a in collected}  # Hypothesis replays some cases
    return list(unique.values())


def load_python_function(info: FunctionInfo) -> Any:
    return getattr(load_module(info), info.qualname)


def load_module(info: FunctionInfo) -> Any:
    """Import the target's module fresh (never from sys.modules), so edits are always seen."""
    name = f"_apex_fidelity_{info.module}_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, info.path)
    if spec is None or spec.loader is None:
        raise FidelityError(f"cannot import {info.path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(info.path).resolve().parent))
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # the user's module failed at import time
        raise FidelityError(f"importing {info.path} failed: {type(e).__name__}: {e}") from e
    finally:
        sys.path.pop(0)
    return module


@dataclass
class _PyOutcome:
    value: Any = None
    error: str | None = None
    mutated: bool = False


def _run_python(fn: Any, args: tuple[Any, ...]) -> _PyOutcome:
    call_args = copy.deepcopy(args)
    try:
        value = fn(*call_args)
    except RecursionError:
        return _PyOutcome(error="RecursionError")
    except Exception as e:
        return _PyOutcome(error=f"{type(e).__name__}: {e}")
    return _PyOutcome(value=value, mutated=call_args != args)


def build_eval_file(model: LeanModel, inputs: list[tuple[Any, ...]]) -> str:
    sig = model.target_sig
    fn = model.def_names[-1]
    types = [t for _, t in sig.params]
    calls = [
        f"Apex.pyRepr ({fn} {' '.join(to_lean(a, t) for a, t in zip(args, types, strict=True))})"
        for args in inputs
    ]
    evals = []
    for i in range(0, len(calls), _CHUNK):
        chunk = ",\n    ".join(calls[i : i + _CHUNK])
        evals.append(f'#eval IO.println ("{_MARK}\\n" ++ "\\n".intercalate [\n    {chunk}])')
    return (
        f"import Apex.Prelude\nset_option autoImplicit false\n\nnamespace {model.namespace}\n\n"
        f"{model.text}\n" + "\n\n".join(evals) + f"\n\nend {model.namespace}\n"
    )


def run_lean_model(
    model: LeanModel, inputs: list[tuple[Any, ...]], project: LeanProject, timeout_s: float
) -> list[Any]:
    from apex.lean.compiler import run_lean

    run = run_lean(build_eval_file(model, inputs), project, timeout_s)
    if run.code is None:
        raise FidelityError(f"evaluating the Lean model timed out after {timeout_s:.0f}s")
    outputs: list[str] = []
    for msg, raw in run.messages:
        if msg.severity == "error":
            raise FidelityError(f"Lean model failed to evaluate: {msg.message.splitlines()[0]}")
        if raw.startswith(_MARK):
            outputs.extend(raw.rstrip("\n").split("\n")[1:])
    if len(outputs) != len(inputs):
        raise FidelityError(f"expected {len(inputs)} Lean outputs, got {len(outputs)}")
    try:
        return [ast.literal_eval(o) for o in outputs]
    except (ValueError, SyntaxError) as e:
        raise FidelityError(f"unparseable Lean output: {e}") from e


def check_fidelity(
    info: FunctionInfo,
    model: LeanModel,
    *,
    project: LeanProject | None = None,
    n: int = DEFAULT_EXAMPLES,
    timeout_s: float = 300.0,
) -> FidelitySummary:
    project = project or LeanProject.locate()
    sig = model.target_sig
    names = [p for p, _ in sig.params]
    module = load_module(info)
    fn = getattr(module, info.qualname)
    # The model leaves out leading asserts (preconditions), so it is compared only where they
    # hold: where they fail, Python returns nothing a theorem could be wrong about.
    pres = [a.test for a in leading_asserts(info.node)]

    def accept(args: tuple[Any, ...]) -> bool:
        env = dict(zip(names, args, strict=True))
        try:
            return all(evaluate(c, env, vars(module)) for c in pres)
        except HintEvalError:
            return False

    inputs = generate_inputs([t for _, t in sig.params], n, accept if pres else None)
    if not inputs:
        raise FidelityError("no generated input satisfies the function's leading asserts")
    py = [_run_python(fn, args) for args in inputs]
    lean = run_lean_model(model, inputs, project, timeout_s)

    mismatches: list[Counterexample] = []
    for args, p, out in zip(inputs, py, lean, strict=True):
        if p.error is None and not p.mutated and normalize(p.value) == normalize(out):
            continue
        actual = (
            f"raises {p.error}"
            if p.error
            else f"mutates its arguments (returned {p.value!r})"
            if p.mutated
            else repr(p.value)
        )
        mismatches.append(
            Counterexample(
                inputs={k: repr(v) for k, v in zip(names, args, strict=True)},
                expected=f"Lean model returns {out!r}",
                actual=f"Python {actual}",
                found_by="hypothesis",
            )
        )
    domain = " and ".join(ast.unparse(c) for c in pres) or None
    if not mismatches:
        return FidelitySummary(agree=True, inputs_checked=len(inputs), domain=domain)
    # No cross-language shrinking yet: report the smallest disagreement we saw.
    smallest = min(mismatches, key=lambda c: len(repr(c.inputs)))
    return FidelitySummary(
        agree=False, inputs_checked=len(inputs), mismatch=smallest, domain=domain
    )
