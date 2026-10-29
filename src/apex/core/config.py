"""`apex.toml` configuration."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apex.core.verdict import Verdict

SCHEMA_VERSION = "1"
CONFIG_FILENAME = "apex.toml"


class ApexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = SCHEMA_VERSION
    # The default engine-mode provider is an open decision (handoff section 12); until it is made,
    # LLM steps are opt-in and deterministic verbs work with no provider at all.
    #   anthropic | openai     -> API key from ANTHROPIC_API_KEY / OPENAI_API_KEY
    #   claude-code | codex    -> the coding agent CLI already installed and logged in
    #   host                   -> the agent driving Apex proposes, via `apex intent submit`
    model_provider: Literal["anthropic", "openai", "claude-code", "codex", "host", "none"] = "none"
    model: str | None = Field(
        default=None, description="Model for the provider; unset means the provider's default."
    )
    llm_timeout_s: float = Field(default=300, gt=0, description="Per LLM call.")
    proof_budget_s: float = Field(default=120, gt=0, description="Per theorem.")
    retries: int = Field(default=4, ge=0, description="Fixer-loop iterations.")
    refute_budget_s: float = Field(
        default=10, gt=0, description="CrossHair time per property, on the real Python."
    )
    confidence_floor: float = Field(
        default=0.6, ge=0, le=1, description="Below this, a property is flagged SPEC_AMBIGUOUS."
    )
    fail_on: list[Verdict] = Field(
        default_factory=lambda: [Verdict.REFUTED, Verdict.FIDELITY_FAILED, Verdict.CONTRADICTION]
    )
    review_store: str = ".apex/reviews.json"
    ignore: list[str] = Field(default_factory=lambda: ["tests/**", "migrations/**"])

    @classmethod
    def load(cls, path: Path) -> "ApexConfig":
        with path.open("rb") as f:
            data = tomllib.load(f)
        return cls.model_validate(data.get("apex", {}))

    @classmethod
    def discover(cls, start: Path) -> tuple["ApexConfig", Path | None]:
        """Walk up from `start` looking for apex.toml; fall back to defaults."""
        for d in [start, *start.parents]:
            candidate = d / CONFIG_FILENAME
            if candidate.is_file():
                return cls.load(candidate), candidate
        return cls(), None
