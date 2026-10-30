"""Intent sources, the hint language, fusion and statement drafting (no Lean needed)."""

import ast
import textwrap
from pathlib import Path

import pytest

from apex.analyze.purity import classify
from apex.analyze.target import load_function
from apex.intent.candidates import EMPTY, Mined
from apex.intent.docstring import mine_docstring
from apex.intent.english import mine_english, spec_items
from apex.intent.fuse import fuse
from apex.intent.hints import HintError, holds, parse_condition, parse_hint
from apex.intent.proposals import (
    ProposalSet,
    ProposedPrecondition,
    ProposedProperty,
    check_proposals,
    to_candidates,
)
from apex.intent.source import mine_source
from apex.intent.tests import mine_tests
from apex.theorems.draft import draft_property
from apex.translate.lean import signature, translate


def _module(tmp_path: Path, name: str, src: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(src))
    return path


# ---- hint language ---------------------------------------------------------------------------


def test_hint_implication_aliases_and_canonical_text() -> None:
    h = parse_hint("lo <= hi && __return__ >= lo -> result <= hi", ["x", "lo", "hi"])
    assert h.text == "lo <= hi and r >= lo -> r <= hi"
    assert h.mentions_result
    assert holds(h, {"x": 0, "lo": 0, "hi": 5, "r": 3}) is True
    assert holds(h, {"x": 0, "lo": 0, "hi": 5, "r": 9}) is False
    assert holds(h, {"x": 0, "lo": 5, "hi": 0, "r": 9}) is None  # premise false: no claim


def test_hint_result_name_moves_when_a_parameter_is_called_r() -> None:
    h = parse_hint("result > r", ["r"])
    assert h.result == "result" and h.text == "result > r"


@pytest.mark.parametrize(
    "hint",
    [
        "r.bit_length() > 0",  # attribute access
        "open('x') == r",  # calls outside the allowlist
        "r == 'a'",  # strings
        "r == 1.5",  # floats
        "q > 0",  # unknown name
        "r == x ** 100000",  # unbounded exponent
        "__import__('os')",  # dunder
        "lambda: r",
    ],
)
def test_hint_rejects_unsafe_or_unknown(hint: str) -> None:
    with pytest.raises(HintError):
        parse_hint(hint, ["x"])


def test_precondition_may_not_mention_result_or_imply() -> None:
    assert ast.unparse(parse_condition("lo <= hi", ["lo", "hi"])) == "lo <= hi"
    with pytest.raises(HintError):
        parse_condition("r >= 0", ["x"])
    with pytest.raises(HintError):
        parse_condition("x > 0 -> x > 1", ["x"])


def test_generators_see_their_environment() -> None:
    h = parse_hint("all(v <= r for v in xs)", ["xs"])
    assert holds(h, {"xs": [1, 5, 2], "r": 5}) is True
    assert holds(h, {"xs": [1, 5, 2], "r": 4}) is False


# ---- intent sources --------------------------------------------------------------------------


CLAMP = '''
def clamp(x: int, lo: int, hi: int) -> int:
    """Clamp.

    pre: lo <= hi
    post: lo <= __return__ <= hi

    >>> clamp(5, 0, 3)
    3
    """
    assert lo <= hi
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''


def test_docstring_contracts_and_doctests(tmp_path: Path) -> None:
    info = load_function(f"{_module(tmp_path, 'm', CLAMP)}::clamp")
    mined = mine_docstring(info)
    assert [(e.args, e.expected) for e in mined.examples] == [((5, 0, 3), 3)]
    assert [(c.kind, c.hint) for c in mined.candidates] == [
        ("precondition", "lo <= hi"),
        ("property", "lo <= __return__ <= hi"),
    ]


def test_leading_asserts_are_preconditions_and_not_modelled(tmp_path: Path) -> None:
    info = load_function(f"{_module(tmp_path, 'm', CLAMP)}::clamp")
    assert [c.hint for c in mine_source(info).candidates] == ["lo <= hi"]
    assert "assert" not in translate(info).text


def test_hypothesis_tests_become_properties_with_strategy_bounds(tmp_path: Path) -> None:
    _module(tmp_path, "m", "def f(a: int, b: int) -> int:\n    return a + b\n")
    (tmp_path / "test_m.py").write_text(
        textwrap.dedent(
            """
            import pytest
            from hypothesis import assume, given, strategies as st
            from m import f

            @given(st.integers(min_value=0), st.integers())
            def test_sum(x, y):
                assume(y >= 0)
                r = f(x, y)
                assert r >= x

            @given(st.integers().filter(lambda v: v > 3), st.integers())
            def test_unknown_domain(x, y):
                assert f(x, y) > 0

            @pytest.mark.parametrize("x,y,want", [(1, 2, 3), (0, 0, 0)])
            def test_rows(x, y, want):
                assert f(x, y) == want

            def test_plain():
                assert f(2, 2) == 4
            """
        )
    )
    mined = mine_tests(load_function(f"{tmp_path / 'm.py'}::f"))
    assert [c.hint for c in mined.candidates] == ["a >= 0 -> b >= 0 -> r >= a"]
    assert sorted((e.args, e.expected) for e in mined.examples) == [
        ((0, 0), 0),
        ((1, 2), 3),
        ((2, 2), 4),
    ]


def test_english_sections_hints_and_requirements(tmp_path: Path) -> None:
    spec = tmp_path / "m.spec.md"
    spec.write_text(
        "# m\n\n## other\n- unrelated `r == 0`\n\n## clamp\n"
        "- Requires: lo <= hi `lo <= hi`\n- In range. `lo <= r <= hi`\n- Fast.\n"
    )
    items = spec_items(spec.read_text(), "clamp")
    assert [(i.id, i.text, i.hint, i.is_precondition) for i in items] == [
        ("S1", "lo <= hi", "lo <= hi", True),
        ("S2", "In range.", "lo <= r <= hi", False),
        ("S3", "Fast.", None, False),
    ]
    mined, _ = mine_english(spec, "clamp", ["x", "lo", "hi"], set())
    assert [c.confidence for c in mined.candidates] == [0.9, 0.9, 0.5]


# ---- fusion ----------------------------------------------------------------------------------


def _fuse(path: Path, fn: str, mined: Mined, previous=None):
    info = load_function(f"{path}::{fn}")
    sig = signature(info.node)
    return fuse(
        info,
        classify(info.node, info.module_tree),
        sig.params,
        sig.returns,
        mined,
        previous=previous,
    )


def test_same_claim_from_two_sources_merges(tmp_path: Path) -> None:
    path = _module(tmp_path, "m", CLAMP)
    info = load_function(f"{path}::clamp")
    spec = tmp_path / "m.spec.md"
    spec.write_text("- In range `lo <= r <= hi`\n")
    eng, _ = mine_english(spec, "clamp", ["x", "lo", "hi"], set())
    intent, _ = _fuse(path, "clamp", mine_docstring(info) + mine_source(info) + eng)
    p1 = next(p for p in intent.properties if p.id == "P1")
    assert {s.type for s in p1.sources} == {"docstring", "spec"}
    assert p1.confidence > 0.9
    assert len(intent.preconditions) == 1  # `lo <= hi` from the docstring and the assert


def test_example_contradicting_a_property_is_a_conflict(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "m",
        '''
        def half(x: int) -> int:
            """post: __return__ * 2 <= x

            >>> half(7)
            4
            """
            return x // 2
        ''',
    )
    intent, _ = _fuse(path, "half", mine_docstring(load_function(f"{path}::half")))
    assert [c.property_ids for c in intent.conflicts] == [["P1", "E1"]]


def test_a_hint_that_runs_module_code_is_never_a_conflict(tmp_path: Path) -> None:
    # The planted bug makes reverse_list the identity, so evaluating P1 on E1 with the real
    # function fails. That is the code disagreeing with the intent (refutation's job), not two
    # intent sources disagreeing with each other.
    path = _module(
        tmp_path,
        "m",
        '''
        def reverse_list(xs: list[int]) -> list[int]:
            """post: reverse_list(__return__) == xs

            >>> reverse_list([1, 2, 3])
            [3, 2, 1]
            """
            return xs
        ''',
    )
    intent, _ = _fuse(path, "reverse_list", mine_docstring(load_function(f"{path}::reverse_list")))
    assert intent.conflicts == []


def test_ids_are_stable_across_runs(tmp_path: Path) -> None:
    path = _module(tmp_path, "m", CLAMP)
    spec = tmp_path / "m.spec.md"
    spec.write_text("- In range `lo <= r <= hi`\n- Identity `lo <= x <= hi -> r == x`\n")
    eng, _ = mine_english(spec, "clamp", ["x", "lo", "hi"], set())
    first, _ = _fuse(path, "clamp", eng)
    spec.write_text("- New first `r != hi + 1`\n- Identity `lo <= x <= hi -> r == x`\n")
    eng, _ = mine_english(spec, "clamp", ["x", "lo", "hi"], set())
    second, _ = _fuse(path, "clamp", eng, previous=first)
    ids = {p.formal_hint: p.id for p in second.properties}
    assert ids["lo <= x <= hi -> r == x"] == "P2"  # kept its ID
    assert ids["r != hi + 1"] == "P3"  # P1 is retired, not reused


def test_formalization_fills_the_item_and_may_change_its_kind(tmp_path: Path) -> None:
    path = _module(tmp_path, "m", CLAMP)
    spec = tmp_path / "m.spec.md"
    spec.write_text("- lo is at most hi.\n- In range.\n")
    eng, items = mine_english(spec, "clamp", ["x", "lo", "hi"], set())
    proposals = ProposalSet(
        proposed_by="test-model",
        properties=[
            ProposedProperty(item="S2", text="In range.", formal_hint="lo <= r <= hi", basis="spec")
        ],
        preconditions=[
            ProposedPrecondition(item="S1", text="lo <= hi", formal_hint="lo <= hi", basis="spec")
        ],
    )
    cands = to_candidates(proposals, items, spec.name, path.name)
    intent, _ = _fuse(path, "clamp", eng + Mined([], cands))
    assert [(c.formal_hint, c.proposed_by) for c in intent.preconditions] == [
        ("lo <= hi", "test-model")
    ]
    assert [(p.formal_hint, p.sources[0].loc) for p in intent.properties] == [
        ("lo <= r <= hi", "m.spec.md:2")
    ]


def test_check_proposals_rejects_invalid_hints_and_unknown_items() -> None:
    proposals = ProposalSet(
        properties=[
            ProposedProperty(text="ok", formal_hint="r >= 0", basis="source"),
            ProposedProperty(text="bad", formal_hint="r.real >= 0", basis="source"),
            ProposedProperty(item="S9", text="no item", formal_hint="r >= 0", basis="spec"),
        ]
    )
    check = check_proposals(proposals, ["x"], set(), [])
    assert [p.text for p in check.accepted.properties] == ["ok"]
    assert [r.index for r in check.rejected] == [1, 2]


# ---- drafting --------------------------------------------------------------------------------


def _draft(tmp_path: Path, src: str, fn: str, hint: str, pre: str | None = None) -> str:
    path = _module(tmp_path, "m", src)
    info = load_function(f"{path}::{fn}")
    spec = tmp_path / "m.spec.md"
    spec.write_text(f"- claim `{hint}`\n" + (f"- Requires: pre `{pre}`\n" if pre else ""))
    eng, _ = mine_english(spec, fn, [a.arg for a in info.node.args.args], {fn})
    intent, _ = _fuse(path, fn, eng)
    model = translate(info)
    return draft_property(intent.properties[0], intent, model, {fn}).text


def test_draft_clamp_statement(tmp_path: Path) -> None:
    text = _draft(tmp_path, CLAMP, "clamp", "lo <= r <= hi", pre="lo <= hi")
    assert "theorem P1 (x lo hi : Int) (h1 : lo ≤ hi) :" in text
    assert "(lo ≤ (clamp x lo hi)) ∧ ((clamp x lo hi) ≤ hi) := by" in text
    assert text.rstrip().endswith("sorry")


def test_draft_unwraps_optional_results(tmp_path: Path) -> None:
    src = (
        "def d(a: int, b: int) -> int | None:\n"
        "    if b == 0:\n        return None\n    return a // b\n"
    )
    text = _draft(tmp_path, src, "d", "r is not None -> r * b <= a")
    assert "(v1 : Int)" in text and "(d a b) = some v1" in text
    assert "v1 * b" in text


def test_draft_binds_only_what_the_claim_uses(tmp_path: Path) -> None:
    src = (
        "def s(xs: list[int]) -> int:\n    t = 0\n    for x in xs:\n        t += x\n    return t\n"
    )
    text = _draft(tmp_path, src, "s", "s([]) == 0")
    assert "theorem P1 :" in text


def test_empty_mined_is_an_identity() -> None:
    assert (EMPTY + EMPTY).examples == []
