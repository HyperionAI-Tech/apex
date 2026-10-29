"""Check that Apex is installed, new enough, and has its Lean toolchain. Prints what to do if not.

    python scripts/ensure_apex.py

Exit 0 when Apex is ready, 1 when something must be installed or upgraded first (the message
says what), 3 when Apex is installed but its health check failed.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"
INSTALL_HINT = (
    "Apex is not installed (looked at $APEX_BIN, PATH and ~/.local/bin). Ask the user to install "
    "it with `uv tool install <apex package or wheel>` (see the Apex README for the current "
    "package name), or to set APEX_BIN to its path. Do not search the disk for other copies."
)


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


def _release(version: str) -> tuple[int, ...]:
    """The numeric release segment: `0.1.0.dev0` -> (0, 1, 0)."""
    m = re.match(r"(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def min_version() -> str:
    m = re.search(r'apex-min-version:\s*"?([\w.]+)"?', SKILL_MD.read_text())
    return m.group(1) if m else "0"


def main() -> int:
    apex = find_apex()
    if apex is None:
        print(INSTALL_HINT)
        return 1
    installed = subprocess.run([apex, "--version"], capture_output=True, text=True).stdout.strip()
    needed = min_version()
    if _release(installed) < _release(needed):
        print(
            f"Apex {installed} is older than this skill needs ({needed}). Upgrade it with "
            "`uv tool upgrade <apex package>` and run this script again."
        )
        return 1
    out = subprocess.run([apex, "doctor", "--json"], capture_output=True, text=True)
    try:
        report = json.loads(out.stdout)
    except json.JSONDecodeError:
        print(f"`apex doctor` failed: {(out.stderr or out.stdout).strip()[-500:]}")
        return 3
    failed = [c for c in report["checks"] if c["status"] == "fail"]
    lean = {"elan", "lake", "lean_project", "lean_toolchain", "mathlib", "mathlib_cache"}
    if any(c["name"] in lean for c in failed):
        print(
            f"Apex {installed} is installed, but its Lean toolchain is not set up. Tell the user "
            "this is a one-time download of the Lean toolchain and the prebuilt Mathlib cache "
            "(about 7 GB on disk, usually a few minutes), then run `apex init --yes`. Refutation "
            "(`apex refute`) works without it."
        )
        for c in failed:
            print(f"  {c['name']}: {c['detail']}")
        return 1
    if failed:
        for c in failed:
            print(f"  {c['name']}: {c['detail']}")
        return 3
    print(f"Apex {installed} is ready ({apex}). Run it through scripts/apex_run.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
