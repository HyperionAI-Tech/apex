"""The MCP server: tools mirror the deterministic verbs, and none of them can approve."""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from apex.lean.project import LeanProject
from apex.mcp.server import build_server

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
TOOLS = {
    "apex_analyze",
    "apex_intent_submit",
    "apex_scaffold",
    "apex_prove",
    "apex_verify",
    "apex_refute",
    "apex_check",
    "apex_explain",
    "apex_suggest",
    "apex_review_status",
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("mathx.py", "mathx_bugs.py", "mathx_bugs.spec.md"):
        shutil.copy(CORPUS / name, tmp_path / name)
    (tmp_path / "apex.toml").write_text("[apex]\nrefute_budget_s = 2\n")
    monkeypatch.chdir(tmp_path)  # the server works on the project it was started in
    return tmp_path


def call(*calls: tuple[str, dict[str, Any]]) -> list[Any]:
    async def go() -> list[Any]:
        async with Client(build_server()) as c:
            return [await c.call_tool(name, args) for name, args in calls]

    return asyncio.run(go())


def test_tools_mirror_the_verbs_and_none_can_approve() -> None:
    async def go() -> list[Any]:
        async with Client(build_server()) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(go())
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert "approve" not in t.name
        assert "approve" not in json.dumps(t.input_schema)  # no approval argument anywhere


def test_refute_returns_the_counterexample_as_structured_content(project: Path) -> None:
    (res,) = call(("apex_refute", {"target": "mathx_bugs.py::sign"}))
    assert not res.is_error
    refuted = [p for p in res.structured_content["properties"] if p["refuted"]]
    assert refuted and refuted[0]["counterexample"]["inputs"]


def test_tool_errors_come_back_as_readable_results(project: Path) -> None:
    bad, host_check = call(
        ("apex_refute", {"target": "nope.py::f"}),
        ("apex_check", {"paths": ["."]}),
    )
    assert bad.is_error and "no such file" in bad.content[0].text
    assert host_check.is_error and "engine mode" in host_check.content[0].text


def test_intent_submit_checks_hints_against_the_signature(project: Path) -> None:
    proposals = {
        "proposed_by": "host",
        "properties": [
            {"text": "never negative", "formal_hint": "r >= 0", "basis": "docstring"},
            {"text": "nonsense", "formal_hint": "r.bit_length() > 0", "basis": "docstring"},
        ],
        "preconditions": [],
    }
    (res,) = call(("apex_intent_submit", {"target": "mathx.py::my_abs", "proposals": proposals}))
    check = res.structured_content
    assert len(check["accepted"]["properties"]) == 1 and len(check["rejected"]) == 1


def test_the_real_binary_serves_mcp_over_stdio(project: Path) -> None:
    async def go() -> set[str]:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "apex", "mcp"], cwd=str(project)
        )
        async with Client(params) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(go()) == TOOLS


@pytest.mark.lean
def test_host_mode_loop_over_mcp_with_lean_errors_on_spec_lines(
    project: Path, lean_project: LeanProject
) -> None:
    target = "mathx.py::count_positive"
    (mathx_spec := project / "mathx.spec.md").write_text((CORPUS / "mathx.spec.md").read_text())
    scaffolded, proved = call(
        ("apex_scaffold", {"target": target}), ("apex_prove", {"target": target})
    )
    assert not scaffolded.is_error and "P1" in proved.structured_content["open"]
    spec_path = Path(scaffolded.structured_content["spec_path"])
    text = spec_path.read_text()
    # The agent writes a wrong proof for P1 and submits the spec through the tool.
    broken = text.replace(
        "(count_positive xs) ≥ (0 : Int) := by\n  sorry",
        "(count_positive xs) ≥ (0 : Int) := by\n  omega",
        1,
    )
    assert broken != text
    (verified,) = call(("apex_verify", {"targets": [target], "spec_text": broken}))
    props = {p["id"]: p for p in verified.structured_content["functions"][0]["properties"]}
    p1 = props["P1"]
    assert p1["verdict"] == "unproven" and p1["lean_errors"]
    err = p1["lean_errors"][0]
    lines = broken.splitlines()
    p1_at = next(i for i, ln in enumerate(lines) if ln.startswith("theorem P1"))
    omega_line = next(i for i in range(p1_at, len(lines)) if lines[i].strip() == "omega") + 1
    # Positioned in the spec file the agent edits, on the failing tactic, with the goal state.
    assert err["file"] == str(spec_path) and err["line"] == omega_line
    assert err["goal"] or "omega" in err["message"]
    (status,) = call(("apex_review_status", {"target": target}))
    assert {s["status"] for s in status.structured_content["statements"]} == {"pending"}
    assert mathx_spec.is_file()
