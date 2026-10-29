"""End-to-end through the real verdict path (needs Lean)."""

import shutil
from pathlib import Path

import pytest

from apex.core.verdict import Verdict
from apex.lean.project import LeanProject
from apex.pipeline import Workspace, prove_tactics, scaffold, verify

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


@pytest.mark.lean
def test_clamp_end_to_end_is_unreviewed_until_approved(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    shutil.copy(CORPUS / "mathx.py", tmp_path / "mathx.py")
    ws = Workspace.discover(tmp_path)
    target = str(tmp_path / "mathx.py::clamp")
    sc = scaffold(target, ws, lean_project)
    assert sc.model_compiles and sc.spec_path
    spec = Path(sc.spec_path)
    spec.write_text(spec.read_text() + "\n" + (CORPUS / "specs" / "clamp.lean").read_text())

    closed = prove_tactics(target, ws, project=lean_project).closed
    assert set(closed) == {"E1", "E2", "P1", "P2"}
    report = verify([target], ws, project=lean_project, fidelity_n=50)
    fn = report.functions[0]
    assert fn.fidelity and fn.fidelity.agree
    # Invariant 4: without human approval nothing is `proven`.
    assert {p.verdict for p in fn.properties} == {Verdict.PROVEN_UNREVIEWED}
    assert report.exit_code == 2
    assert ws.last_report.is_file()


@pytest.mark.lean
def test_sorry_in_submitted_proof_is_never_proven(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    shutil.copy(CORPUS / "mathx.py", tmp_path / "mathx.py")
    ws = Workspace.discover(tmp_path)
    target = str(tmp_path / "mathx.py::my_abs")
    sc = scaffold(target, ws, lean_project)
    assert sc.spec_path
    Path(sc.spec_path).write_text("theorem P1 (x : Int) : my_abs x < 0 := by\n  exact sorryAx _\n")
    p1 = next(
        p
        for p in verify([target], ws, project=lean_project).functions[0].properties
        if p.id == "P1"
    )
    assert p1.verdict is Verdict.UNPROVEN
