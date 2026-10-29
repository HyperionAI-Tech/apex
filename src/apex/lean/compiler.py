"""The verdict compiler: the only code path in Apex that can say a theorem was proved.

Every check is a cold `lean` run (in the environment `lake env` resolves) over a standalone file,
never the warm REPL, because reused elaboration state is exactly what you do not want behind a
trust claim. The file is the
submission followed by an appended footer of `#print axioms` commands; only messages positioned
inside that footer count as the audit, so nothing the submission prints can impersonate it.
"""

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from apex.core.hashing import sha256_text
from apex.lean.guard import GuardFinding, scan
from apex.lean.project import LeanProject, LeanUnavailable

SCHEMA_VERSION = "1"

# Invariant 2: the only axioms a proven theorem may depend on.
ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

FOOTER_MARKER = "-- apex: axiom audit (appended by the verdict compiler; do not edit)"

_DEPENDS_RE = re.compile(r"^'(?P<name>.+?)' depends on axioms: \[(?P<axioms>.*)\]$", re.DOTALL)
_NO_AXIOMS_RE = re.compile(r"^'(?P<name>.+?)' does not depend on any axioms$")


class TheoremOutcome(StrEnum):
    PROVED = "proved"
    FAILED = "failed"
    TIMEOUT = "timeout"


class LeanMessage(BaseModel):
    severity: Literal["error", "warning", "information"]
    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None
    message: str
    goal: str | None = Field(
        default=None, description="Goal state, when the message carries one (text after ⊢ etc.)"
    )


class AxiomAudit(BaseModel):
    axioms: list[str]
    disallowed: list[str]


class TheoremCheck(BaseModel):
    name: str
    outcome: TheoremOutcome
    audit: AxiomAudit | None = None
    reasons: list[str] = Field(default_factory=list)


class CompileResult(BaseModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    status: Literal["ok", "error", "timeout", "tool_error"]
    """File-level status. `ok` means Lean reported no errors; per-theorem outcomes are separate."""
    theorems: list[TheoremCheck] = Field(default_factory=list)
    messages: list[LeanMessage] = Field(default_factory=list)
    guard_findings: list[GuardFinding] = Field(default_factory=list)
    duration_s: float = 0.0
    source_sha256: str
    lean_file: str | None = None
    tool_error: str | None = None

    @property
    def errors(self) -> list[LeanMessage]:
        return [m for m in self.messages if m.severity == "error"]


def build_file(source: str, theorem_names: list[str]) -> tuple[str, int]:
    """Append the audit footer. Returns the full text and the first line number of the footer."""
    body = source.rstrip("\n") + "\n\n"
    footer_line = body.count("\n") + 1
    footer = FOOTER_MARKER + "\n" + "".join(f"#print axioms {n}\n" for n in theorem_names)
    return body + footer, footer_line


def extract_goal(message: str) -> str | None:
    """Pull the goal state out of messages like 'unsolved goals\\nx : Int\\n⊢ x = x'."""
    if "⊢" not in message:
        return None
    head, _, rest = message.partition("\n")
    return rest.strip() if rest.strip() else head.strip()


def parse_axiom_message(text: str) -> tuple[str, list[str]] | None:
    text = text.strip()
    if m := _NO_AXIOMS_RE.match(text):
        return m["name"], []
    if m := _DEPENDS_RE.match(text):
        axioms = [a.strip() for a in " ".join(m["axioms"].split()).split(",") if a.strip()]
        return m["name"], axioms
    return None


def parse_lean_json(stdout: str) -> list[tuple[LeanMessage, str]]:
    """Parse `lean --json` output: one JSON object per line. Returns (message, raw text) pairs."""
    out: list[tuple[LeanMessage, str]] = []
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        pos = obj.get("pos") or {}
        end = obj.get("endPos") or {}
        text = obj.get("data", "")
        caption = obj.get("caption") or ""
        if caption and caption != text:
            text = f"{caption}\n{text}" if text else caption
        severity = obj.get("severity", "error")
        if severity not in ("error", "warning", "information"):
            severity = "error"
        msg = LeanMessage(
            severity=severity,
            line=pos.get("line", 0),
            column=pos.get("column", 0),
            end_line=end.get("line"),
            end_column=end.get("column"),
            message=text,
            goal=extract_goal(text),
        )
        out.append((msg, obj.get("data", "")))
    return out


def _run(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float
) -> tuple[int | None, str, str]:
    """Run with a hard timeout that kills the whole process group, not just the direct child."""
    kwargs: dict = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        stdout, stderr = proc.communicate()
        return None, stdout, stderr


@dataclass
class LeanRun:
    code: int | None
    """Process exit code; None when the run was killed at the timeout."""
    stdout: str
    stderr: str
    duration_s: float

    @property
    def messages(self) -> list[tuple[LeanMessage, str]]:
        return parse_lean_json(self.stdout)


def run_lean(
    text: str, project: LeanProject, timeout_s: float, *, keep_path: Path | None = None
) -> LeanRun:
    """Cold `lean --json` over `text` in a fresh file. Raises LeanUnavailable on tool failure.

    This is plumbing, not a verdict: only `compile_and_audit` may interpret a run as a proof.
    """
    lean, env = project.lean_command()
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if keep_path is not None:
        keep_path.parent.mkdir(parents=True, exist_ok=True)
        lean_path = keep_path
    else:
        tmp = tempfile.TemporaryDirectory(prefix="apex-lean-")
        lean_path = Path(tmp.name) / "ApexCheck.lean"
    try:
        lean_path.write_text(text, encoding="utf-8")
        started = time.monotonic()
        try:
            code, stdout, stderr = _run(
                [str(lean), "--json", str(lean_path)], project.root, env, timeout_s
            )
        except OSError as e:
            raise LeanUnavailable(f"failed to run lean: {e}") from e
        return LeanRun(code, stdout, stderr, time.monotonic() - started)
    finally:
        if tmp is not None:
            tmp.cleanup()


def compile_and_audit(
    source: str,
    theorem_names: list[str],
    *,
    project: LeanProject | None = None,
    timeout_s: float = 120.0,
    keep_dir: Path | None = None,
) -> CompileResult:
    """Cold-compile `source` and audit the axioms of each named theorem.

    A theorem is `proved` only if: the guard found nothing, Lean reported no errors anywhere in
    the file, its audit line appeared inside the footer, and every axiom is on the allowlist.
    """
    source_sha = sha256_text(source)
    try:
        project = project or LeanProject.locate()
        project.lean_command()  # fail fast, before any work, if Lean is missing
    except LeanUnavailable as e:
        return CompileResult(status="tool_error", source_sha256=source_sha, tool_error=str(e))

    findings = scan(source)
    text, footer_line = build_file(source, theorem_names)

    lean_path = keep_dir / f"{source_sha[:16]}.lean" if keep_dir is not None else None
    try:
        run = run_lean(text, project, timeout_s, keep_path=lean_path)
    except LeanUnavailable as e:
        return CompileResult(
            status="tool_error",
            source_sha256=source_sha,
            guard_findings=findings,
            tool_error=str(e),
        )
    code, stdout, stderr, duration = run.code, run.stdout, run.stderr, run.duration_s

    parsed = parse_lean_json(stdout)
    user_msgs = [m for m, _ in parsed if m.line < footer_line]
    # Footer line k (after the marker) audits theorem_names[k]. Keying by line, not by the printed
    # name, keeps namespaces from confusing the mapping and stops one audit standing in for another.
    line_to_name = {footer_line + 1 + k: n for k, n in enumerate(theorem_names)}
    audits: dict[str, list[str]] = {}
    footer_errors: list[LeanMessage] = []
    for msg, raw in parsed:
        if msg.line < footer_line:
            continue
        name = line_to_name.get(msg.line)
        if msg.severity == "information" and name and (hit := parse_axiom_message(raw)):
            audits[name] = hit[1]
        elif msg.severity == "error":
            footer_errors.append(msg)

    timed_out = code is None
    has_errors = any(m.severity == "error" for m in user_msgs) or bool(footer_errors)
    if timed_out:
        status = "timeout"
    elif has_errors:
        status = "error"
    elif code != 0:
        # Non-zero exit with no parsed errors means Lean itself failed (bad toolchain, OOM, ...).
        return CompileResult(
            status="tool_error",
            source_sha256=source_sha,
            guard_findings=findings,
            duration_s=duration,
            tool_error=(stderr or stdout).strip()[-2000:] or f"lean exit {code}",
        )
    else:
        status = "ok"

    checks = [
        _judge(name, status, audits, findings, user_msgs, footer_errors) for name in theorem_names
    ]
    return CompileResult(
        status=status,
        theorems=checks,
        messages=user_msgs + footer_errors,
        guard_findings=findings,
        duration_s=round(duration, 3),
        source_sha256=source_sha,
        lean_file=str(lean_path) if keep_dir is not None else None,
    )


def _judge(
    name: str,
    status: str,
    audits: dict[str, list[str]],
    findings: list[GuardFinding],
    user_msgs: list[LeanMessage],
    footer_errors: list[LeanMessage],
) -> TheoremCheck:
    if status == "timeout":
        return TheoremCheck(
            name=name,
            outcome=TheoremOutcome.TIMEOUT,
            reasons=["Lean did not finish within the budget"],
        )
    reasons: list[str] = []
    reasons += [f"guard: `{f.token}` at {f.line}:{f.column} ({f.reason})" for f in findings]
    first_error = next((m for m in user_msgs if m.severity == "error"), None)
    if first_error:
        summary = first_error.message.splitlines()[0] if first_error.message else "error"
        reasons.append(f"lean error at {first_error.line}:{first_error.column}: {summary}")
    if footer_errors and name not in audits:
        reasons.append(f"axiom audit failed for `{name}` (is it declared under this name?)")

    audit = None
    if name in audits:
        axioms = audits[name]
        disallowed = sorted(set(axioms) - ALLOWED_AXIOMS)
        audit = AxiomAudit(axioms=axioms, disallowed=disallowed)
        if disallowed:
            reasons.append(f"depends on disallowed axioms: {', '.join(disallowed)}")
    elif not reasons:
        reasons.append(f"no axiom audit output for `{name}`")

    outcome = TheoremOutcome.FAILED if reasons else TheoremOutcome.PROVED
    return TheoremCheck(name=name, outcome=outcome, audit=audit, reasons=reasons)
