"""Fuzzing campaigns: as many inputs as a time budget allows, against the real Python.

Refutation runs a few hundred inputs on every verify. A campaign runs far more, for as long as it
is given, and records what it did, so that a property Lean could not decide (unproven, or the
function is out of scope) can report `fuzzed`: evidence, never proof. A campaign shares
refutation's checks: the same preconditions filter its inputs, an input past a resource limit is
skipped, and any input that breaks the property is replayed here, in plain Python, by the same
evaluator before it is reported.

Engines:

- `hypothesis` (default, pure Python): batches of inputs from refutation's strategies, a fresh
  seed per batch, until the budget runs out. Failures are shrunk to a small input.
- `atheris` (optional `fuzz` extra): coverage-guided libFuzzer. It runs in a subprocess, because
  libFuzzer exits the process when it finishes; the input it reports is replayed here. It needs a
  libFuzzer-capable clang to install (on macOS, Homebrew LLVM).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from hypothesis import given, seed, settings
from hypothesis import strategies as st
from hypothesis.errors import Flaky, Unsatisfiable
from pydantic import BaseModel, Field

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.intent import IntentModel
from apex.core.report import Counterexample, FuzzEvidence
from apex.fidelity.bridge import (
    SUPPRESSED_HEALTH_CHECKS,
    FidelityError,
    strategy_for,
    unbounded_int_digits,
)
from apex.fidelity.refute import PropertyRefutation, _Check, _violation, load_checks

Engine = Literal["hypothesis", "atheris"]
ENGINES: tuple[Engine, ...] = ("hypothesis", "atheris")
BATCH = 500
"""Inputs per Hypothesis batch; each batch has its own seed."""
_CONFIG_ENV = "APEX_FUZZ_CONFIG"
_RUNS = re.compile(r"Done (\d+) runs in")


class FuzzError(RuntimeError):
    """The campaign could not run (an engine that is not installed, a module that fails to load)."""


class FuzzResult(BaseModel):
    target: str
    engine: Engine
    budget_s: float
    """Per property."""
    properties: list[FuzzEvidence] = Field(default_factory=list)
    skipped: list[PropertyRefutation] = Field(default_factory=list)
    """Properties with nothing to fuzz (no formal hint, conflicting sources, examples), and why."""


def atheris_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("atheris") is not None


@unbounded_int_digits()
def fuzz(
    info: FunctionInfo,
    intent: IntentModel,
    params: list[tuple[str, PyType]],
    *,
    ids: list[str] | None = None,
    budget_s: float = 30.0,
    engine: Engine = "hypothesis",
) -> FuzzResult:
    """One campaign of `budget_s` seconds per property (`ids`, default all with a hint)."""
    if engine == "atheris" and not atheris_available():
        raise FuzzError(
            "atheris is not installed: `uv pip install atheris` with a libFuzzer-capable clang "
            "(on macOS: CLANG_BIN=$(brew --prefix llvm)/bin/clang), or use --engine hypothesis"
        )
    names = [n for n, _ in params]
    try:
        target = load_checks(info, intent, names, ids)
    except FidelityError as e:
        raise FuzzError(str(e)) from e
    result = FuzzResult(
        target=f"{info.path}::{info.qualname}",
        engine=engine,
        budget_s=budget_s,
        skipped=list(target.skipped.values())
        + [
            PropertyRefutation(id=pid, refuted=False, note="an example is a single input")
            for pid in target.examples
        ],
    )
    hints = {p.id: p.formal_hint for p in intent.properties}
    for c in target.checks:
        c.max_slow = 1_000_000  # a campaign skips slow inputs and keeps going until its budget
        started = time.monotonic()
        if engine == "hypothesis":
            _hypothesis_campaign(c, target.fn, params, target.ns, budget_s)
        else:
            _atheris_campaign(c, info, intent, params, target.fn, target.ns, budget_s)
        result.properties.append(
            FuzzEvidence(
                property_id=c.pid,
                hint=hints[c.pid] or c.hint.text,
                function_sha256=info.sha256,
                engine=engine,
                executions=c.calls,
                seconds=round(time.monotonic() - started, 1),
                skipped=c.skipped,
                found=c.found,
                note=c.note,
            )
        )
    return result


def _hypothesis_campaign(
    c: _Check, fn: Any, params: list[tuple[str, PyType]], ns: dict[str, Any], budget_s: float
) -> None:
    names = [n for n, _ in params]
    deadline = time.monotonic() + budget_s
    found = []

    class _Broken(Exception):
        pass

    def search(args: tuple[Any, ...]) -> None:
        if time.monotonic() > deadline:
            return  # let the batch drain quickly
        cx = _violation(c, fn, names, args, ns, "fuzz")
        if cx is not None:
            found.append(cx)  # Hypothesis replays the shrunk example last
            raise _Broken

    strategy = st.tuples(*(strategy_for(t) for _, t in params))
    batch = 0
    while time.monotonic() < deadline and c.found is None:
        batch += 1
        test = seed(batch)(
            settings(
                max_examples=BATCH,
                database=None,
                deadline=None,
                suppress_health_check=SUPPRESSED_HEALTH_CHECKS,
            )(given(strategy)(search))
        )
        try:
            test()
        except _Broken:
            c.found = found[-1]
        except Unsatisfiable:
            c.note = "no generated input satisfies the preconditions"
            return
        except Flaky:
            c.note = "the function gave different results for the same input"
            return


@unbounded_int_digits()
def replay(
    info: FunctionInfo,
    intent: IntentModel,
    params: list[tuple[str, PyType]],
    evidence: FuzzEvidence,
) -> Counterexample | None:
    """Run a recorded counterexample again in this process: still a counterexample, or None."""
    if evidence.found is None:
        return None
    names = [n for n, _ in params]
    try:
        target = load_checks(info, intent, names, [evidence.property_id])
        args = tuple(_literal(evidence.found.inputs[n]) for n in names)
    except (FidelityError, KeyError, ValueError, SyntaxError):
        return None
    if not target.checks:
        return None
    return _violation(target.checks[0], target.fn, names, args, target.ns, "fuzz")


# ---- atheris (subprocess) ----------------------------------------------------------------------


def _atheris_campaign(
    c: _Check,
    info: FunctionInfo,
    intent: IntentModel,
    params: list[tuple[str, PyType]],
    fn: Any,
    ns: dict[str, Any],
    budget_s: float,
) -> None:
    with tempfile.TemporaryDirectory(prefix="apex-fuzz-") as tmp:
        config = Path(tmp) / "config.json"
        found = Path(tmp) / "found.json"
        config.write_text(
            json.dumps(
                {
                    "path": str(Path(info.path).resolve()),
                    "qualname": info.qualname,
                    "intent": intent.model_dump(mode="json", by_alias=True),
                    "params": [[n, t.python()] for n, t in params],
                    "property": c.pid,
                    "found": str(found),
                }
            )
        )
        cmd = [
            sys.executable,
            "-m",
            "apex.fidelity.fuzz",
            f"-max_total_time={max(1, int(budget_s))}",
            "-use_value_profile=1",
            f"-artifact_prefix={tmp}/",
        ]
        try:
            run = subprocess.run(
                cmd,
                env={**os.environ, _CONFIG_ENV: str(config)},
                capture_output=True,
                text=True,
                timeout=budget_s + 60,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            c.note = "the atheris campaign did not stop within its budget"
            return
        m = _RUNS.search(run.stderr)
        c.calls = int(m.group(1)) if m else c.calls
        if not found.is_file():
            if run.returncode != 0 and not m:
                tail = run.stderr.strip().splitlines()[-1:] or ["no output"]
                c.note = f"atheris failed: {tail[0]}"
            return
        args = tuple(_literal(v) for v in json.loads(found.read_text())["args"])
        names = [n for n, _ in params]
        # Replayed here, in plain Python. libFuzzer exits before reporting its run count, so the
        # campaign's executions are only known as "at least this replay".
        c.found = _violation(c, fn, names, args, ns, "fuzz")
        if c.found is None:
            c.note = "atheris reported an input that did not reproduce when replayed"


def _literal(text: str) -> Any:
    import ast

    return ast.literal_eval(text)


def _consume(fdp: Any, ty: PyType) -> Any:
    """A value of `ty` from libFuzzer's bytes, with refutation's mix of small and large ints."""
    match ty.kind:
        case "int":
            bound = (16, 2**16, 2**40)[fdp.ConsumeIntInRange(0, 2)]
            return fdp.ConsumeIntInRange(-bound, bound)
        case "bool":
            return fdp.ConsumeBool()
        case "list":
            return [_consume(fdp, ty.args[0]) for _ in range(fdp.ConsumeIntInRange(0, 8))]
        case "tuple":
            return tuple(_consume(fdp, a) for a in ty.args)
        case "option":
            return None if fdp.ConsumeBool() else _consume(fdp, ty.args[0])
    raise FidelityError(f"no fuzz input for {ty.kind}")


def _atheris_main() -> None:  # pragma: no cover - runs inside the libFuzzer subprocess
    import ast
    import importlib

    from apex.analyze.target import load_function
    from apex.analyze.types import parse_annotation

    atheris: Any = importlib.import_module("atheris")  # no type stubs
    cfg = json.loads(Path(os.environ[_CONFIG_ENV]).read_text())
    info = load_function(f"{cfg['path']}::{cfg['qualname']}")
    intent = IntentModel.model_validate(cfg["intent"])
    params = [(n, parse_annotation(ast.parse(t, mode="eval").body)) for n, t in cfg["params"]]
    names = [n for n, _ in params]
    target = load_checks(info, intent, names, [cfg["property"]])
    (check,) = target.checks
    check.max_slow = 1_000_000
    # instrument_func returns an instrumented copy: rebind it in the module, so that calls between
    # the module's functions go through the instrumented versions too.
    module_globals = target.fn.__globals__
    for name in {info.qualname, *target.ns}:
        if callable(module_globals.get(name)):
            module_globals[name] = atheris.instrument_func(module_globals[name])
    target.fn = module_globals[info.qualname]
    target.ns = {name: module_globals[name] for name in target.ns}

    def one(data: bytes) -> None:
        fdp = atheris.FuzzedDataProvider(data)
        args = tuple(_consume(fdp, t) for _, t in params)
        if _violation(check, target.fn, names, args, target.ns, "fuzz") is not None:
            Path(cfg["found"]).write_text(json.dumps({"args": [repr(a) for a in args]}))
            os._exit(0)  # the parent replays it; libFuzzer's own crash report is not needed

    # The harness and the precondition check are instrumented too: libFuzzer quits when its first
    # (empty) input covers nothing, which happens whenever that input fails a precondition, and
    # their branches steer it toward inputs that pass the preconditions.
    from apex.fidelity import refute

    atheris.instrument_func(refute._violation)
    atheris.instrument_func(one)
    atheris.Setup(sys.argv, one)
    atheris.Fuzz()


if __name__ == "__main__":  # pragma: no cover
    _atheris_main()
