from conflicts import double, half, neg
from hypothesis import given
from hypothesis import strategies as st


def test_double() -> None:
    assert double(2) == 5  # contradicts the docstring


def test_half() -> None:
    assert half(7) == 4  # contradicts the docstring's post: 4 * 2 > 7


@given(st.integers())
def test_neg_is_not_positive(x):
    assert neg(x) <= 0  # contradicts the doctest neg(-3) == 3
