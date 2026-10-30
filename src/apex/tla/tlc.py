"""The pinned TLA+ tools (TLC, SANY, the PlusCal translator) and a parser for TLC's output.

`tla2tools.jar` is pinned by version and SHA-256 like the Lean toolchain: `apex init --tla`
downloads the official release into `~/.apex/tla/` and refuses a file whose checksum differs.
`APEX_TLA_JAR` points at another copy (it must still match the pin).

TLC's output is parsed from its plain-text messages. Two facts about it shape the parser: with
`-continue` it prints "No error has been found" even after reporting violations, so success is
never read from that line; and its first counterexample per invariant is a shortest one
(breadth-first search), so Apex runs TLC once per invariant, without `-continue`.
"""

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from apex.lean.project import apex_home

TLA_VERSION = "1.7.4"
TLA_URL = f"https://github.com/tlaplus/tlaplus/releases/download/v{TLA_VERSION}/tla2tools.jar"
TLA_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
ENV_TLA_JAR = "APEX_TLA_JAR"


class TlaUnavailable(RuntimeError):
    """Java or the pinned tla2tools.jar is missing (exit code 3)."""


def jar_path() -> Path:
    return Path(os.environ.get(ENV_TLA_JAR) or apex_home() / "tla" / f"tla2tools-{TLA_VERSION}.jar")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tools() -> tuple[str, Path]:
    """(java, jar), or TlaUnavailable with what to do about it."""
    java = shutil.which("java")
    if java is None:
        raise TlaUnavailable("TLA+ needs Java 11 or later on PATH (TLC runs on the JVM)")
    jar = jar_path()
    if not jar.is_file():
        raise TlaUnavailable(f"the TLA+ tools are not installed: run `apex init --tla` ({jar})")
    if _sha256(jar) != TLA_SHA256:
        raise TlaUnavailable(
            f"{jar} is not the pinned tla2tools.jar v{TLA_VERSION} (checksum differs); "
            "remove it and run `apex init --tla`"
        )
    return java, jar


def install(force: bool = False) -> Path:
    """Download the pinned tla2tools.jar and verify its checksum; returns its path."""
    jar = jar_path()
    if jar.is_file() and not force and _sha256(jar) == TLA_SHA256:
        return jar
    jar.parent.mkdir(parents=True, exist_ok=True)
    tmp = jar.with_suffix(".download")
    with urllib.request.urlopen(TLA_URL, timeout=120) as r:
        tmp.write_bytes(r.read())
    if _sha256(tmp) != TLA_SHA256:
        tmp.unlink()
        raise TlaUnavailable(f"downloaded {TLA_URL} but its checksum is not the pinned one")
    tmp.replace(jar)
    return jar


# ---- running ---------------------------------------------------------------------------------


@dataclass
class TlcOutcome:
    ok: bool
    """TLC ran to completion (a violation is still `ok`: it is a result, not a failure to run)."""
    violations: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    """Invariant -> TLC's behavior up to the violation, one {variable: TLA value} per state."""
    deadlock: list[dict[str, str]] | None = None
    states_generated: int = 0
    distinct_states: int = 0
    depth: int | None = None
    error: str | None = None
    """Why TLC could not check the spec (parse or semantic error, a crash, the time budget)."""
    output: str = ""


_VIOLATED = re.compile(r"^Error: Invariant (\w+) is violated(?: by the initial state)?\.", re.M)
_STATS = re.compile(r"^(\d+) states generated, (\d+) distinct states found", re.M)
_DEPTH = re.compile(r"^The depth of the complete state graph search is (\d+)\.", re.M)
_STATE = re.compile(r"^State \d+: ")
_ASSIGN = re.compile(r"^/\\ (\w+) = (.*)$")


@contextlib.contextmanager
def _java(java: str, jar: Path) -> Iterator[list[str]]:
    """The command prefix for one TLA+ tool run, with a private java.io.tmpdir: TLC and SANY
    unpack the standard modules (Naturals.tla, ...) into the temp directory, and runs sharing the
    system one delete each other's copies ("File does not exist: .../FiniteSets.tla")."""
    with tempfile.TemporaryDirectory(prefix="apex-java-") as tmp:
        yield [java, f"-Djava.io.tmpdir={tmp}", "-XX:+UseParallelGC", "-cp", str(jar)]


def translate_pluscal(spec: Path, timeout_s: float = 60) -> str | None:
    """Run the PlusCal translator in place when the module contains an algorithm; an error, or
    None. The translation is written between the BEGIN/END TRANSLATION comments."""
    if "--algorithm" not in spec.read_text() and "--fair algorithm" not in spec.read_text():
        return None
    with _java(*tools()) as cmd:
        run = subprocess.run(
            [*cmd, "pcal.trans", "-nocfg", spec.name],
            cwd=spec.parent,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    if "Translation completed" not in run.stdout:
        lines = [ln for ln in (run.stdout + run.stderr).splitlines() if ln.strip()]
        return "PlusCal translation failed: " + (" ".join(lines[-3:]) or "no output")
    return None


def run_tlc(
    directory: Path,
    module: str,
    cfg: str,
    *,
    timeout_s: float = 300,
    deadlock: bool = False,
) -> TlcOutcome:
    """TLC on `directory/module.tla` with config text `cfg`. `deadlock`: check for deadlock
    (off by default; a terminated PlusCal algorithm is not a deadlock, but many specs stop)."""
    java, jar = tools()
    with tempfile.TemporaryDirectory(prefix="apex-tlc-") as meta, _java(java, jar) as prefix:
        cfg_path = directory / f"{module}.apex.cfg"
        cfg_path.write_text(cfg)
        cmd = [*prefix, "tlc2.TLC", "-workers", "auto"]
        cmd += ["-metadir", meta, "-config", cfg_path.name]
        if not deadlock:
            cmd.append("-deadlock")  # TLC's flag *disables* deadlock checking
        cmd.append(f"{module}.tla")
        try:
            run = subprocess.run(
                cmd, cwd=directory, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return TlcOutcome(ok=False, error=f"TLC did not finish within {timeout_s:.0f}s")
        finally:
            cfg_path.unlink(missing_ok=True)
    return parse_tlc(run.stdout + run.stderr)


def parse_tlc(out: str) -> TlcOutcome:
    res = TlcOutcome(ok=True, output=out)
    if m := _STATS.search(out):
        res.states_generated, res.distinct_states = int(m.group(1)), int(m.group(2))
    if m := _DEPTH.search(out):
        res.depth = int(m.group(1))
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if m := _VIOLATED.match(line):
            res.violations.setdefault(m.group(1), _behavior(lines, i + 1))
        elif line.startswith("Error: Deadlock reached."):
            res.deadlock = _behavior(lines, i + 1)
    if res.violations or res.deadlock is not None:
        return res
    if "Model checking completed. No error has been found." not in out:
        res.ok = False
        res.error = _first_error(lines)
    return res


def _behavior(lines: list[str], start: int) -> list[dict[str, str]]:
    """The states TLC prints after an error, until the next error or the end of the trace."""
    states: list[dict[str, str]] = []
    var: str | None = None
    for line in lines[start:]:
        if line.startswith("Error:") and states:
            break
        if _STATE.match(line):
            states.append({})
            var = None
        elif states and (m := _ASSIGN.match(line)):
            name: str = m.group(1)
            states[-1][name] = m.group(2).strip()
            var = name
        elif states and var and line.startswith("   "):  # a value continued on the next line
            states[-1][var] += " " + line.strip()
        elif states and not line.strip():
            var = None
        elif states and not line.startswith(("/\\", " ")):
            if states[-1]:
                break
    return [s for s in states if s]


def _first_error(lines: list[str]) -> str:
    for marker in ("***Parse Error***", "Semantic errors:", "Error:", "Exception"):
        for i, line in enumerate(lines):
            if marker in line:
                detail = " ".join(x.strip() for x in lines[i : i + 4] if x.strip())
                return detail[:400]
    return "TLC stopped without a result"


def sany_check(spec: Path, timeout_s: float = 60) -> str | None:
    """Parse and semantically check a module with SANY: an error message, or None."""
    with _java(*tools()) as cmd:
        run = subprocess.run(
            [*cmd, "tla2sany.SANY", spec.name],
            cwd=spec.parent,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    if run.returncode == 0:
        return None
    lines = [ln.strip() for ln in (run.stdout + run.stderr).splitlines() if ln.strip()]
    useful = [ln for ln in lines if not ln.startswith(("Parsing file", "Semantic processing"))]
    return "SANY: " + " ".join(useful[-6:])[:500]
