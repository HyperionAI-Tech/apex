"""M0 canary suite: the verdict compiler must say proved / failed / timeout correctly.

Each canary's first line declares the expectation:
    -- canary: expect=<proved|failed|timeout> theorem=<name>[,<name>] [timeout=<seconds>]
               [audit_rejects=<axiom substring>]

`audit_rejects` asserts the axiom audit catches the cheat on its own, so a guard that happens to
fire first can never mask a regression in the audit, which is the actual trust anchor.
"""

import re
from pathlib import Path

import pytest

from apex.lean.compiler import ALLOWED_AXIOMS, TheoremOutcome, compile_and_audit
from apex.lean.project import LeanProject

CANARY_DIR = Path(__file__).parent / "canaries"
HEADER = re.compile(r"^-- canary: (?P<fields>.+)$", re.MULTILINE)
DEFAULT_TIMEOUT_S = 120.0


def _canaries() -> list[Path]:
    return sorted(CANARY_DIR.glob("*/*.lean"))


def _header(path: Path) -> dict[str, str]:
    m = HEADER.search(path.read_text())
    assert m, f"{path.name} has no canary header"
    return dict(kv.split("=", 1) for kv in m["fields"].split())


def test_suite_has_ten_of_each() -> None:
    assert len(list((CANARY_DIR / "good").glob("*.lean"))) >= 10
    assert len(list((CANARY_DIR / "bad").glob("*.lean"))) >= 10


@pytest.mark.lean
@pytest.mark.parametrize("path", _canaries(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_canary(path: Path, lean_project: LeanProject) -> None:
    fields = _header(path)
    expected = TheoremOutcome(fields["expect"])
    names = fields["theorem"].split(",")
    timeout = float(fields.get("timeout", DEFAULT_TIMEOUT_S))

    result = compile_and_audit(path.read_text(), names, project=lean_project, timeout_s=timeout)

    assert result.status != "tool_error", result.tool_error
    for check in result.theorems:
        assert check.outcome is expected, f"{check.name}: {check.outcome} {check.reasons}"
        if rejects := fields.get("audit_rejects"):
            assert check.audit is not None, f"{check.name}: no audit output"
            assert any(rejects in ax for ax in check.audit.disallowed), check.audit
        if check.outcome is TheoremOutcome.PROVED:
            assert check.audit is not None
            assert set(check.audit.axioms) <= ALLOWED_AXIOMS
