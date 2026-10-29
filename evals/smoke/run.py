"""Harness smoke matrix: the apex-verify skill, driven headlessly by real coding agents.

    uv run python evals/smoke/run.py --harness claude --harness codex [--scenario NAME] [--keep]

Each scenario gets a fresh project (a git repo, so harnesses scan it as one) with the skill
installed by `apex skill install`, and a natural request that never mentions Apex. Assertions are
on `.apex/last_report.json`, never on the agent's prose:

- `clamp`:       a correct clamp     -> some property `proven_unreviewed`, nothing refuted
- `off_by_one`:  the planted bug     -> a property `refuted` with a concrete counterexample
- `closed_loop`: the planted bug, "find it and fix it" -> the agent reads the counterexample,
                 edits the code, re-verifies: final report has nothing refuted and the bug is gone

Claude Code gets the MCP server too (project `.mcp.json`); Codex uses the CLI channel, so both
of the skill's channels are exercised without touching the user's global harness config.
Costs real model usage: every scenario is one agent session.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VENV_BIN = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")

SCENARIOS = {
    "clamp": ("clamp.py", "Formally verify the clamp function in clamp.py and tell me the result."),
    "off_by_one": (
        "clamp_bug.py",
        "Formally verify the clamp function in clamp_bug.py and tell me the result.",
    ),
    "closed_loop": (
        "clamp_bug.py",
        "I think the clamp function in clamp_bug.py has a bug. Find it with formal verification, "
        "fix the code, and verify again until nothing is refuted.",
    ),
}


@dataclass
class Outcome:
    harness: str
    scenario: str
    ok: bool = False
    seconds: float = 0.0
    verdicts: dict[str, str] = field(default_factory=dict)
    detail: str = ""
    channel: str = ""
    skill_activated: bool = False
    tools: list[str] = field(default_factory=list)
    """Claude Code: the tools the agent called, in order (from its stream-json transcript)."""
    workdir: str = ""


def _env() -> dict[str, str]:
    env = {**os.environ, "PATH": f"{VENV_BIN}{os.pathsep}{os.environ['PATH']}"}
    # Harnesses that run commands in a login shell rebuild PATH; APEX_BIN survives that.
    env["APEX_BIN"] = str(VENV_BIN / "apex")
    env.pop("VIRTUAL_ENV", None)
    return env


def _setup(harness: str, fixture: str, work: Path) -> None:
    shutil.copy(FIXTURES / fixture, work / fixture)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    args = [str(VENV_BIN / "apex"), "skill", "install", "--harness", harness]
    if harness == "claude":
        args.append("--with-mcp")  # project .mcp.json only
    subprocess.run(args, cwd=work, check=True, capture_output=True, env=_env())


def _agent(harness: str, prompt: str, work: Path, timeout_s: float) -> subprocess.CompletedProcess:
    if harness == "claude":
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Bash(apex:*)",
            "Bash(python:*)",
            "Bash(python3:*)",
            "Bash(uv:*)",
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "Skill",
            "mcp__apex",
            "--mcp-config",
            str(work / ".mcp.json"),
            "--no-session-persistence",
        ]
    else:
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--color",
            "never",
            "--ephemeral",
            "-C",
            str(work),
            prompt,
        ]
    return subprocess.run(
        cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s, env=_env()
    )


def _verdicts(work: Path, fn: str) -> dict[str, str]:
    path = work / ".apex" / "last_report.json"
    if not path.is_file():
        return {}
    report = json.loads(path.read_text())
    out = {}
    for f in report["functions"]:
        if f["qualname"].endswith(f".{fn}"):
            out |= {p["id"]: p["verdict"] for p in f["properties"]}
    return out


def _evidence(harness: str, transcript: str, o: Outcome) -> None:
    """Did the agent pick up the skill from a natural request, and how did it reach Apex?"""
    if harness == "claude":
        for line in transcript.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name, args = block["name"], block.get("input", {})
                    label = name
                    if name == "Bash":
                        label = "Bash: " + str(args.get("command", ""))[:60]
                    elif name == "Skill":
                        label = f"Skill: {args.get('skill') or args.get('command') or args}"
                    o.tools.append(label)
        o.skill_activated = any(
            t.startswith("Skill") and "apex-verify" in t for t in o.tools
        ) or any("SKILL.md" in t or "apex_run.py" in t for t in o.tools)
        if any(t.startswith("mcp__apex") for t in o.tools):
            o.channel = "mcp"
    else:
        o.skill_activated = "apex-verify" in transcript or "apex_run.py" in transcript


def _judge(scenario: str, work: Path, o: Outcome) -> None:
    module = SCENARIOS[scenario][0].removesuffix(".py")
    report = work / ".apex" / "last_report.json"
    o.verdicts = _verdicts(work, "clamp")
    # scripts/apex_run.py saves `.apex/last_<verb>.json`; Apex itself writes only last_report.json.
    by_script = [f for f in (work / ".apex").glob("last_*.json") if f.name != "last_report.json"]
    o.channel = "skill script" if by_script else "mcp or cli"
    if not report.is_file():
        o.detail = "no .apex/last_report.json: Apex never produced a report"
        return
    data = json.loads(report.read_text())
    cxs = [
        p.get("counterexample")
        for f in data["functions"]
        for p in f["properties"]
        if p["verdict"] == "refuted"
    ]
    vs = set(o.verdicts.values())
    if scenario == "clamp":
        o.ok = "proven_unreviewed" in vs and "refuted" not in vs and "proven" not in vs
        o.detail = "expects proven_unreviewed, nothing refuted"
    elif scenario == "off_by_one":
        o.ok = "refuted" in vs and all(c and c.get("inputs") for c in cxs)
        o.detail = "expects refuted with a counterexample"
    else:
        source = (work / f"{module}.py").read_text()
        fixed = "return hi - 1" not in source
        o.ok = fixed and "refuted" not in vs and "proven_unreviewed" in vs
        o.detail = f"code fixed: {fixed}; expects nothing refuted after the fix"


def run(harness: str, scenario: str, keep: bool, timeout_s: float) -> Outcome:
    o = Outcome(harness=harness, scenario=scenario)
    fixture, prompt = SCENARIOS[scenario]
    work = Path(tempfile.mkdtemp(prefix=f"apex-smoke-{harness}-{scenario}-"))
    o.workdir = str(work)
    started = time.monotonic()
    try:
        _setup(harness, fixture, work)
        res = _agent(harness, prompt, work, timeout_s)
        (work / "agent_output.txt").write_text(res.stdout + "\n--- stderr ---\n" + res.stderr)
        _judge(scenario, work, o)
        _evidence(harness, res.stdout + res.stderr, o)
        if not o.ok and res.returncode != 0:
            o.detail += f"; agent exited {res.returncode}"
    except subprocess.TimeoutExpired:
        _judge(scenario, work, o)
        o.ok = False
        o.detail = f"agent did not finish within {timeout_s:.0f}s; " + o.detail
    finally:
        o.seconds = round(time.monotonic() - started, 1)
        if not keep and o.ok:
            shutil.rmtree(work, ignore_errors=True)
    return o


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", action="append", choices=["claude", "codex"])
    parser.add_argument("--scenario", action="append", choices=list(SCENARIOS))
    parser.add_argument("--keep", action="store_true", help="Keep passing workdirs too.")
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    outcomes = [
        run(h, s, args.keep, args.timeout)
        for h in args.harness or ["claude", "codex"]
        for s in args.scenario or list(SCENARIOS)
    ]
    if args.json:
        print(json.dumps([asdict(o) for o in outcomes], indent=2))
    else:
        for o in outcomes:
            mark = "ok  " if o.ok else "FAIL"
            got = ", ".join(f"{k}={v}" for k, v in o.verdicts.items()) or "-"
            skill = "skill" if o.skill_activated else "no skill seen"
            print(
                f"{mark} {o.harness:7} {o.scenario:12} {o.seconds:7.1f}s  {got}  "
                f"[{o.channel}; {skill}]"
            )
            if not o.ok:
                print(f"       {o.detail}; workdir kept: {o.workdir}")
    return 0 if all(o.ok for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
