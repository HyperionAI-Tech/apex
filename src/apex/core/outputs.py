"""`--json` outputs of the CLI commands that have no richer contract of their own.

Every command's JSON output is one of the published schemas (`apex schema`), so machines never
parse a shape that is not written down.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ToolErrorOutput(BaseModel):
    """What any command prints with --json when it exits 3 (tool error)."""

    error: str


class InitStep(BaseModel):
    step: str
    status: str
    path: str | None = None


class InitResult(BaseModel):
    ok: bool
    steps: list[InitStep] = Field(default_factory=list)
    error: str | None = None


class ReviewEntry(BaseModel):
    id: str
    kind: Literal["lean", "tla"] = "lean"
    english: str | None = None
    lean: str | None = None
    """The Lean statement (kind `lean`)."""
    tla: str | None = None
    """The invariant and its bundle (kind `tla`); approving it approves the whole bundle."""
    status: Literal["approved", "pending", "lapsed"]
    reviewer: str | None = None


class ReviewStatus(BaseModel):
    """`apex review <target> --json`: which statements are approved, pending or lapsed."""

    schema_version: Literal["1"] = "1"
    target: str
    spec_path: str
    statements: list[ReviewEntry] = Field(default_factory=list)


class SchemaExport(BaseModel):
    written: list[str]


class TlaDraftResult(BaseModel):
    """`apex tla draft --json`: where the drafted bundle was written (pending review)."""

    schema_version: Literal["1"] = "1"
    target: str
    bundle: str
    status: Literal["pending review"] = "pending review"
