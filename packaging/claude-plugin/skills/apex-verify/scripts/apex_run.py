"""Run an Apex verb and print only what an agent needs; the full JSON goes to a file.

    python scripts/apex_run.py verify mathx.py::clamp
    python scripts/apex_run.py refute mathx.py::clamp

Prints the verdict table plus the first actionable line per property (or a trimmed result for
verbs without a report) and exits with Apex's exit code (0 proven, 1 failure, 2 gaps, 3 tool
error). The complete output is saved to `.apex/last_<verb>.json`; reports are also in
`.apex/last_report.json`. Read those files when the summary is not enough.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPORT_VERBS = {"verify", "prove", "check", "report"}
MAX_STR = 400
MAX_ITEMS = 20


def find_apex() -> str | None:
    """`APEX_BIN`, then PATH, then uv's default tool directory.

    A harness that runs commands in a login shell may rebuild PATH from the user's profile; the
    environment variable and the uv location still find an installed Apex.
    """
    if (env := os.environ.get("APEX_BIN")) and Path(env).is_file():
        return env
    if found := shutil.which("apex"):
        return found
    local = Path.home() / ".local" / "bin" / ("apex.exe" if os.name == "nt" else "apex")
    return str(local) if local.is_file() else None


def _trim(value: object) -> object:
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + " ..."
    if isinstance(value, list):
        items = [_trim(v) for v in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"... {len(value) - MAX_ITEMS} more")
        return items
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items() if v not in (None, [], {})}
    return value


def _first_line(p: dict) -> str:
    v = p["verdict"]
    if v == "proven":
        return "approved and proven"
    if v == "proven_unreviewed":
        return f"proven; statement not approved (user runs `apex review ... --approve {p['id']}`)"
    if cx := p.get("counterexample"):
        args = ", ".join(f"{k}={x}" for k, x in cx["inputs"].items())
        return f"input ({args}): expected {cx.get('expected')}; {cx.get('actual')}"
    for err in p.get("lean_errors") or []:
        goal = f" | goal: {err['goal']}" if err.get("goal") else ""
        return f"Lean {err.get('file')}:{err.get('line')}: {err['message'].splitlines()[0]}{goal}"
    if gap := p.get("gap"):
        return f"{gap['reason_code']}: {gap['explanation'].splitlines()[0]}"
    return ""


def _table(report: dict) -> str:
    rows = ["| function | property | verdict | detail |", "|---|---|---|---|"]
    for f in report["functions"]:
        if not f["properties"] and f.get("gap"):
            g = f["gap"]
            rows.append(
                f"| `{f['qualname']}` | - | `gap` | {g['reason_code']}: {g['explanation']} |"
            )
        for p in f["properties"]:
            detail = _first_line(p).replace("|", "\\|")
            rows.append(f"| `{f['qualname']}` | {p['id']} | `{p['verdict']}` | {detail} |")
    meaning = {0: "every approved property proven", 1: "failures", 2: "gaps only", 3: "tool error"}
    rows.append(f"\nexit {report['exit_code']}: {meaning.get(report['exit_code'], '?')}")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 3
    apex = find_apex()
    if apex is None:
        print("Apex is not installed; run scripts/ensure_apex.py")
        return 3
    args = [a for a in argv if a not in ("--json", "--markdown")]
    out = subprocess.run([apex, *args, "--json"], capture_output=True, text=True)
    verb = args[0].replace(" ", "_")
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        print((out.stderr or out.stdout).strip()[-2000:])
        return out.returncode
    Path(".apex").mkdir(exist_ok=True)
    Path(f".apex/last_{verb}.json").write_text(json.dumps(data, indent=2))
    if "error" in data and len(data) == 1:
        print(f"tool error: {data['error']}")
    elif verb in REPORT_VERBS and "functions" in data:
        print(_table(data))
    else:
        print(json.dumps(_trim(data), indent=2))
    print(f"(full output: .apex/last_{verb}.json)")
    return out.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
