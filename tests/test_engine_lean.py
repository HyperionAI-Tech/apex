"""Engine mode through the real verdict path (needs Lean): the fixer loop, `check`, exit codes."""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from apex.analyze.target import load_function
from apex.core.verdict import Verdict
from apex.lean.project import LeanProject
from apex.pipeline import (
    RunTimeout,
    Workspace,
    check,
    function_id,
    load_spec,
    prove_llm,
    prove_tactics,
    scaffold,
    verify,
)
from apex.report.sarif import to_sarif

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
FIXTURES = Path(__file__).parent / "fixtures"

GOOD_P1 = {
    "proof": "by\n  exact P1_aux xs 0 (by omega)",
    "lemmas": (
        "theorem P1_aux (xs : List Int) : ∀ n : Int, n ≥ 0 →\n"
        "    xs.foldl (fun n x => if x > (0 : Int) then n + 1 else n) n ≥ 0 := by\n"
        "  induction xs with\n"
        "  | nil => intro n h; exact h\n"
        "  | cons x xs ih =>\n"
        "    intro n h\n"
        "    simp only [List.foldl_cons]\n"
        "    apply ih\n"
        "    split <;> omega"
    ),
    "imports": [],
}


class KeyedProvider:
    """Answers per theorem ID, in order; records every prompt."""

    name = "scripted"

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.prompts: dict[str, list[str]] = {}

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        m = re.search(r"Theorem to prove \(ID (\w+);", prompt)
        assert m, prompt
        self.prompts.setdefault(m[1], []).append(prompt)
        queue = self.answers.get(m[1]) or [{"proof": "by simp", "lemmas": "", "imports": []}]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _workspace(tmp_path: Path, *files: str, config: str = "") -> Workspace:
    for name in files:
        shutil.copy(CORPUS / name, tmp_path / name)
    (tmp_path / "apex.toml").write_text(f"[apex]\nrefute_budget_s = 2\nllm_parallel = 1\n{config}")
    return Workspace.discover(tmp_path)


@pytest.mark.lean
def test_fixer_loop_closes_a_proof_from_lean_errors_and_the_verdict_path_judges_it(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    ws = _workspace(tmp_path, "mathx.py", "mathx.spec.md", config="retries = 3\n")
    target = str(tmp_path / "mathx.py::count_positive")
    scaffold(target, ws, lean_project)
    assert "P1" in prove_tactics(target, ws, project=lean_project).open
    llm = KeyedProvider(
        {
            "P1": [
                {"proof": "by sorry", "lemmas": "", "imports": []},  # refused before Lean
                {"proof": "by\n  simp [count_positive]", "lemmas": "", "imports": []},
                GOOD_P1,
            ],
            # Tries to weaken the statement through a lemma named like a property: refused.
            "P2": [
                {"proof": "by trivial", "lemmas": "theorem P2 : True := trivial", "imports": []}
            ],
        }
    )
    res = prove_llm(target, ws, llm, project=lean_project)
    assert res.closed.keys() == {"P1"} and res.attempts["P1"] == 3
    assert "P2" in res.open and "P3" in res.open
    # Lean's error from attempt 2 reached the model in attempt 3's prompt.
    assert "Attempt 2 was rejected" in llm.prompts["P1"][2]
    assert "sorry" in llm.prompts["P1"][1]  # and the guard's refusal in attempt 2's

    spec = load_spec(ws.spec_path(load_function(target)))
    assert spec.get("P1_aux") is not None
    report = verify([target], ws, project=lean_project, fidelity_n=50)
    props = {p.id: p for p in report.functions[0].properties}
    assert props["P1"].verdict is Verdict.PROVEN_UNREVIEWED
    assert props["P1"].provenance.proof_by == "scripted"
    assert props["P2"].verdict is Verdict.UNPROVEN

    # Editing the proof by hand drops the model's provenance (it is keyed on the proof text).
    path = ws.spec_path(load_function(target))
    edited = path.read_text().replace("(by omega)", "(by decide)")
    path.write_text(edited)
    again = verify([target], ws, project=lean_project, fidelity_n=50)
    p1 = next(p for p in again.functions[0].properties if p.id == "P1")
    assert p1.verdict is Verdict.PROVEN_UNREVIEWED and p1.provenance.proof_by is None


@pytest.mark.lean
def test_check_over_a_directory_reports_bugs_gaps_and_scope(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    ws = _workspace(tmp_path, "mathx_bugs.py", "mathx_bugs.spec.md", "impure.py")
    (tmp_path / "bare.py").write_text("def ident(x: int) -> int:\n    return x\n")
    run = check([str(tmp_path)], ws, project=lean_project, fidelity_n=50)
    report = run.report
    assert report.mode == "engine" and report.provider is None
    assert report.exit_code == 1
    fns = {f.qualname: f for f in report.functions}
    assert fns["impure.logged_clamp"].gap and not fns["impure.logged_clamp"].properties
    assert fns["bare.ident"].gap and fns["bare.ident"].gap.reason_code == "SPEC_AMBIGUOUS"
    verdicts = {p.verdict for f in report.functions for p in f.properties}
    assert Verdict.REFUTED in verdicts and Verdict.PROVEN not in verdicts
    assert any(s.step == "tactics" for s in run.steps)
    sarif = to_sarif(report, tmp_path)
    jsonschema.validate(sarif, json.loads((FIXTURES / "sarif-2.1.0.schema.json").read_text()))


@pytest.mark.lean
def test_exit_0_only_when_every_property_is_approved_and_proven(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    ws = _workspace(tmp_path, "mathx.py", "mathx.spec.md")
    target = str(tmp_path / "mathx.py::clamp")
    scaffold(target, ws, lean_project)
    prove_tactics(target, ws, project=lean_project)
    assert verify([target], ws, project=lean_project, fidelity_n=50).exit_code == 2

    info = load_function(target)
    for d in load_spec(ws.spec_path(info)).properties:  # what `apex review --approve` records
        ws.review_store.approve(
            qualname=function_id(info),
            property_id=d.name,
            statement=d.full_statement,
            function_sha256=info.sha256,
            statement_sha256=d.statement_sha256,
            reviewer="test",
        )
    report = verify([target], ws, project=lean_project, fidelity_n=50)
    assert {p.verdict for p in report.functions[0].properties} == {Verdict.PROVEN}
    assert report.exit_code == 0


@pytest.mark.lean
def test_fail_on_promotes_gaps_and_the_run_budget_is_a_tool_error(
    tmp_path: Path, lean_project: LeanProject
) -> None:
    ws = _workspace(
        tmp_path,
        "mathx.py",
        "mathx.spec.md",
        config='fail_on = ["refuted", "fidelity_failed", "contradiction", "proven_unreviewed"]\n',
    )
    target = str(tmp_path / "mathx.py::clamp")
    scaffold(target, ws, lean_project)
    prove_tactics(target, ws, project=lean_project)
    assert verify([target], ws, project=lean_project, fidelity_n=50).exit_code == 1
    with pytest.raises(RunTimeout):
        check([target], ws, project=lean_project, timeout_s=1e-6)
