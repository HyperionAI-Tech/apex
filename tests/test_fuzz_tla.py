"""Fuzzing campaigns and TLA+ checking: evidence verdicts that never become `proven`."""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from apex.core.report import Counterexample, FuzzEvidence, ModelCheckEvidence, PropertyResult
from apex.core.verdict import ReasonCode, Verdict
from apex.fidelity.fuzz import atheris_available
from apex.pipeline import Workspace, fuzz_target, tla_check_target, verify
from apex.tla.bundle import BundleError, load_bundle, parse_cfg
from apex.tla.check import InvariantResult, TlaCheckResult
from apex.tla.conform import HarnessError, load_harness, record, tla_value, trace_module
from apex.tla.tlc import TlaUnavailable, parse_tlc, tools

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals" / "corpus"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tlc"

LOOPS = '''
def isqrt(n: int) -> int:
    """pre: 0 <= n <= 10**12
    post: __return__ * __return__ <= n < (__return__ + 1) * (__return__ + 1)"""
    lo, hi = 0, n + 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if mid * mid <= n:
            lo = mid
        else:
            hi = mid
    return lo


def checksum(n: int) -> int:
    """post: 0 <= __return__ < 997"""
    total = 0
    while n > 0:
        total = (total + n % 1000) % 997
        n //= 1000
    if (n * n + total * total) % 101 == 1:  # e.g. n = -1: the loop is skipped
        return -1
    return total
'''


@pytest.fixture
def loops(tmp_path: Path) -> Workspace:
    (tmp_path / "loops.py").write_text(textwrap.dedent(LOOPS))
    (tmp_path / "apex.toml").write_text("[apex]\nrefute_budget_s = 1\n")
    return Workspace.discover(tmp_path)


# ---- fuzzing ---------------------------------------------------------------------------------


def test_a_clean_campaign_makes_an_out_of_scope_property_fuzzed(loops: Workspace) -> None:
    target = str(loops.root / "loops.py::isqrt")
    before = verify([target], loops, write_report=False).functions[0].properties[0]
    assert before.verdict is Verdict.OUT_OF_SCOPE  # a while loop: Lean has no model
    (e,) = fuzz_target(target, loops, budget_s=2).properties
    assert e.found is None and e.executions > 0 and e.engine == "hypothesis"
    p = verify([target], loops, write_report=False).functions[0].properties[0]
    assert p.verdict is Verdict.FUZZED and p.fuzz == e
    assert p.gap is not None and p.gap.reason_code is ReasonCode.NONTERMINATION  # why not proven
    report = verify([target], loops, write_report=False)
    assert report.exit_code == 2  # fuzzed is a gap, never a pass


def test_evidence_lapses_when_the_function_changes(loops: Workspace) -> None:
    target = str(loops.root / "loops.py::isqrt")
    fuzz_target(target, loops, budget_s=1)
    src = loops.root / "loops.py"
    src.write_text(src.read_text().replace("lo, hi = 0, n + 1", "lo, hi = 0, n + 2"))
    p = verify([target], loops, write_report=False).functions[0].properties[0]
    assert p.verdict is Verdict.OUT_OF_SCOPE


def test_a_campaign_counterexample_is_replayed_and_refutes(loops: Workspace) -> None:
    target = str(loops.root / "loops.py::checksum")
    (e,) = fuzz_target(target, loops, budget_s=5).properties
    assert e.found is not None and e.found.found_by == "fuzz"
    report = verify([target], loops, write_report=False)
    assert report.functions[0].properties[0].verdict is Verdict.REFUTED
    assert report.exit_code == 1


@pytest.mark.skipif(not atheris_available(), reason="atheris (the fuzz extra) is not installed")
def test_atheris_engine_runs_in_a_subprocess_and_replays(loops: Workspace) -> None:
    clean = fuzz_target(str(loops.root / "loops.py::isqrt"), loops, budget_s=3, engine="atheris")
    assert clean.properties[0].found is None and clean.properties[0].executions > 1000
    bug = fuzz_target(str(loops.root / "loops.py::checksum"), loops, budget_s=10, engine="atheris")
    assert bug.properties[0].found is not None


def test_evidence_verdicts_need_their_evidence() -> None:
    with pytest.raises(ValueError, match="fuzzed requires"):
        PropertyResult(id="P1", verdict=Verdict.FUZZED, gap=_gap())
    e = FuzzEvidence(
        property_id="P1",
        hint="r >= 0",
        function_sha256="x",
        engine="hypothesis",
        executions=10,
        seconds=1,
        found=Counterexample(inputs={"x": "1"}, found_by="fuzz"),
    )
    with pytest.raises(ValueError, match="fuzzed requires"):
        PropertyResult(id="P1", verdict=Verdict.FUZZED, gap=_gap(), fuzz=e)
    m = ModelCheckEvidence(
        property_id="T_I", invariant="I", spec_sha256="s", function_sha256="f", tlc_trace=[{}]
    )
    with pytest.raises(ValueError, match="model_checked requires"):
        PropertyResult(id="T_I", verdict=Verdict.MODEL_CHECKED, gap=_gap(), model_check=m)


def _gap():
    from apex.core.report import Gap

    return Gap(reason_code=ReasonCode.NEEDS_CONCURRENCY_TOOL, explanation="x")


# ---- TLC output, configs, values -------------------------------------------------------------


def test_tlc_violation_is_read_from_the_error_lines_not_the_summary() -> None:
    out = (FIXTURES / "violation.out").read_text()
    # A -continue run ends with "No error has been found" even after reporting violations.
    assert "No error has been found" in out
    res = parse_tlc(out)
    assert res.ok and list(res.violations) == ["NoLostUpdate"]
    last = res.violations["NoLostUpdate"][-1]
    assert last["count"] == "3" and last["pc"] == '<<"Done", "Done">>'
    assert (res.distinct_states, res.depth) == (201, 15)


def test_tlc_success_and_parse_error() -> None:
    ok = parse_tlc((FIXTURES / "success.out").read_text())
    assert ok.ok and not ok.violations and ok.distinct_states == 36
    bad = parse_tlc((FIXTURES / "parse_error.out").read_text())
    assert not bad.ok and bad.error


def test_cfg_parsing() -> None:
    cfg = "SPECIFICATION Spec\nCONSTANTS N = 2\n  K = 3 \\* bound\nINVARIANTS A\n  B\nINVARIANT C\n"
    assert parse_cfg(cfg) == (["A", "B", "C"], {"N": "2", "K": "3"})


def test_tla_values_and_trace_module() -> None:
    assert tla_value([1, True, "a"]) == '<<1, TRUE, "a">>'
    assert tla_value({3, 1}) == "{1, 3}"
    assert tla_value({"x": -1}) == "[x |-> -1]"
    with pytest.raises(HarnessError):
        tla_value(1.5)
    mod = trace_module([{"count": "0"}, {"count": "-1"}])
    assert "EXTENDS Spec, Integers" in mod  # negative values need Integers
    assert "count' = Trace[tl + 1].count" in mod and "UNCHANGED <<tl, count>>" in mod


def test_bundle_validation(tmp_path: Path) -> None:
    shutil.copytree(CORPUS / "tla" / "locked_worker", tmp_path / "b")
    b = load_bundle(tmp_path / "b")
    assert b.property_ids == ["T_NoLostUpdate", "T_NeverAhead"]
    before = b.sha256
    (tmp_path / "b" / "harness.py").write_text(b.harness + "\n# edited\n")
    assert load_bundle(tmp_path / "b").sha256 != before  # every file is part of the approval
    (tmp_path / "b" / "Spec.tla").write_text(b.spec.replace("MODULE Spec", "MODULE Other"))
    with pytest.raises(BundleError, match="named Spec"):
        load_bundle(tmp_path / "b")


# ---- recording the real code (no TLC) --------------------------------------------------------


def test_recording_reproduces_the_lost_update_and_not_on_the_locked_code() -> None:
    watched = {str((CORPUS / "workers.py").resolve())}
    racy = load_harness(CORPUS / "tla" / "racy_worker" / "harness.py", CORPUS)
    rec = record(racy, watched, ["NoLostUpdate", "NeverAhead"], runs=300)
    assert "NoLostUpdate" in rec.violations and "NeverAhead" not in rec.violations
    locked = load_harness(CORPUS / "tla" / "locked_worker" / "harness.py", CORPUS)
    rec = record(locked, watched, ["NoLostUpdate", "NeverAhead"], runs=50)
    assert not rec.violations and rec.traces
    assert all(t[0]["count"] == "0" and t[-1]["count"] == "4" for t in rec.traces)


def test_recording_through_a_symlinked_directory(tmp_path: Path) -> None:
    # macOS's /var/folders is a symlink to /private/var/folders: code objects carry the path the
    # module was imported by, so the tracer must compare real paths, or it records nothing but
    # the first and last state (and conformance then rejects every run).
    real = tmp_path / "real"
    real.mkdir()
    shutil.copy(CORPUS / "workers.py", real / "workers.py")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    h = load_harness(CORPUS / "tla" / "locked_worker" / "harness.py", link)
    rec = record(h, {str(real / "workers.py")}, [], runs=5)
    assert all(len(t) == 5 for t in rec.traces)  # 0, 1, 2, 3, 4


# ---- verdicts from recorded TLA+ results ------------------------------------------------------


def _tla_ws(tmp_path: Path, name: str) -> tuple[Workspace, str]:
    shutil.copy(CORPUS / "workers.py", tmp_path / "workers.py")
    ws = Workspace.discover(tmp_path)
    dest = tmp_path / ".apex" / "tla" / "workers" / name
    shutil.copytree(CORPUS / "tla" / name, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return ws, str(tmp_path / f"workers.py::{name}")


def _write_result(ws: Workspace, target: str, **kw) -> None:
    from apex.analyze.target import load_function

    info = load_function(target)
    b = load_bundle(ws.tla_dir(info))
    res = TlaCheckResult(target=target, spec_sha256=b.sha256)
    for inv, pid in zip(b.invariants, b.property_ids, strict=True):
        ev = ModelCheckEvidence(
            property_id=pid,
            invariant=inv,
            spec_sha256=b.sha256,
            function_sha256=info.sha256,
            runs=10,
            traces_checked=1,
            **{k: v for k, v in kw.items() if k in ("tlc_trace", "rejected_trace")},
        )
        res.invariants[pid] = InvariantResult(
            evidence=ev, observed=kw.get("observed"), error=kw.get("error")
        )
    (ws.tla_dir(info) / "evidence.json").write_text(res.model_dump_json())


@pytest.mark.parametrize(
    ("kw", "verdict", "reason"),
    [
        ({}, Verdict.MODEL_CHECKED, ReasonCode.NEEDS_CONCURRENCY_TOOL),
        (
            {"tlc_trace": [{"count": "3"}]},
            Verdict.UNPROVEN,
            ReasonCode.SPEC_VIOLATION_NOT_REPRODUCED,
        ),
        ({"error": "parse"}, Verdict.UNPROVEN, ReasonCode.MODEL_CHECK_FAILED),
        ({"rejected_trace": [{"count": "-1"}]}, Verdict.FIDELITY_FAILED, None),
        (
            {
                "rejected_trace": [{"count": "-1"}],
                "observed": Counterexample(inputs={"run": "3"}, expected="I", found_by="stress"),
            },
            Verdict.REFUTED,
            None,
        ),
    ],
)
def test_tla_verdict_precedence(tmp_path: Path, kw: dict, verdict: Verdict, reason) -> None:
    ws, target = _tla_ws(tmp_path, "locked_worker")
    _write_result(ws, target, **kw)
    props = verify([target], ws, write_report=False).functions[0].properties
    assert [p.id for p in props] == ["T_NoLostUpdate", "T_NeverAhead"]
    assert {p.verdict for p in props} == {verdict}
    if reason:
        assert props[0].gap is not None and props[0].gap.reason_code is reason


def test_a_bundle_edit_makes_the_tla_result_stale(tmp_path: Path) -> None:
    ws, target = _tla_ws(tmp_path, "locked_worker")
    _write_result(ws, target)
    cfg = tmp_path / ".apex" / "tla" / "workers" / "locked_worker" / "Spec.cfg"
    cfg.write_text(cfg.read_text().replace("K = 2", "K = 3"))
    props = verify([target], ws, write_report=False).functions[0].properties
    assert {p.verdict for p in props} == {Verdict.UNPROVEN}
    assert "changed since the last check" in (props[0].gap.explanation if props[0].gap else "")


# ---- end to end with TLC ---------------------------------------------------------------------


def _have_tlc() -> bool:
    try:
        tools()
    except TlaUnavailable:
        return False
    return True


@pytest.mark.tla
@pytest.mark.skipif(not _have_tlc(), reason="needs Java and `apex init --tla`")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("racy_worker", {"T_NoLostUpdate": Verdict.REFUTED, "T_NeverAhead": Verdict.MODEL_CHECKED}),
        (
            "locked_worker",
            {"T_NoLostUpdate": Verdict.MODEL_CHECKED, "T_NeverAhead": Verdict.MODEL_CHECKED},
        ),
        (
            "jittery_worker",
            {"T_NoLostUpdate": Verdict.FIDELITY_FAILED, "T_NeverAhead": Verdict.FIDELITY_FAILED},
        ),
    ],
)
def test_tla_check_end_to_end(tmp_path: Path, name: str, expected: dict) -> None:
    ws, target = _tla_ws(tmp_path, name)
    res = tla_check_target(target, ws, runs=300)
    assert res.traces_checked >= 1
    if name == "racy_worker":
        assert res.invariants["T_NoLostUpdate"].evidence.tlc_trace  # TLC finds it in the spec too
    props = verify([target], ws, write_report=False).functions[0].properties
    assert {p.id: p.verdict for p in props} == expected
    stored = json.loads((ws.tla_dir(_info(target)) / "evidence.json").read_text())
    assert stored["spec_sha256"] == load_bundle(ws.tla_dir(_info(target))).sha256


def _info(target: str):
    from apex.analyze.target import load_function

    return load_function(target)


def test_each_tla_tool_run_gets_its_own_java_tmpdir() -> None:
    # TLC unpacks Naturals.tla & co. into java.io.tmpdir; parallel runs sharing the system one
    # deleted each other's copies ("File does not exist: .../FiniteSets.tla").
    from apex.tla.tlc import _java

    with _java("java", Path("t.jar")) as a, _java("java", Path("t.jar")) as b:
        dirs = [next(x for x in cmd if x.startswith("-Djava.io.tmpdir=")) for cmd in (a, b)]
        assert dirs[0] != dirs[1] and all(Path(d.split("=", 1)[1]).is_dir() for d in dirs)


def test_an_execution_budget_makes_a_campaign_reproducible(loops: Workspace) -> None:
    target = str(loops.root / "loops.py::isqrt")
    runs = [
        fuzz_target(target, loops, budget_s=60, max_executions=400).properties[0] for _ in range(2)
    ]
    assert [r.executions for r in runs] == [400, 400]  # stops at the budget, not the clock
    assert all(r.found is None for r in runs)
