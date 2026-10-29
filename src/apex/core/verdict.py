"""Per-property verdicts, gap reason codes, and the exit-code contract (handoff section 6)."""

from collections.abc import Iterable
from enum import IntEnum, StrEnum


class Verdict(StrEnum):
    PROVEN = "proven"
    """Lean compiled cold, axioms clean, statement approved, fidelity check passed."""
    PROVEN_UNREVIEWED = "proven_unreviewed"
    """As `proven`, but the statement has not been human-approved."""
    PROVEN_CONDITIONAL = "proven_conditional"
    """Proven under an added precondition that is not present in the source."""
    REFUTED = "refuted"
    """A concrete input, replayed in Python, makes the real function violate the property."""
    FIDELITY_FAILED = "fidelity_failed"
    """The Lean model and the Python disagree on some input; the proof is discarded."""
    CONTRADICTION = "contradiction"
    """Intent sources disagree (e.g. the spec says X, a test asserts not-X)."""
    UNPROVEN = "unproven"
    """Budget exhausted with no counterexample found."""
    OUT_OF_SCOPE = "out_of_scope"
    """Impure or unsupported construct; the reason and any pure core are reported."""


# Invariant 1: only the Lean verdict path may emit these. Enforced where verdicts are assigned.
LEAN_ONLY_VERDICTS = frozenset(
    {Verdict.PROVEN, Verdict.PROVEN_UNREVIEWED, Verdict.PROVEN_CONDITIONAL}
)

FAILURE_VERDICTS = frozenset({Verdict.REFUTED, Verdict.FIDELITY_FAILED, Verdict.CONTRADICTION})

GAP_VERDICTS = frozenset(
    {
        Verdict.UNPROVEN,
        Verdict.OUT_OF_SCOPE,
        Verdict.PROVEN_UNREVIEWED,
        Verdict.PROVEN_CONDITIONAL,
    }
)


class ReasonCode(StrEnum):
    UNSUPPORTED_CONSTRUCT = "UNSUPPORTED_CONSTRUCT"
    IMPURE_IO = "IMPURE_IO"
    FLOAT_SEMANTICS = "FLOAT_SEMANTICS"
    NONTERMINATION = "NONTERMINATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SPEC_AMBIGUOUS = "SPEC_AMBIGUOUS"
    SPEC_CONFLICT = "SPEC_CONFLICT"
    NEEDS_CONCURRENCY_TOOL = "NEEDS_CONCURRENCY_TOOL"
    """Recommends TLA+; Apex does not integrate it."""
    PROOF_REJECTED = "PROOF_REJECTED"
    """Lean rejected the submitted proof (errors, or a disallowed axiom)."""
    NO_STATEMENT = "NO_STATEMENT"
    """The property has no Lean statement yet."""


class ExitCode(IntEnum):
    OK = 0
    """Every approved property is proven."""
    FAILURE = 1
    """Any refuted, fidelity_failed or contradiction."""
    GAPS = 2
    """Gaps only: unproven, out_of_scope, proven_unreviewed, proven_conditional."""
    TOOL_ERROR = 3
    """Lean missing, timeout of the whole run, bad config."""


def exit_code_for(
    verdicts: Iterable[Verdict], fail_on: Iterable[Verdict] | None = None
) -> ExitCode:
    """Map a run's verdicts to the exit-code contract.

    Failures always win over gaps. `fail_on` only decides whether a gap verdict listed in it is
    promoted to a failure; by default gaps exit 2, which CI treats as a pass unless configured.
    """
    fail_set = set(fail_on) if fail_on is not None else set(FAILURE_VERDICTS)
    seen = set(verdicts)
    if seen & (FAILURE_VERDICTS | fail_set):
        return ExitCode.FAILURE
    if seen & GAP_VERDICTS:
        return ExitCode.GAPS
    return ExitCode.OK
