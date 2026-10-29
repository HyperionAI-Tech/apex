"""Offline mirror of `skills-ref validate` (CI also runs the real validator).

Rules from the Agent Skills specification: name matches the folder, lowercase letters, digits and
single hyphens, at most 64 chars; description at most 1,024 chars; compatibility at most 500;
only spec fields in the shared frontmatter, harness-specific metadata goes in sidecars.
"""

import re
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1] / "skill" / "apex-verify"
ALLOWED_FIELDS = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _frontmatter() -> tuple[dict, str]:
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert text.startswith("---\n")
    raw, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(raw), body


def test_frontmatter_fields_are_spec_only() -> None:
    meta, _ = _frontmatter()
    assert set(meta) <= ALLOWED_FIELDS
    assert {"name", "description"} <= set(meta)


def test_name_rules() -> None:
    meta, _ = _frontmatter()
    assert meta["name"] == SKILL_DIR.name
    assert NAME_RE.match(meta["name"]) and len(meta["name"]) <= 64


def test_length_limits() -> None:
    meta, _ = _frontmatter()
    assert 0 < len(meta["description"]) <= 1024
    assert len(meta.get("compatibility", "")) <= 500


def test_body_stays_small_and_harness_neutral() -> None:
    _, body = _frontmatter()
    assert len(body.splitlines()) < 500
    # Portability rule: refer to "a shell", never one harness's tool names.
    for harness_term in ("Bash tool", "run_shell_command", "Claude Code", "Codex", "Cursor"):
        assert harness_term not in body


def test_min_version_metadata_present() -> None:
    meta, _ = _frontmatter()
    assert meta["metadata"]["apex-min-version"]
