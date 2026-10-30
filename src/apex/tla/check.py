"""`apex tla check`: TLC on the spec, then the real code against the spec, per invariant.

The outcome of one invariant, and the verdict `verify` gives it:

- TLC found no violation and every recorded trace is a behavior of the spec: `model_checked`;
- a recorded trace is not a behavior of the spec: `fidelity_failed` (the spec describes some
  other code, so nothing TLC says about it transfers);
- the harness's `check_<Name>` saw the invariant break in a real run: `refuted`, with that run;
- TLC found a violation that no real run showed: `unproven` (SPEC_VIOLATION_NOT_REPRODUCED),
  carrying TLC's trace; a TLC counterexample alone never refutes the code;
- TLC could not check the spec: `unproven` (MODEL_CHECK_FAILED).
"""

import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from apex.core.report import Counterexample, ModelCheckEvidence
from apex.tla import conform
from apex.tla.bundle import HARNESS, SPEC, Bundle
from apex.tla.tlc import TlcOutcome, run_tlc, translate_pluscal

MAX_TRACES = 50
"""Distinct recorded traces checked by TLC per run of `apex tla check` (one JVM start each)."""


class InvariantResult(BaseModel):
    evidence: ModelCheckEvidence
    observed: Counterexample | None = None
    """The invariant breaking in a real run (from the harness's `check_<Name>`)."""
    error: str | None = None
    """TLC could not check the spec."""


class TlaCheckResult(BaseModel):
    target: str
    spec_sha256: str
    invariants: dict[str, InvariantResult] = Field(default_factory=dict)
    """Keyed by property ID (`T_<Name>`)."""
    runs: int = 0
    hung: int = 0
    traces_checked: int = 0
    conformance_error: str | None = None
    """Why the real code could not be checked against the spec: then nothing is model_checked."""
    note: str | None = None


def check_bundle(
    bundle: Bundle,
    target: str,
    code_file: Path,
    function_sha256: str,
    *,
    runs: int = 200,
    run_timeout_s: float = 10.0,
    tlc_timeout_s: float = 300.0,
) -> TlaCheckResult:
    result = TlaCheckResult(target=target, spec_sha256=bundle.sha256)
    with tempfile.TemporaryDirectory(prefix="apex-tla-") as tmp:
        work = Path(tmp)
        shutil.copy(bundle.dir / SPEC, work / SPEC)
        translation_error = translate_pluscal(work / SPEC)
        outcomes: dict[str, TlcOutcome] = {}
        base = bundle.cfg_without_invariants()
        for inv in bundle.invariants:
            outcomes[inv] = (
                TlcOutcome(ok=False, error=translation_error)
                if translation_error
                else run_tlc(work, "Spec", base + f"\nINVARIANT {inv}\n", timeout_s=tlc_timeout_s)
            )

        harness = conform.load_harness(bundle.dir / HARNESS, code_file.parent)
        rec = conform.record(
            harness,
            {str(code_file.resolve())},
            bundle.invariants,
            runs=runs,
            run_timeout_s=run_timeout_s,
        )
        result.runs, result.hung = rec.runs, rec.hung
        rejected: list[dict[str, str]] | None = None
        notes: list[str] = []
        if not translation_error:
            constants = "".join(
                line + "\n"
                for line in base.splitlines()
                if not line.strip().startswith(("SPECIFICATION", "INIT", "NEXT"))
            )
            for trace in rec.traces[:MAX_TRACES]:
                (work / "ApexTrace.tla").write_text(conform.trace_module(trace))
                cfg = "INIT TInit\nNEXT TNext\n" + constants + "INVARIANT TraceUnfinished\n"
                out = run_tlc(work, "ApexTrace", cfg, timeout_s=tlc_timeout_s)
                result.traces_checked += 1
                if "TraceUnfinished" in out.violations:
                    continue  # TLC reached the end of the trace: a behavior of the spec
                if out.ok:
                    rejected = trace
                    break
                result.conformance_error = f"TLC could not check a recorded trace: {out.error}"
                break
            if len(rec.traces) > MAX_TRACES:
                notes.append(f"checked {MAX_TRACES} of {len(rec.traces)} distinct traces")
        if rec.hung:
            result.conformance_error = (
                f"a run did not finish within {run_timeout_s:.0f}s (deadlock or hang?); "
                "its trace is incomplete"
            )
        elif not rec.traces and not translation_error:
            result.conformance_error = "the runs recorded no trace (does observe() see the state?)"
        result.note = "; ".join(notes) or None

        for inv in bundle.invariants:
            o = outcomes[inv]
            observed = None
            if inv in rec.violations:
                run, trace = rec.violations[inv]
                observed = Counterexample(
                    inputs={"run": str(run)},
                    expected=inv,
                    actual="reached "
                    + ", ".join(f"{k} = {v}" for k, v in trace[-1].items())
                    + f" after {len(trace)} observed states",
                    found_by="stress",
                )
            result.invariants[bundle.property_ids[bundle.invariants.index(inv)]] = InvariantResult(
                evidence=ModelCheckEvidence(
                    property_id=f"T_{inv}",
                    invariant=inv,
                    spec_sha256=bundle.sha256,
                    function_sha256=function_sha256,
                    constants=bundle.constants,
                    states_generated=o.states_generated,
                    distinct_states=o.distinct_states,
                    depth=o.depth,
                    tlc_trace=o.violations.get(inv),
                    runs=rec.runs,
                    traces_checked=result.traces_checked,
                    rejected_trace=rejected,
                    note=result.note,
                ),
                observed=observed,
                error=(None if o.ok else o.error) or result.conformance_error,
            )
    return result
