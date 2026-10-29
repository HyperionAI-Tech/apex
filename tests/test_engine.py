"""Engine mode without Lean: the proof fixer's guards, target discovery, SARIF, explain/suggest."""

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from apex.core.report import (
    Counterexample,
    FunctionResult,
    Gap,
    PropertyResult,
    Report,
    run_verdicts,
)
from apex.core.verdict import ExitCode, ReasonCode, Verdict, exit_code_for
from apex.llm.providers import ProviderError
from apex.pipeline import NO_INTENT_GAP, Workspace, discover_targets
from apex.prove.llm import (
    Candidate,
    ProofContext,
    _validate,
    build_prompt,
    search_proof,
    splice,
)
from apex.report.explain import advise, regression_test
from apex.report.sarif import to_sarif
from apex.theorems.spec import parse_spec

FIXTURES = Path(__file__).parent / "fixtures"

SPEC = """\
/-- P1: never negative. [hint: r >= 0; spec m.spec.md:1] -/
theorem P1 (x : Int) :
    (f x) ≥ (0 : Int) := by
  sorry

-- TODO P2: something without a hint

/-- P3: bounded. [hint: r <= 9; spec m.spec.md:2] -/
theorem P3 (x : Int) :
    (f x) ≤ (9 : Int) := by
  sorry
"""


def _p1() -> tuple[Any, Any]:
    spec = parse_spec(SPEC)
    decl = spec.get("P1")
    assert decl is not None
    return spec, decl


# ---- the fixer can add proofs and lemmas, never change a statement ----------------------------


def test_splice_writes_proof_and_named_lemmas_and_keeps_the_rest() -> None:
    spec, decl = _p1()
    out = splice(
        SPEC,
        spec,
        decl,
        Candidate(
            proof="by\n  exact P1_aux x",
            lemmas="theorem P1_aux (x : Int) : f x ≥ 0 := by\n  omega",
            imports=["Mathlib.Tactic.Ring"],
        ),
    )
    new = parse_spec(out)
    assert out.startswith("import Mathlib.Tactic.Ring\n")
    assert [d.name for d in new.decls] == ["P1_aux", "P1", "P3"]
    assert new.get("P1").statement_sha256 == decl.statement_sha256  # type: ignore[union-attr]
    assert new.get("P1").has_proof and not new.get("P3").has_proof  # type: ignore[union-attr]
    assert "-- TODO P2" in out


@pytest.mark.parametrize(
    "cand, why",
    [
        # A new property smuggled in through an unindented proof line.
        (Candidate(proof="by omega\ntheorem P9 : True := trivial"), "P1_"),
        (Candidate(proof="by omega", lemmas="theorem helper : True := trivial"), "P1_"),
        (Candidate(proof="by omega", lemmas="theorem P7 : True := trivial"), "P1_"),
        (Candidate(proof="by omega", lemmas="def P1_g := 1"), "fit in a spec"),
        # Redefining another property under the same name.
        (Candidate(proof="by omega", lemmas="theorem P3 : True := trivial"), "fit in a spec"),
    ],
)
def test_splice_refuses_anything_but_a_proof_and_its_lemmas(cand: Candidate, why: str) -> None:
    spec, decl = _p1()
    with pytest.raises(ValueError, match=why):
        splice(SPEC, spec, decl, cand)


def test_forbidden_tokens_and_foreign_imports_are_rejected_before_lean() -> None:
    _, decl = _p1()
    assert any("sorry" in p for p in _validate(Candidate(proof="by sorry"), decl))
    assert any("native_decide" in p for p in _validate(Candidate(proof="by native_decide"), decl))
    assert any("axiom" in p for p in _validate(Candidate("by simp", "axiom P1_x : False"), decl))
    bad = _validate(Candidate(proof="by simp", imports=["Evil.Module", "os; rm"]), decl)
    assert len(bad) == 2
    assert _validate(Candidate(proof="by simp", imports=["Mathlib.Tactic"]), decl) == []


class ScriptedProvider:
    name = "scripted"

    def __init__(self, answers: list[dict[str, Any] | Exception]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ctx() -> ProofContext:
    spec = parse_spec(SPEC)
    return ProofContext(
        namespace="m",
        model="def f (x : Int) : Int := x * x",
        spec_text=SPEC,
        spec=spec,
        header="import Apex.Prelude\n",
        prelude="namespace Apex end Apex",
        project=None,  # type: ignore[arg-type]  # the check is stubbed below
        timeout_s=10,
        tried_tactics=["by omega"],
    )


def test_fixer_feeds_lean_errors_back_until_a_proof_passes() -> None:
    provider = ScriptedProvider(
        [
            {"proof": "by simp", "lemmas": "", "imports": []},
            {"proof": "by nlinarith [sq_nonneg x]", "lemmas": "", "imports": ["Mathlib.Tactic"]},
        ]
    )

    def check(ctx: ProofContext, decl: Any, cand: Candidate) -> list[str]:
        return [] if "nlinarith" in cand.proof else ["line 1 of your answer: simp made no progress"]

    _, decl = _p1()
    search = search_proof(provider, _ctx(), decl, attempts=5, check=check)
    assert search.closed is not None and "nlinarith" in search.closed.proof
    assert len(search.attempts) == 2
    assert "simp made no progress" in provider.prompts[1]
    assert "by omega" in provider.prompts[0]  # tactic tiers that already failed are shown
    assert "do not change the statement" in provider.prompts[0]


def test_fixer_stops_on_provider_error_and_at_the_attempt_budget() -> None:
    _, decl = _p1()
    stopped = search_proof(
        ScriptedProvider([ProviderError("not logged in")]), _ctx(), decl, attempts=3,
        check=lambda *_: [],
    )
    assert stopped.closed is None and "not logged in" in (stopped.last_error or "")
    always_bad = ScriptedProvider([{"proof": "by simp", "lemmas": "", "imports": []}] * 3)
    spent = search_proof(always_bad, _ctx(), decl, attempts=3, check=lambda *_: ["nope"])
    assert spent.closed is None and len(spent.attempts) == 3 and spent.last_error == "nope"


def test_prompt_never_contains_the_python_source() -> None:
    _, decl = _p1()
    prompt = build_prompt(_ctx(), decl, [])
    assert "def f (x : Int)" in prompt and "theorem P1" in prompt and "def f(" not in prompt


# ---- discovery -------------------------------------------------------------------------------


def test_discovery_skips_tests_private_ignored_and_tool_dirs(tmp_path: Path) -> None:
    (tmp_path / "apex.toml").write_text('[apex]\nignore = ["gen/**"]\n')
    for rel, body in {
        "pkg/a.py": "def f(x: int) -> int:\n    return x\n\ndef _g() -> int:\n    return 1\n",
        "pkg/test_a.py": "def test_f() -> None:\n    pass\n",
        "pkg/conftest.py": "def fixture() -> None:\n    pass\n",
        "gen/b.py": "def h() -> int:\n    return 1\n",
        ".venv/lib/c.py": "def k() -> int:\n    return 1\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    ws = Workspace.discover(tmp_path)
    found = discover_targets([str(tmp_path)], ws)
    assert found == [f"{tmp_path / 'pkg' / 'a.py'}::f"]
    assert len(discover_targets([str(tmp_path / "pkg")], ws, include_private=True)) == 2
    assert discover_targets(["x.py::f"], ws) == ["x.py::f"]


# ---- exit codes are never vacuously 0 ---------------------------------------------------------


def _fn(name: str, **kw: Any) -> FunctionResult:
    return FunctionResult(qualname=f"m.{name}", file="m.py", sha256="0" * 64, line=3, **kw)


def test_whole_function_gaps_count_in_the_exit_code() -> None:
    oos = _fn("io", gap=Gap(reason_code=ReasonCode.IMPURE_IO, explanation="prints"))
    no_intent = _fn("bare", gap=NO_INTENT_GAP)
    assert exit_code_for(run_verdicts([oos])) is ExitCode.GAPS
    assert run_verdicts([no_intent]) == [Verdict.UNPROVEN]
    assert exit_code_for(run_verdicts([])) is ExitCode.OK


# ---- SARIF and explain/suggest -----------------------------------------------------------------


def _report(tmp_path: Path) -> Report:
    refuted = PropertyResult(
        id="P1",
        verdict=Verdict.REFUTED,
        counterexample=Counterexample(
            inputs={"x": "0", "lo": "0", "hi": "0"},
            expected="lo <= hi -> lo <= r <= hi",
            actual="returns -1",
            found_by="hypothesis",
        ),
    )
    example = PropertyResult(
        id="E1",
        verdict=Verdict.REFUTED,
        counterexample=Counterexample(
            inputs={"x": "5", "lo": "0", "hi": "3"}, expected="3", actual="returns 2",
            found_by="example",
        ),
    )
    gap = PropertyResult(
        id="P2",
        verdict=Verdict.UNPROVEN,
        gap=Gap(reason_code=ReasonCode.BUDGET_EXHAUSTED, explanation="No proof yet."),
    )
    fn = FunctionResult(
        qualname="mod.clamp",
        file=str(tmp_path / "mod.py"),
        sha256="0" * 64,
        line=1,
        properties=[refuted, example, gap],
    )
    io = FunctionResult(
        qualname="mod.log",
        file=str(tmp_path / "mod.py"),
        sha256="1" * 64,
        line=9,
        gap=Gap(
            reason_code=ReasonCode.IMPURE_IO,
            explanation="prints",
            next_steps=["Move the computation into a pure function."],
        ),
    )
    return Report(apex_version="0.1.0.dev0", mode="engine", functions=[fn, io], exit_code=1)


def test_sarif_is_valid_2_1_0_and_keeps_verdicts_verbatim(tmp_path: Path) -> None:
    doc = to_sarif(_report(tmp_path), tmp_path)
    schema = json.loads((FIXTURES / "sarif-2.1.0.schema.json").read_text())
    jsonschema.validate(doc, schema)
    results = doc["runs"][0]["results"]
    assert [r["ruleId"] for r in results] == ["refuted", "refuted", "unproven", "out_of_scope"]
    assert {r["ruleId"] for r in results} <= {v.value for v in Verdict}
    refuted = results[0]
    assert refuted["level"] == "error" and refuted["kind"] == "fail"
    loc = refuted["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "mod.py" and loc["region"]["startLine"] == 1
    assert results[-1]["kind"] == "notApplicable" and results[-1]["level"] == "none"


def test_suggest_writes_regression_tests_that_fail_while_the_bug_remains(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        textwrap.dedent(
            """
            def clamp(x: int, lo: int, hi: int) -> int:
                if x < lo:
                    return lo
                if x >= hi:
                    return hi - 1
                return x
            """
        )
    )
    report = _report(tmp_path)
    advice = advise(report, tmp_path, "suggest")
    snippets = {a.property: a.snippet for a in advice.items if a.snippet}
    assert set(snippets) == {"P1", "E1"}
    assert "assert (not (lo <= hi)) or (lo <= r <= hi)" in (snippets["P1"] or "")
    for pid, snippet in snippets.items():
        assert snippet is not None
        test_file = tmp_path / f"test_{pid}.py"
        test_file.write_text(snippet)
        namespace: dict[str, Any] = {}
        sys.path.insert(0, str(tmp_path))
        try:
            exec(compile(snippet, str(test_file), "exec"), namespace)
            test = next(v for k, v in namespace.items() if k.startswith("test_"))
            with pytest.raises(AssertionError):
                test()
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("mod", None)
    # Every non-proven property gets a next step; the out-of-scope function its own entry.
    assert {a.property for a in advice.items} == {"P1", "E1", "P2", None}
    assert all(a.suggestions for a in advice.items)


def test_explain_states_the_verdict_and_never_a_softer_word(tmp_path: Path) -> None:
    advice = advise(_report(tmp_path), tmp_path, "explain", only="clamp", properties=["P1"])
    (item,) = advice.items
    assert item.verdict is Verdict.REFUTED and item.target == "mod.py::clamp"
    assert "clamp(x=0, lo=0, hi=0) returns -1" in item.explanation
    fn = _report(tmp_path).functions[0]
    assert regression_test(fn, fn.properties[2], None) is None  # only counterexamples get one


def test_config_templates_are_valid_configs() -> None:
    import tomllib

    from apex.cli.app import CONFIG_TEMPLATE
    from apex.core.config import ApexConfig

    skill = Path(__file__).parents[1] / "skill" / "apex-verify" / "assets" / "apex.toml.template"
    for text in (CONFIG_TEMPLATE, skill.read_text()):
        ApexConfig.model_validate(tomllib.loads(text)["apex"])
