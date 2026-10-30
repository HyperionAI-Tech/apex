from disagree import dec, first_or, sum3, triple, twice_len
from hypothesis import given
from hypothesis import strategies as st


def test_triple() -> None:
    assert triple(2) == 5  # contradicts the doctest triple(2) == 6


def test_dec() -> None:
    assert dec(5) == 3  # contradicts the English spec: r == x - 1


def test_twice_len() -> None:
    assert twice_len([1]) == 3  # contradicts the docstring: 2 * len(xs)


@given(st.integers(), st.integers(), st.integers())
def test_sum3_adds(a, b, c):
    assert sum3(a, b, c) == a + b + c  # contradicts the doctest sum3(1, 1, 1) == 4


def test_first_or_empty() -> None:
    assert first_or([], 7) == 7  # agrees with the spec; the code returns 0
