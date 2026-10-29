"""M3: Apex installs with `uv tool install` and keeps its exit-code contract once installed.

Builds the wheel, installs it into a throwaway tool directory and drives the installed `apex`
binary, with an empty APEX_HOME so nothing from this checkout's Lean project leaks in.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals" / "corpus"

pytestmark = pytest.mark.install


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    tmp = tmp_path_factory.mktemp("install")
    dist = tmp / "dist"
    subprocess.run(
        [uv, "build", "--wheel", "-o", str(dist)], cwd=ROOT, check=True, capture_output=True
    )
    wheel = next(dist.glob("*.whl"))
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tmp / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp / "bin"),
        "APEX_HOME": str(tmp / "home"),
    }
    env.pop("APEX_LEAN_PROJECT", None)
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(
        [
            uv,
            "tool",
            "install",
            "--python",
            f"{sys.version_info[0]}.{sys.version_info[1]}",
            str(wheel),
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    exe = tmp / "bin" / ("apex.exe" if os.name == "nt" else "apex")
    assert exe.is_file()
    work = tmp / "work"
    work.mkdir()
    for name in ("mathx.py", "mathx_bugs.py", "mathx_bugs.spec.md"):
        shutil.copy(CORPUS / name, work / name)
    return exe, {**env, "PWD": str(work)}


def run(installed: tuple[Path, dict[str, str]], *args: str) -> subprocess.CompletedProcess:
    exe, env = installed
    return subprocess.run(
        [str(exe), *args], cwd=env["PWD"], env=env, capture_output=True, text=True, timeout=300
    )


def test_installed_binary_reports_its_version(installed: tuple[Path, dict[str, str]]) -> None:
    from apex import __version__

    out = run(installed, "--version")
    assert out.returncode == 0 and out.stdout.strip() == __version__


def test_installed_package_ships_the_pinned_lean_project(
    installed: tuple[Path, dict[str, str]],
) -> None:
    out = run(installed, "doctor", "--json")
    assert out.returncode == 3  # Lean is not set up under the empty APEX_HOME
    checks = {c["name"]: c for c in json.loads(out.stdout)["checks"]}
    assert checks["lean_project"]["status"] == "fail"
    assert "apex init" in checks["lean_project"]["detail"]


def test_installed_exit_codes(installed: tuple[Path, dict[str, str]]) -> None:
    assert run(installed, "refute", "mathx_bugs.py::sign").returncode == 1
    assert run(installed, "refute", "mathx.py::sign").returncode == 0
    assert run(installed, "--bogus").returncode == 3
    out = run(installed, "verify", "mathx.py::clamp", "--json")
    assert out.returncode == 3 and "apex init" in json.loads(out.stdout)["error"]


def test_installed_package_installs_its_skill(installed: tuple[Path, dict[str, str]]) -> None:
    _, env = installed
    out = run(installed, "skill", "install", "--harness", "claude", "--harness", "codex", "--json")
    assert out.returncode == 0, out.stderr
    work = Path(env["PWD"])
    skill = work / ".agents" / "skills" / "apex-verify"
    assert (skill / "SKILL.md").is_file() and (skill / "scripts" / "apex_run.py").is_file()
    assert (work / ".claude" / "skills" / "apex-verify" / "SKILL.md").is_file()
