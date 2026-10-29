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
    loc: str | None = None


class Property(_Strict):
    id: str
    kind: Literal["postcondition", "invariant", "equivalence", "termination", "example", "other"]
    """`example` is a concrete input/output pair mined from a doctest or a test assertion."""
    text: str
    formal_hint: str | None = None
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Precondition(_Strict):
    id: str
    text: str
    sources: list[Source] = Field(default_factory=list)


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
