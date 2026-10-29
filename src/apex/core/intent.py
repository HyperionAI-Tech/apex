"""The Intent Model: the hand-off between "understanding" a function and proving things about it."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FunctionRef(_Strict):
    qualname: str
    file: str
    sha256: str


class Purity(_Strict):
    class_: Literal["pure", "impure", "unknown"] = Field(alias="class")
    reasons: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Signature(_Strict):
    params: list[tuple[str, str]]
    returns: str


class Source(_Strict):
    type: Literal["docstring", "test", "spec", "source", "inferred"]
    """`spec` is an English spec file; `inferred` is a model's reading of the code alone."""
    loc: str | None = None


class Property(_Strict):
    id: str
    kind: Literal["postcondition", "invariant", "equivalence", "termination", "example", "other"]
    """`example` is a concrete input/output pair mined from a doctest or a test assertion."""
    text: str
    formal_hint: str | None = None
    """A Python boolean expression over the parameters and `r` (the result); `A -> B` is
    implication. Grammar: references/INTENT.md. Evaluated in Python for conflicts and refutation,
    translated to Lean for the drafted statement."""
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    proposed_by: str | None = None
    """The model that wrote the formal hint (e.g. `claude-code`), if any. Debugging, not trust."""


class Precondition(_Strict):
    id: str
    text: str
    formal_hint: str | None = None
    """A Python boolean expression over the parameters. Every drafted statement assumes it."""
    sources: list[Source] = Field(default_factory=list)
    proposed_by: str | None = None


class Conflict(_Strict):
    property_ids: list[str]
    description: str
    sources: list[Source] = Field(default_factory=list)


class IntentModel(_Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    function: FunctionRef
    purity: Purity
    signature: Signature
    properties: list[Property] = Field(default_factory=list)
    preconditions: list[Precondition] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
