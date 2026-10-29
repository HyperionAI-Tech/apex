"""The run report. The gap report is a view of it, not a separate artifact."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apex.core.verdict import GAP_VERDICTS, LEAN_ONLY_VERDICTS, ReasonCode, Verdict

SCHEMA_VERSION = "1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Review(_Strict):
    status: Literal["approved", "pending", "lapsed"]
    reviewer: str | None = None
    approved_at: datetime | None = None


class ProofArtifact(_Strict):
    path: str
    sha256: str
    axioms: list[str]


class Counterexample(_Strict):
    inputs: dict[str, str]
    """Argument name -> Python `repr` of the value, so it can be replayed verbatim."""
    expected: str | None = None
    actual: str | None = None
    found_by: Literal["crosshair", "hypothesis", "example"]


class Gap(_Strict):
    reason_code: ReasonCode
    explanation: str
    next_steps: list[str] = Field(default_factory=list)


class Provenance(_Strict):
    """Which model proposed each candidate. Recorded for debugging, never for trust."""

    statement_by: str | None = None
    proof_by: str | None = None


class PropertyResult(_Strict):
    id: str
    verdict: Verdict
    lean_statement: str | None = None
    review: Review | None = None
    proof: ProofArtifact | None = None
    counterexample: Counterexample | None = None
    added_preconditions: list[str] = Field(default_factory=list)
    gap: Gap | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _verdict_shape(self) -> Self:
        v = self.verdict
        if v in LEAN_ONLY_VERDICTS and self.proof is None:
            raise ValueError(f"{v} requires a proof artifact from the Lean verdict path")
        if v is Verdict.PROVEN and (self.review is None or self.review.status != "approved"):
            raise ValueError("proven requires an approved statement; use proven_unreviewed")
        if v is Verdict.PROVEN_CONDITIONAL and not self.added_preconditions:
            raise ValueError("proven_conditional must list the added preconditions")
        if v is Verdict.REFUTED and self.counterexample is None:
            raise ValueError("refuted requires a concrete counterexample")
        if v in GAP_VERDICTS - {Verdict.PROVEN_UNREVIEWED} and self.gap is None:
            raise ValueError(f"{v} is a gap and must carry a reason code and next steps")
        return self


class FidelitySummary(_Strict):
    agree: bool
    inputs_checked: int
    mismatch: Counterexample | None = None


class FunctionResult(_Strict):
    qualname: str
    file: str
    sha256: str
    purity: Literal["pure", "impure", "unknown"] = "unknown"
    gap: Gap | None = None
    """Set when the whole function is out of scope (impure, or outside the translatable subset)."""
    pure_core: str | None = None
    fidelity: FidelitySummary | None = None
    properties: list[PropertyResult] = Field(default_factory=list)


class Report(_Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    apex_version: str
    mode: Literal["engine", "host"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lean_toolchain: str | None = None
    mathlib_rev: str | None = None
    functions: list[FunctionResult] = Field(default_factory=list)
    exit_code: int
