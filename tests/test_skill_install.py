"""`apex skill install` / `apex mcp install`: one skill copy, merged configs, nothing clobbered."""

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

from apex.skill.install import MARKER, InstallError, install_mcp, install_skill

SKILL = Path(__file__).resolve().parents[1] / "skill" / "apex-verify"
CMD = ["/opt/bin/apex", "mcp"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def test_one_copy_in_agents_linked_from_claude(tmp_path: Path, home: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    res = install_skill(base=proj)
    shared = proj / ".agents" / "skills" / "apex-verify"
    link = proj / ".claude" / "skills" / "apex-verify"
    assert (shared / "SKILL.md").read_text() == (SKILL / "SKILL.md").read_text()
    assert (shared / MARKER).is_file() and (shared / "scripts" / "apex_run.py").is_file()
    assert link.is_symlink() and link.resolve() == shared.resolve()
    assert res.written == [str(shared), str(link)]
    again = install_skill(base=proj)  # idempotent: the copy refreshes, the link stays
    assert str(link) in again.unchanged
    assert not (home / ".agents").exists()  # project install never touches the home directory


def test_claude_only_install_is_a_real_copy(tmp_path: Path, home: Path) -> None:
    install_skill(["claude"], base=tmp_path)
    dst = tmp_path / ".claude" / "skills" / "apex-verify"
    assert dst.is_dir() and not dst.is_symlink() and not (tmp_path / ".agents").exists()


def test_refuses_to_replace_a_skill_apex_did_not_install(tmp_path: Path, home: Path) -> None:
    foreign = tmp_path / ".agents" / "skills" / "apex-verify"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: apex-verify\ndescription: someone else's\n---\n")
    with pytest.raises(InstallError, match="not Apex's"):
        install_skill(base=tmp_path)
    assert "someone else's" in (foreign / "SKILL.md").read_text()


def test_user_scope_and_harnesses_without_one(tmp_path: Path, home: Path) -> None:
    install_skill(["codex", "claude"], user=True, base=tmp_path)
    assert (home / ".agents" / "skills" / "apex-verify" / "SKILL.md").is_file()
    assert (home / ".claude" / "skills" / "apex-verify").is_symlink()
    with pytest.raises(InstallError, match="project only"):
        install_skill(["cursor"], user=True, base=tmp_path)


def test_mcp_json_configs_are_merged_never_overwritten(tmp_path: Path, home: Path) -> None:
    mcp = tmp_path / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}))
    res = install_mcp(["claude", "cursor", "gemini", "vscode"], base=tmp_path, command=CMD)
    data = json.loads(mcp.read_text())
    assert data["keep"] == 1 and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["apex"] == {"command": "/opt/bin/apex", "args": ["mcp"]}
    vscode = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert vscode["servers"]["apex"]["type"] == "stdio"
    assert (tmp_path / ".cursor" / "mcp.json").is_file()
    assert (tmp_path / ".gemini" / "settings.json").is_file()
    assert len(res.written) == 4
    assert install_mcp(["claude"], base=tmp_path, command=CMD).unchanged == [str(mcp)]


def test_a_foreign_apex_server_or_broken_config_is_left_alone(tmp_path: Path, home: Path) -> None:
    mcp = tmp_path / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"apex": {"command": "node", "args": ["apex.js"]}}}))
    res = install_mcp(["claude"], base=tmp_path, command=CMD)
    assert res.refused and json.loads(mcp.read_text())["mcpServers"]["apex"]["command"] == "node"
    broken = tmp_path / ".cursor" / "mcp.json"
    broken.parent.mkdir()
    broken.write_text("{not json")
    assert install_mcp(["cursor"], base=tmp_path, command=CMD).refused
    assert broken.read_text() == "{not json"
    # An older Apex entry (another path to `apex mcp`) is updated in place.
    mcp.write_text(json.dumps({"mcpServers": {"apex": {"command": "apex", "args": ["mcp"]}}}))
    assert install_mcp(["claude"], base=tmp_path, command=CMD).written == [str(mcp)]


def test_codex_toml_is_appended_and_comments_survive(tmp_path: Path, home: Path) -> None:
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('# my settings\nmodel = "x"\n\n[mcp_servers.docs]\nurl = "https://d"\n')
    install_mcp(["codex"], base=tmp_path, command=CMD)
    text = cfg.read_text()
    assert text.startswith("# my settings\n")
    data = tomllib.loads(text)
    assert data["mcp_servers"]["apex"] == {"command": "/opt/bin/apex", "args": ["mcp"]}
    assert data["mcp_servers"]["docs"] == {"url": "https://d"}
    assert install_mcp(["codex"], base=tmp_path, command=CMD).unchanged == [str(cfg)]
    other = install_mcp(["codex"], base=tmp_path, command=["apex2", "serve"])
    assert other.refused and tomllib.loads(cfg.read_text())["mcp_servers"]["apex"]["args"] == [
        "mcp"
    ]


# ---- the skill's own scripts ------------------------------------------------------------------


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_apex_run_prints_the_table_with_the_first_actionable_line() -> None:
    run = _script("apex_run")
    report = {
        "exit_code": 1,
        "functions": [
            {
                "qualname": "m.clamp",
                "properties": [
                    {
                        "id": "P1",
                        "verdict": "refuted",
                        "counterexample": {
                            "inputs": {"x": "0"},
                            "expected": "r >= 0",
                            "actual": "returns -1",
                        },
                    },
                    {
                        "id": "P2",
                        "verdict": "unproven",
                        "lean_errors": [
                            {
                                "file": "Spec.lean",
                                "line": 7,
                                "message": "unsolved goals\n...",
                                "goal": "x : Int ⊢ 0 ≤ x",
                            }
                        ],
                        "gap": {"reason_code": "PROOF_REJECTED", "explanation": "rejected"},
                    },
                ],
            },
            {
                "qualname": "m.io",
                "properties": [],
                "gap": {"reason_code": "IMPURE_IO", "explanation": "prints"},
            },
        ],
    }
    table = run._table(report)
    assert "| `m.clamp` | P1 | `refuted` | input (x=0): expected r >= 0; returns -1 |" in table
    assert "Lean Spec.lean:7: unsolved goals | goal: x : Int ⊢ 0 ≤ x" in table.replace("\\|", "|")
    assert "IMPURE_IO" in table and "exit 1: failures" in table


def test_ensure_apex_compares_release_versions() -> None:
    ensure = _script("ensure_apex")
    assert ensure._release("0.1.0.dev0") == (0, 1, 0)
    assert ensure._release("0.2.0") > ensure._release("0.1.9")
    assert ensure.min_version() == "0.1.0"


def test_packaging_is_generated_from_the_canonical_skill(tmp_path: Path) -> None:
    from apex.skill.packaging import build

    committed = SKILL.parents[1] / "packaging"
    fresh = tmp_path / "packaging"
    files = build(fresh)
    rel = {p.relative_to(fresh) for p in files}
    assert rel == {p.relative_to(committed) for p in committed.rglob("*") if p.is_file()}, (
        "packaging/ is stale: run `uv run python -m apex.skill.packaging`"
    )
    for r in rel:
        assert (committed / r).read_bytes() == (fresh / r).read_bytes(), r
    plugin = json.loads((fresh / "claude-plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "apex-verify"
