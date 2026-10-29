"""Proposals: formal hints written by a model, in engine mode or host mode alike.

This is the contract between "a model read the English / docstring / code" and the Intent Model.
Apex's own LLM client (`apex intent propose`) and an agent driving Apex (`apex intent submit`)
both produce a `ProposalSet`, and both go through the same `check_proposals`: every hint is parsed
against the function's real signature, and anything invalid comes back with a reason instead of
entering the model. A proposal is never a verdict; at most it becomes a drafted statement that a
human still has to approve.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apex.core.intent import Source
from apex.intent.candidates import Candidate
from apex.intent.english import SpecItem
from apex.intent.hints import HintError, parse_condition, parse_hint

SCHEMA_VERSION = "1"

BASIS_CONFIDENCE = {"spec": 0.85, "docstring": 0.8, "source": 0.6}
"""A formalized English item, docstring prose, or the code alone. Reading intent off the code
assumes the code is right, so `source` sits at the default floor: drafted, never trusted more."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedProperty(_Strict):
    item: str | None = Field(
        default=None, description="ID of the English spec item this formalizes (S1, ...)."
    )
    text: str = Field(description="The property in one plain-English sentence.")
    formal_hint: str = Field(description="Python boolean expression; `r` is the result.")
    kind: Literal["postcondition", "invariant", "equivalence", "other"] = "postcondition"
    basis: Literal["spec", "docstring", "source"] = Field(
        description="What it was read from: an English spec item, docstring prose, or the code."
    )


class ProposedPrecondition(_Strict):
    item: str | None = None
    text: str
    formal_hint: str = Field(description="Python boolean expression over the parameters only.")
    basis: Literal["spec", "docstring", "source"]


class ProposalSet(_Strict):
    schema_version: Literal["1"] = SCHEMA_VERSION
    proposed_by: str = Field(
        default="host", description="Which model or agent wrote these. Debugging, not trust."
    )
    properties: list[ProposedProperty] = Field(default_factory=list)
    preconditions: list[ProposedPrecondition] = Field(default_factory=list)


class Rejection(_Strict):
    index: int
    kind: Literal["property", "precondition"]
    formal_hint: str
    reason: str


class ProposalCheck(_Strict):
    accepted: ProposalSet
    rejected: list[Rejection] = Field(default_factory=list)


def check_proposals(
    proposals: ProposalSet, params: list[str], helpers: set[str], items: list[SpecItem]
) -> ProposalCheck:
    item_ids = {it.id for it in items}
    accepted = ProposalSet(proposed_by=proposals.proposed_by)
    rejected: list[Rejection] = []
    for k, p in enumerate(proposals.properties):
        try:
            if p.item is not None and p.item not in item_ids:
                raise HintError(f"no English spec item {p.item}")
            parse_hint(p.formal_hint, params, helpers)
            accepted.properties.append(p)
        except HintError as e:
            rejected.append(
                Rejection(index=k, kind="property", formal_hint=p.formal_hint, reason=str(e))
            )
    for k, c in enumerate(proposals.preconditions):
        try:
            if c.item is not None and c.item not in item_ids:
                raise HintError(f"no English spec item {c.item}")
            parse_condition(c.formal_hint, params, helpers)
            accepted.preconditions.append(c)
        except HintError as e:
            rejected.append(
                Rejection(index=k, kind="precondition", formal_hint=c.formal_hint, reason=str(e))
            )
    return ProposalCheck(accepted=accepted, rejected=rejected)


def merge(stored: ProposalSet | None, new: ProposalSet) -> ProposalSet:
    """Add new proposals to stored ones; the same item + hint is kept once (newest wins)."""
    if stored is None:
        return new

    def key(p: ProposedProperty | ProposedPrecondition) -> tuple[str | None, str]:
        return p.item, " ".join(p.formal_hint.split())

    props = {key(p): p for p in [*stored.properties, *new.properties]}
    pres = {key(c): c for c in [*stored.preconditions, *new.preconditions]}
    by = stored.proposed_by if stored.proposed_by == new.proposed_by else "mixed"
    return ProposalSet(
        proposed_by=by, properties=list(props.values()), preconditions=list(pres.values())
    )


def to_candidates(
    proposals: ProposalSet, items: list[SpecItem], spec_file: str | None, fn_file: str
) -> list[Candidate]:
    lines = {it.id: it.line for it in items}
    out = []
    for p in [*proposals.properties, *proposals.preconditions]:
        if p.basis == "spec" and p.item and spec_file:
            src = Source(type="spec", loc=f"{spec_file}:{lines.get(p.item, 0)}")
        elif p.basis == "docstring":
            src = Source(type="docstring", loc=fn_file)
        else:
            src = Source(type="inferred", loc=fn_file)
        out.append(
            Candidate(
                kind="property" if isinstance(p, ProposedProperty) else "precondition",
                text=p.text,
                hint=p.formal_hint,
                source=src,
                confidence=BASIS_CONFIDENCE[p.basis],
                prop_kind=p.kind if isinstance(p, ProposedProperty) else "postcondition",
                proposed_by=proposals.proposed_by,
                item=p.item,
            )
        )
    return out
