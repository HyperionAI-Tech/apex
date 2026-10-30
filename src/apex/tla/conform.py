"""Does the real code behave like its TLA+ spec? Record runs, then let TLC check each trace.

Recording: the harness runs the real code `runs` times. A trace function (installed in every
thread the run starts) snapshots `observe(state)` after each line executed in the code's own
files and keeps a snapshot whenever it differs from the last, so a trace is the sequence of
distinct shared states the run went through. Scheduling is perturbed so that runs interleave
differently: a tiny thread switch interval, and a short random sleep at some line events.

Checking: for each distinct trace TLC gets a module that extends the spec with a counter `tl` and
allows only steps of `Next` that either leave every observed variable unchanged (a step the
observation cannot see) or move to the next recorded state. If TLC can reach the end of the trace
(reported as a violation of `TraceUnfinished`, the usual trick), the run is a behavior of the
spec; if it cannot, the spec does not describe this code.

A run's observations are also handed to the harness's `check_<Name>(obs, done)` functions (`done`
is true for the last observation of a run that finished), so that an invariant TLC breaks in the
spec can be watched breaking in the real code too.
"""

import importlib.util
import os
import random
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PERTURB_P = 0.05
"""Share of line events at which the running thread sleeps briefly, to vary the interleaving."""


class HarnessError(RuntimeError):
    """The harness failed to import, to set up, or to run."""


def tla_value(v: Any) -> str:
    """A Python value as a TLA+ expression (what `observe` returns has to be one of these)."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list | tuple):
        return "<<" + ", ".join(tla_value(x) for x in v) + ">>"
    if isinstance(v, set | frozenset):
        return "{" + ", ".join(sorted(tla_value(x) for x in v)) + "}"
    if isinstance(v, dict) and all(isinstance(k, str) and k.isidentifier() for k in v):
        return "[" + ", ".join(f"{k} |-> {tla_value(x)}" for k, x in v.items()) + "]"
    raise HarnessError(f"observe() returned {type(v).__name__}, which has no TLA+ form: {v!r}")


@dataclass
class Recording:
    traces: list[list[dict[str, str]]] = field(default_factory=list)
    """Distinct traces: each a list of {variable: TLA+ value}."""
    runs: int = 0
    hung: int = 0
    """Runs that did not finish within the per-run budget (threads left blocked: deadlock?)."""
    violations: dict[str, tuple[int, list[dict[str, str]]]] = field(default_factory=dict)
    """Invariant -> (run number, trace up to the observation `check_<Name>` rejected)."""


def load_harness(path: Path, code_dir: Path) -> Any:
    name = f"_apex_harness_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    # The harness imports the code by module name. A module of that name already imported (an
    # earlier check in a long-lived process, or a copy elsewhere) would be the wrong code: forget
    # every cached module that is not this directory's, so the import below is fresh.
    root = code_dir.resolve()
    for name, mod in list(sys.modules.items()):
        file = getattr(mod, "__file__", None)
        if file and _shadows(Path(file), root):
            del sys.modules[name]
    sys.path.insert(0, str(code_dir))
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ inside the reviewed bundle folder
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise HarnessError(f"importing {path} failed: {type(e).__name__}: {e}") from e
    finally:
        sys.path.remove(str(code_dir))
        sys.dont_write_bytecode = write_bytecode
    return module


def _shadows(file: Path, code_dir: Path) -> bool:
    """A cached module that a bare import from `code_dir` should resolve afresh: one that lives
    there, or one with the same file name as a module there (an import would mean that one)."""
    if file.suffix != ".py":
        return False
    file = file.resolve()
    return file.parent == code_dir or (code_dir / file.name).is_file()


def record(
    harness: Any,
    watched: set[str],
    invariants: list[str],
    *,
    runs: int = 200,
    seed: int = 0,
    run_timeout_s: float = 10.0,
) -> Recording:
    """Run the harness `runs` times; `watched`: paths of the code's own files."""
    watched = {os.path.realpath(w) for w in watched}
    checks = {
        inv: getattr(harness, f"check_{inv}")
        for inv in invariants
        if callable(getattr(harness, f"check_{inv}", None))
    }
    out = Recording()
    seen: set[tuple[tuple[tuple[str, str], ...], ...]] = set()
    old_interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1e-6)
        for r in range(runs):
            rng = random.Random(seed + r)
            trace, observed, finished = _one_run(harness, watched, rng, run_timeout_s)
            out.runs += 1
            if not finished:
                out.hung += 1
                break  # its threads may still be running; later runs would not be independent
            for inv, check in checks.items():
                if inv in out.violations:
                    continue
                for i, obs in enumerate(observed):
                    if not check(obs, i == len(observed) - 1):
                        out.violations[inv] = (r, trace[: i + 1])
                        break
            key = tuple(tuple(sorted(s.items())) for s in trace)
            if key not in seen:
                seen.add(key)
                out.traces.append(trace)
    finally:
        sys.setswitchinterval(old_interval)
    return out


def _one_run(
    harness: Any, watched: set[str], rng: random.Random, timeout_s: float
) -> tuple[list[dict[str, str]], list[dict[str, Any]], bool]:
    try:
        state = harness.setup()
    except Exception as e:
        raise HarnessError(f"setup() failed: {type(e).__name__}: {e}") from e
    trace: list[dict[str, str]] = []
    observed: list[dict[str, Any]] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def snapshot() -> None:
        with lock:
            obs = harness.observe(state)
            tla = {k: tla_value(v) for k, v in obs.items()}
            if not trace or trace[-1] != tla:
                trace.append(tla)
                observed.append(dict(obs))

    def local(frame: Any, event: str, arg: Any) -> Any:
        if event == "line":
            snapshot()
            if rng.random() < PERTURB_P:
                time.sleep(rng.choice((0, 1e-5, 1e-4)))
        return local

    real: dict[str, bool] = {}

    def is_watched(filename: str) -> bool:
        # Compare real paths: code objects carry the path the module was imported by, which may
        # go through a symlink (macOS's /var/folders is /private/var/folders).
        if filename not in real:
            real[filename] = os.path.realpath(filename) in watched
        return real[filename]

    def tracer(frame: Any, event: str, arg: Any) -> Any:
        if event == "call" and is_watched(frame.f_code.co_filename):
            return local
        return None

    def body() -> None:
        sys.settrace(tracer)
        try:
            harness.run(state)
        except BaseException as e:  # reported to the caller, not swallowed
            errors.append(e)
        finally:
            sys.settrace(None)

    snapshot()
    threading.settrace(tracer)
    try:
        runner = threading.Thread(target=body, daemon=True)
        runner.start()
        runner.join(timeout_s)
    finally:
        threading.settrace(None)  # type: ignore[arg-type]
    if runner.is_alive():
        return trace, observed, False
    if errors:
        e = errors[0]
        raise HarnessError(f"run() raised {type(e).__name__}: {e}") from e
    snapshot()
    return trace, observed, True


def trace_module(trace: list[dict[str, str]]) -> str:
    """A TLA+ module checking that `trace` is a behavior of module Spec (see the docstring)."""
    names = list(trace[0])
    states = ",\n    ".join("[" + ", ".join(f"{k} |-> {s[k]}" for k in names) + "]" for s in trace)
    match_init = " /\\ ".join(f"{k} = Trace[1].{k}" for k in names)
    match_next = " /\\ ".join(f"{k}' = Trace[tl + 1].{k}" for k in names)
    unchanged = ", ".join(["tl", *names])
    return (
        "---- MODULE ApexTrace ----\n"
        "EXTENDS Spec, Integers, Sequences, TLC\n"  # Integers: observed values may be negative
        "VARIABLE tl\n"
        f"Trace == <<\n    {states}\n>>\n"
        f"TInit == Init /\\ tl = 1 /\\ {match_init}\n"
        f"Hidden == Next /\\ UNCHANGED <<{unchanged}>>\n"
        f"Step == tl < Len(Trace) /\\ Next /\\ tl' = tl + 1 /\\ {match_next}\n"
        "TNext == Hidden \\/ Step\n"
        "TraceUnfinished == tl < Len(Trace)\n"
        "====\n"
    )
