from pathlib import Path

import pytest

from apex.theorems.review import ReviewStore
from apex.theorems.spec import SpecError, parse_spec, render_decl

SPEC = """import Mathlib.Tactic.Ring
-- comment
/-- P1: result is in range -/
theorem P1 (x lo hi : Int) (h : lo ≤ hi) :
    lo ≤ clamp x lo hi ∧ clamp x lo hi ≤ hi := by
  sorry

/-- helper -/
@[simp] lemma helper (x : Int) : x + 0 = x := by omega

set_option maxHeartbeats 400000 in
theorem E1 : clamp 5 0 3 = 3 := by decide
"""


def test_parse_spec() -> None:
    spec = parse_spec(SPEC)
    assert spec.imports == ["Mathlib.Tactic.Ring"]
    assert [d.name for d in spec.properties] == ["P1", "E1"]
    assert [d.name for d in spec.helpers] == ["helper"]
    p1 = spec.get("P1")
    assert p1 is not None and not p1.has_proof and p1.doc == "P1: result is in range"
    assert not p1.text.count("helper"), "a decl must not swallow the next doc comment"
    assert spec.get("E1") is not None and spec.get("E1").has_proof  # type: ignore[union-attr]


def test_statement_hash_ignores_proof_and_layout() -> None:
    a = parse_spec(SPEC).get("P1")
    b = parse_spec(SPEC.replace("  sorry", "  omega").replace(":\n    lo", ": lo")).get("P1")
    assert a and b and a.statement_sha256 == b.statement_sha256


def test_render_decl_keeps_statement() -> None:
    p1 = parse_spec(SPEC).get("P1")
    assert p1 is not None
    out = render_decl(p1, "by omega")
    assert out.startswith("/-- P1") and out.rstrip().endswith(":= by omega")
    assert parse_spec(out).get("P1").statement_sha256 == p1.statement_sha256  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "bad",
    [
        'local notation "≤" => fun a b => True\n',
        "instance : LE Int := ⟨fun _ _ => True⟩\n",
        "def clamp (x : Int) : Int := x\n",
        "set_option autoImplicit true\n",
        "attribute [simp] Int.add_comm\n",
        "theorem P1 : True := trivial\ntheorem P1 : True := trivial\n",
        "theorem P1 : True\n",
    ],
)
def test_spec_rejects_meaning_changers(bad: str) -> None:
    with pytest.raises(SpecError):
        parse_spec(bad)


def test_review_store_lapses_on_change(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "reviews.json")
    assert store.status("m.f", "fn1", "P1", "st1").status == "pending"
    store.approve(
        qualname="m.f",
        property_id="P1",
        statement="theorem P1 : True",
        function_sha256="fn1",
        statement_sha256="st1",
        reviewer="ana",
    )
    fresh = ReviewStore(tmp_path / "reviews.json")
    assert fresh.status("m.f", "fn1", "P1", "st1").status == "approved"
    assert fresh.status("m.f", "fn1", "P1", "st1").reviewer == "ana"
    assert fresh.status("m.f", "fn2", "P1", "st1").status == "lapsed"  # function changed
    assert fresh.status("m.f", "fn1", "P1", "st2").status == "lapsed"  # statement changed
    assert fresh.status("m.f", "fn1", "P2", "st1").status == "pending"
