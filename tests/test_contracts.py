import pytest
from pydantic import ValidationError

from apex.core.config import ApexConfig
from apex.core.hashing import review_key, statement_hash
from apex.core.intent import IntentModel
from apex.core.report import Gap, ProofArtifact, PropertyResult, Review
from apex.core.verdict import ExitCode, ReasonCode, Verdict, exit_code_for

CLAMP_INTENT = {
    "schema_version": "1",
    "function": {"qualname": "mathx.clamp", "file": "mathx.py", "sha256": "abc"},
    "purity": {"class": "pure", "reasons": []},
    "signature": {"params": [["x", "int"], ["lo", "int"], ["hi", "int"]], "returns": "int"},
    "properties": [
        {
            "id": "P1",
            "kind": "postcondition",
            "text": "result is between lo and hi",
            "formal_hint": "lo <= hi -> lo <= r && r <= hi",
            "sources": [
                {"type": "docstring", "loc": "mathx.py:3"},
                {"type": "test", "loc": "test_mathx.py:12"},
            ],
            "confidence": 0.92,
        }
    ],
    "preconditions": [{"id": "C1", "text": "lo <= hi", "sources": [{"type": "inferred"}]}],
    "conflicts": [],
}

PROOF = ProofArtifact(path=".apex/proofs/p1.lean", sha256="f00", axioms=["propext"])


def test_handoff_intent_example_round_trips() -> None:
    model = IntentModel.model_validate(CLAMP_INTENT)
    assert model.purity.class_ == "pure"
    assert model.model_dump(by_alias=True, mode="json", exclude_none=True) == CLAMP_INTENT


def test_intent_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        IntentModel.model_validate({**CLAMP_INTENT, "surprise": 1})


def test_proven_requires_approval_and_proof() -> None:
    with pytest.raises(ValidationError):
        PropertyResult(id="P1", verdict=Verdict.PROVEN, proof=PROOF)
    with pytest.raises(ValidationError):
        PropertyResult(id="P1", verdict=Verdict.PROVEN, review=Review(status="approved"))
    PropertyResult(id="P1", verdict=Verdict.PROVEN, proof=PROOF, review=Review(status="approved"))
    PropertyResult(
        id="P1", verdict=Verdict.PROVEN_UNREVIEWED, proof=PROOF, review=Review(status="pending")
    )


def test_refuted_needs_counterexample_and_gaps_need_reasons() -> None:
    with pytest.raises(ValidationError):
        PropertyResult(id="P1", verdict=Verdict.REFUTED)
    with pytest.raises(ValidationError):
        PropertyResult(id="P1", verdict=Verdict.UNPROVEN)
    PropertyResult(
        id="P1",
        verdict=Verdict.UNPROVEN,
        gap=Gap(
            reason_code=ReasonCode.BUDGET_EXHAUSTED,
            explanation="ran out",
            next_steps=["raise budget"],
        ),
    )


def test_exit_codes() -> None:
    assert exit_code_for([Verdict.PROVEN]) is ExitCode.OK
    assert exit_code_for([]) is ExitCode.OK
    assert exit_code_for([Verdict.PROVEN, Verdict.UNPROVEN]) is ExitCode.GAPS
    assert exit_code_for([Verdict.UNPROVEN, Verdict.REFUTED]) is ExitCode.FAILURE
    assert exit_code_for([Verdict.CONTRADICTION]) is ExitCode.FAILURE
    assert exit_code_for([Verdict.UNPROVEN], fail_on=[Verdict.UNPROVEN]) is ExitCode.FAILURE


def test_config_defaults_match_handoff() -> None:
    cfg = ApexConfig()
    assert cfg.proof_budget_s == 120 and cfg.retries == 4 and cfg.confidence_floor == 0.6
    assert cfg.fail_on == [Verdict.REFUTED, Verdict.FIDELITY_FAILED, Verdict.CONTRADICTION]


def test_statement_hash_ignores_whitespace_only() -> None:
    a = statement_hash("theorem t (x : Int) :\n    x = x")
    assert a == statement_hash("theorem t (x : Int) : x = x")
    assert a != statement_hash("theorem t (x : Int) : x ≤ x")
    assert review_key("f", "P1", a).startswith("f:P1:")
