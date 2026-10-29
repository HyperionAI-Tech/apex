"""The exit-code contract, through the real entry point (`python -m apex`), without Lean.

0 every approved property proven; 1 refuted / fidelity_failed / contradiction; 2 gaps only;
3 tool error. Usage errors and crashes must be 3: Click and Python would otherwise exit 2 and 1,
which CI would read as "gaps" and "refuted".
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from apex.core.schema import CLI_OUTPUTS, CONTRACTS

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"


def apex(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "apex", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=300,
    )


def _schema(command: str) -> dict:
    return CONTRACTS[CLI_OUTPUTS[command]].model_json_schema(by_alias=True)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    for name in ("mathx.py", "mathx_bugs.py", "impure.py", "mathx.spec.md", "mathx_bugs.spec.md"):
        (tmp_path / name).write_text((CORPUS / name).read_text())
    return tmp_path


@pytest.mark.parametrize(
    "args",
    [
        ["--no-such-option"],
        ["verify"],  # missing argument
        ["refute", "nope.py::f"],
        ["refute", "mathx.py"],  # not a path.py::name target
        ["analyze", "mathx.py::missing"],
        ["refute", "impure.py::logged_clamp"],  # impure: refusing to run side effects
    ],
)
def test_usage_and_target_errors_exit_3(work: Path, args: list[str]) -> None:
    assert apex(*args, cwd=work).returncode == 3


def test_tool_errors_print_the_published_error_schema(work: Path) -> None:
    out = apex("refute", "nope.py::f", "--json", cwd=work)
    assert out.returncode == 3
    jsonschema.validate(json.loads(out.stdout), CONTRACTS["tool_error"].model_json_schema())


def test_invalid_config_is_a_tool_error(work: Path) -> None:
    (work / "apex.toml").write_text('[apex]\nmodel_provider = "telepathy"\n')
    out = apex("refute", "mathx.py::clamp", cwd=work)
    assert out.returncode == 3 and "apex.toml" in out.stderr
    (work / "apex.toml").write_text("[apex\n")
    assert apex("analyze", "mathx.py::clamp", cwd=work).returncode == 3


def test_refute_exits_1_on_a_counterexample_and_0_without(work: Path) -> None:
    bad = apex("refute", "mathx_bugs.py::sign", "--json", cwd=work)
    assert bad.returncode == 1
    jsonschema.validate(json.loads(bad.stdout), _schema("refute"))
    assert apex("refute", "mathx.py::sign", cwd=work).returncode == 0


def test_missing_lean_is_a_tool_error_for_every_lean_verb(work: Path, tmp_path: Path) -> None:
    fake = tmp_path / "fake-lean"
    fake.mkdir()
    (fake / "lakefile.toml").write_text('name = "x"\n')
    (fake / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
    (fake / "lake-manifest.json").write_text(
        '{"packages": [{"name": "mathlib", "rev": "abc"}]}\n'
    )
    env = {"APEX_LEAN_PROJECT": str(fake)}
    for args in (
        ["verify", "mathx.py::clamp"],
        ["scaffold", "mathx.py::clamp"],
        ["prove", "mathx.py::clamp", "--tactics-only"],
        ["check", ".", "--no-llm"],
    ):
        out = apex(*args, "--json", cwd=work, env=env)
        assert out.returncode == 3, (args, out.stdout, out.stderr)
        assert json.loads(out.stdout)["error"]


def test_engine_steps_without_a_provider_are_a_tool_error_with_a_way_out(work: Path) -> None:
    out = apex("prove", "mathx.py::clamp", cwd=work)
    assert out.returncode == 3 and "--tactics-only" in out.stderr
    out = apex("check", ".", "--provider", "telepathy", cwd=work)
    assert out.returncode == 3


def test_analyze_and_review_json_follow_their_schemas(work: Path) -> None:
    out = apex("analyze", "mathx.py::clamp", "--json", cwd=work)
    assert out.returncode == 0
    jsonschema.validate(json.loads(out.stdout), _schema("analyze"))
    (work / "spec.lean").write_text("theorem P1 (x : Int) : x = x := by rfl\n")
    out = apex("review", "mathx.py::clamp", "--spec", "spec.lean", "--json", cwd=work)
    assert out.returncode == 0
    status = json.loads(out.stdout)
    jsonschema.validate(status, _schema("review"))
    assert status["statements"][0]["status"] == "pending"


def test_approval_needs_a_terminal(work: Path) -> None:
    (work / "spec.lean").write_text("theorem P1 (x : Int) : x = x := by rfl\n")
    out = apex("review", "mathx.py::clamp", "--spec", "spec.lean", "--approve", "P1", cwd=work)
    assert out.returncode == 3 and "interactive terminal" in out.stderr
    assert not (work / ".apex" / "reviews.json").exists()


def test_every_command_has_a_published_output_schema() -> None:
    from apex.cli.app import app

    def names(typer_app, prefix: str = "") -> set[str]:
        out = {prefix + (c.name or c.callback.__name__) for c in typer_app.registered_commands}
        for g in typer_app.registered_groups:
            if not g.hidden:
                out |= names(g.typer_instance, prefix + f"{g.name} ")
        return out

    commands = {n.replace("_", "-") for n in names(app)} - {"dev compile"}
    assert commands <= set(CLI_OUTPUTS), commands - set(CLI_OUTPUTS)
    assert all(CLI_OUTPUTS[c] in CONTRACTS for c in CLI_OUTPUTS)
