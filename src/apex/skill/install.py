"""`apex skill install` and `apex mcp install`: put the canonical skill and the MCP entry where
each coding harness looks for them.

One copy of the skill goes to `.agents/skills/apex-verify` (read by Codex, Gemini CLI, Cursor and
VS Code / Copilot) and `.claude/skills/apex-verify` links to it, so a single install serves every
harness. MCP config files are merged, never overwritten: an existing `apex` entry that is not
Apex's own is reported and left alone, and every other key in the file is preserved.
"""

import json
import os
import shutil
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SKILL_NAME = "apex-verify"
MARKER = ".apex-skill"
"""Written into an installed skill folder; its presence is how Apex knows the folder is its own."""
HARNESSES = ("claude", "codex", "gemini", "cursor", "vscode")
Harness = Literal["claude", "codex", "gemini", "cursor", "vscode"]


class InstallError(RuntimeError):
    """Refused: would overwrite something that is not Apex's, or the harness has no such scope."""


class SkillInstallResult(BaseModel):
    written: list[str] = Field(default_factory=list)
    """Every file or link created or changed, in order."""
    unchanged: list[str] = Field(default_factory=list)
    refused: list[str] = Field(default_factory=list)
    """Things left alone because they are not Apex's (each with the reason)."""


def skill_source() -> Path:
    """The canonical skill folder: in a source checkout, or shipped inside the wheel."""
    here = Path(__file__).resolve()
    for candidate in (
        here.parents[3] / "skill" / SKILL_NAME,
        here.parents[1] / "_skill" / SKILL_NAME,
    ):
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise InstallError("the apex-verify skill folder is missing from this install")


def _skill_dirs(harnesses: Iterable[str], user: bool, base: Path) -> list[tuple[Path, bool]]:
    """(directory that should contain apex-verify, whether it is the shared copy)."""
    root = Path.home() if user else base
    agents = root / ".agents" / "skills"
    out: list[tuple[Path, bool]] = []
    hs = set(harnesses)
    if hs - {"claude"}:
        if user and hs & {"cursor", "vscode"}:
            raise InstallError(
                "Cursor and VS Code read skills from the project only; install without --user"
            )
        out.append((agents, True))
    if "claude" in hs:
        out.append((root / ".claude" / "skills", not out))
    return out


def _is_ours(path: Path) -> bool:
    return (path / MARKER).is_file() or (path.is_symlink() and (path.resolve() / MARKER).is_file())


def _copy_skill(src: Path, dst: Path, result: SkillInstallResult) -> None:
    if dst.is_symlink() or dst.exists():
        if not _is_ours(dst):
            raise InstallError(
                f"{dst} exists and is not Apex's skill; remove it or install elsewhere"
            )
        if dst.is_symlink():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    from apex import __version__

    (dst / MARKER).write_text(f"apex {__version__}\n")
    result.written.append(str(dst))


def _link_skill(target: Path, link: Path, result: SkillInstallResult) -> None:
    if link.is_symlink() and link.resolve() == target.resolve():
        result.unchanged.append(str(link))
        return
    if link.is_symlink() or link.exists():
        if not _is_ours(link):
            raise InstallError(
                f"{link} exists and is not Apex's skill; remove it or install elsewhere"
            )
        if link.is_symlink():
            link.unlink()
        else:
            shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
    except OSError:  # no symlink privilege (Windows without developer mode): copy instead
        shutil.copytree(target, link)
    result.written.append(str(link))


def install_skill(
    harnesses: Iterable[str] = HARNESSES,
    *,
    user: bool = False,
    base: Path | None = None,
    with_mcp: bool = False,
    command: list[str] | None = None,
) -> SkillInstallResult:
    base = (base or Path.cwd()).resolve()
    harnesses = list(dict.fromkeys(harnesses))
    if unknown := set(harnesses) - set(HARNESSES):
        raise InstallError(
            f"unknown harness {', '.join(sorted(unknown))}; choose from {', '.join(HARNESSES)}"
        )
    src = skill_source()
    result = SkillInstallResult()
    shared: Path | None = None
    for directory, is_shared in _skill_dirs(harnesses, user, base):
        dst = directory / SKILL_NAME
        directory.mkdir(parents=True, exist_ok=True)
        if is_shared:
            _copy_skill(src, dst, result)
            shared = dst
        else:
            assert shared is not None
            _link_skill(shared, dst, result)
    if with_mcp:
        mcp = install_mcp(harnesses, user=user, base=base, command=command)
        result.written += mcp.written
        result.unchanged += mcp.unchanged
        result.refused += mcp.refused
    return result


# ---- MCP config ------------------------------------------------------------------------------


def apex_command() -> list[str]:
    """How a harness should start the server: the absolute `apex` path when one is on PATH, so the
    harness does not depend on its own PATH, else plain `apex`."""
    exe = shutil.which("apex")
    return [str(Path(exe).resolve()) if exe else "apex", "mcp"]


def _mcp_target(harness: str, user: bool, base: Path) -> tuple[Path, str]:
    """(config file, format) for one harness."""
    home = Path.home()
    match harness:
        case "claude":
            return (
                (home / ".claude.json", "json:mcpServers")
                if user
                else (
                    base / ".mcp.json",
                    "json:mcpServers",
                )
            )
        case "codex":
            return home / ".codex" / "config.toml", "toml:mcp_servers"
        case "gemini":
            root = home if user else base
            return root / ".gemini" / "settings.json", "json:mcpServers"
        case "cursor":
            if user:
                return home / ".cursor" / "mcp.json", "json:mcpServers"
            return base / ".cursor" / "mcp.json", "json:mcpServers"
        case "vscode":
            if user:
                raise InstallError("VS Code MCP config is per workspace; install without --user")
            return base / ".vscode" / "mcp.json", "json:servers"
    raise InstallError(f"unknown harness {harness}")


def _entry(fmt: str, command: list[str]) -> dict:
    entry: dict = {"command": command[0], "args": command[1:]}
    if fmt == "json:servers":  # VS Code
        entry = {"type": "stdio", **entry}
    return entry


def _same_server(existing: object, command: list[str]) -> bool:
    if not isinstance(existing, dict):
        return False
    args = existing.get("args") or []
    return existing.get("command") == command[0] and list(args) == command[1:]


def _looks_like_apex(existing: object) -> bool:
    if not isinstance(existing, dict):
        return False
    cmd = str(existing.get("command", ""))
    return Path(cmd).name in ("apex", "apex.exe") and list(existing.get("args") or []) == ["mcp"]


def _merge_json(path: Path, key: str, command: list[str], result: SkillInstallResult) -> None:
    data: dict = {}
    if path.is_file():
        text = path.read_text()
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as e:
            result.refused.append(f"{path}: not valid JSON ({e}); not touched")
            return
        if not isinstance(data, dict):
            result.refused.append(f"{path}: top level is not an object; not touched")
            return
    servers = data.get(key)
    if servers is None:
        servers = data[key] = {}
    if not isinstance(servers, dict):
        result.refused.append(f"{path}: `{key}` is not an object; not touched")
        return
    current = servers.get("apex")
    if current is not None:
        if _same_server(current, command):
            result.unchanged.append(str(path))
            return
        if not _looks_like_apex(current):
            result.refused.append(f"{path}: an `apex` server that is not Apex's exists; left alone")
            return
    servers["apex"] = _entry(f"json:{key}", command)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".apex-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    result.written.append(str(path))


def _toml_value(v: object) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(v)  # a JSON string is a valid TOML basic string


def _merge_toml(path: Path, command: list[str], result: SkillInstallResult) -> None:
    """Append `[mcp_servers.apex]`; never rewrite the rest of the file (comments survive)."""
    text = path.read_text() if path.is_file() else ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        result.refused.append(f"{path}: not valid TOML ({e}); not touched")
        return
    current = (data.get("mcp_servers") or {}).get("apex")
    if current is not None:
        if _same_server(current, command):
            result.unchanged.append(str(path))
        else:
            result.refused.append(
                f"{path}: [mcp_servers.apex] exists with a different command; left alone"
            )
        return
    block = (
        "\n[mcp_servers.apex]\n"
        f"command = {_toml_value(command[0])}\n"
        f"args = {_toml_value(command[1:])}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if not text or text.endswith("\n") else "\n"
    path.write_text(text + sep + block)
    result.written.append(str(path))


def install_mcp(
    harnesses: Iterable[str] = HARNESSES,
    *,
    user: bool = False,
    base: Path | None = None,
    command: list[str] | None = None,
) -> SkillInstallResult:
    base = (base or Path.cwd()).resolve()
    command = command or apex_command()
    result = SkillInstallResult()
    for h in dict.fromkeys(harnesses):
        path, fmt = _mcp_target(h, user, base)
        if fmt.startswith("toml:"):
            _merge_toml(path, command, result)
        else:
            _merge_json(path, fmt.split(":", 1)[1], command, result)
    return result
