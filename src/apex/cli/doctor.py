"""`apex doctor`: a JSON-able health report of everything the pipeline depends on."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from apex import __version__
from apex.lean.project import LeanProject, LeanUnavailable, find_elan, find_lake

Status = Literal["ok", "warn", "fail"]


class Check(BaseModel):
    name: str
    status: Status
    detail: str


class DoctorReport(BaseModel):
    schema_version: Literal["1"] = "1"
    apex_version: str
    healthy: bool
    """True when nothing failed. Warnings do not block the deterministic verbs."""
    checks: list[Check]


def _cmd_version(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else None


def _skill_locations(cwd: Path) -> list[str]:
    dirs = [
        ".agents/skills",
        ".claude/skills",
        ".gemini/skills",
        ".cursor/skills",
        ".github/skills",
    ]
    found = []
    for base in (cwd, Path.home()):
        for d in dirs:
            if (base / d / "apex-verify" / "SKILL.md").is_file():
                found.append(str(base / d / "apex-verify"))
    return found


def run_doctor(cwd: Path | None = None, *, warm_repl: bool = False) -> DoctorReport:
    cwd = cwd or Path.cwd()
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 12)
    checks.append(
        Check(name="python", status="ok" if py_ok else "fail", detail=sys.version.split()[0])
    )

    elan = find_elan()
    checks.append(
        Check(
            name="elan",
            status="ok" if elan else "fail",
            detail=str(elan) if elan else "not found; run `apex init`",
        )
    )
    lake = find_lake()
    checks.append(
        Check(
            name="lake",
            status="ok" if lake else "fail",
            detail=str(lake) if lake else "not found; run `apex init`",
        )
    )

    try:
        project: LeanProject | None = LeanProject.locate()
        assert project is not None
        if project.is_bundle:
            checks.append(
                Check(
                    name="lean_project",
                    status="fail",
                    detail="not set up; run `apex init` (installs into "
                    f"{LeanProject.installed_for(project.root).root})",
                )
            )
        else:
            checks.append(Check(name="lean_project", status="ok", detail=str(project.root)))
    except LeanUnavailable as e:
        project = None
        checks.append(Check(name="lean_project", status="fail", detail=str(e)))

    if project is not None:
        pinned = project.toolchain
        # The same lean binary and environment the verdict compiler uses, so this checks what
        # verdicts actually run on (and elan resolves the pinned toolchain, not the global one).
        try:
            lean, _ = project.lean_command()
            installed = _cmd_version([str(lean), "--version"], project.root)
        except LeanUnavailable:
            installed = None
        tag = pinned.split(":")[-1].lstrip("v")
        lean_ok = installed is not None and tag in installed
        checks.append(
            Check(
                name="lean_toolchain",
                status="ok" if lean_ok else "fail",
                detail=f"pinned {pinned}; found {installed or 'none'}",
            )
        )
        rev = project.mathlib_rev
        checks.append(
            Check(
                name="mathlib",
                status="ok" if rev and project.mathlib_dir.is_dir() else "fail",
                detail=f"rev {rev}" if rev else "not resolved; run `apex init`",
            )
        )
        checks.append(
            Check(
                name="mathlib_cache",
                status="ok" if project.mathlib_cache_present else "fail",
                detail="present"
                if project.mathlib_cache_present
                else "missing; run `apex init` (fetches prebuilt oleans with `lake exe cache get`)",
            )
        )
        checks.append(
            Check(
                name="apex_prelude",
                status="ok" if project.prelude_built else "warn",
                detail="built"
                if project.prelude_built
                else "not built; `apex init` runs `lake build`",
            )
        )

    pyright = shutil.which("pyright")
    checks.append(
        Check(
            name="pyright",
            status="ok" if pyright else "warn",
            detail=pyright or "not found; type facts will be unavailable to analyze",
        )
    )

    for mod, purpose in [
        ("lean_interact", "warm REPL pool"),
        ("hypothesis", "fidelity bridge"),
        ("crosshair", "refutation"),
    ]:
        present = importlib.util.find_spec(mod) is not None
        checks.append(
            Check(
                name=mod,
                status="ok" if present else "warn",
                detail=("installed" if present else "not installed") + f" ({purpose})",
            )
        )

    present = importlib.util.find_spec("atheris") is not None
    checks.append(
        Check(
            name="atheris",
            status="ok" if present else "warn",
            detail="installed (coverage-guided fuzzing)"
            if present
            else "not installed; optional (`apex fuzz --engine atheris`)",
        )
    )
    checks.append(_tla_check())
    checks += _js_checks()
    checks.append(_llm_check(cwd))

    skills = _skill_locations(cwd)
    checks.append(
        Check(
            name="skill",
            status="ok" if skills else "warn",
            detail=", ".join(skills)
            if skills
            else "apex-verify not installed in any harness (`apex skill install`)",
        )
    )

    if warm_repl and project is not None and project.prelude_built:
        checks.append(_repl_warmup(project))

    return DoctorReport(
        apex_version=__version__, healthy=all(c.status != "fail" for c in checks), checks=checks
    )


def _js_checks() -> list[Check]:
    """Node runs JavaScript / TypeScript targets; Jazzer.js fuzzes them. Both optional."""
    import subprocess

    from apex.js.jazzer import JAZZER_VERSION, JazzerUnavailable, jazzer_bin

    node = shutil.which("node")
    if node is None:
        out = [Check(name="node", status="warn", detail="not found; needed for JS/TS targets")]
    else:
        version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
        major = int(version.strip().lstrip("v").split(".")[0] or 0)
        ok = major >= 22
        detail = f"{version.strip()} at {node}" + ("" if ok else "; JS/TS needs Node 22.6+")
        out = [Check(name="node", status="ok" if ok else "warn", detail=detail)]
    try:
        detail = f"Jazzer.js {JAZZER_VERSION} at {jazzer_bin()}"
        out.append(Check(name="jazzer", status="ok", detail=detail))
    except JazzerUnavailable:
        out.append(
            Check(name="jazzer", status="warn", detail="optional (JS fuzzing): `apex init --js`")
        )
    return out


def _tla_check() -> Check:
    """TLC for concurrent code. Optional: only `apex tla` needs it."""
    from apex.tla.tlc import TLA_VERSION, TlaUnavailable, tools

    try:
        _, jar = tools()
    except TlaUnavailable as e:
        return Check(name="tla_tools", status="warn", detail=f"optional (apex tla): {e}")
    return Check(name="tla_tools", status="ok", detail=f"tla2tools.jar v{TLA_VERSION} at {jar}")


def _llm_check(cwd: Path) -> Check:
    """Which ways of reaching a model are available. Optional: every verb works without one."""
    from apex.core.config import ApexConfig
    from apex.llm.providers import api_keys_present, available_agent_clis

    config, _ = ApexConfig.discover(cwd.resolve())
    found = [n for n, path in available_agent_clis().items() if path]
    found += [f"{n} (API key)" for n, ok in api_keys_present().items() if ok]
    configured = config.model_provider
    detail = f"configured: {configured}; available: {', '.join(found) or 'none'}"
    usable = configured in ("none", "host") or any(f.startswith(configured) for f in found)
    return Check(name="llm_provider", status="ok" if usable else "warn", detail=detail)


def _repl_warmup(project: LeanProject) -> Check:
    try:
        from apex.lean.repl import ReplPool

        pool = ReplPool(size=1, project=project)
        try:
            res = pool.check("example : 1 + 1 = 2 := by decide")
        finally:
            pool.close()
        return Check(
            name="repl_warmup",
            status="ok" if res.ok else "fail",
            detail=f"warm-up {pool.warmup_s[0]:.1f}s, query {res.duration_s:.2f}s",
        )
    except Exception as e:  # report, never crash the doctor
        return Check(name="repl_warmup", status="warn", detail=f"{type(e).__name__}: {e}")
