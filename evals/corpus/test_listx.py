"""Intent for listx, as tests. Apex mines the examples and the Hypothesis properties."""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from listx import contains, count_eq, double_all, filter_positive, list_max


@pytest.mark.parametrize("xs,want", [([3, 1, 2], 3), ([-5, -2], -2), ([], None)])
def test_list_max_examples(xs, want):
    assert list_max(xs) == want


@given(st.lists(st.integers()))
def test_list_max_is_upper_bound(xs):
    r = list_max(xs)
    assume(r is not None)
    assert all(v <= r for v in xs)


@given(st.lists(st.integers()))
def test_list_max_is_member(xs):
    r = list_max(xs)
    assume(r is not None)
    assert r in xs


@given(st.lists(st.integers()))
def test_list_max_none_only_when_empty(xs):
    assert (list_max(xs) is None) == (len(xs) == 0)


@given(st.lists(st.integers()), st.integers())
def test_contains_is_membership(xs, v):
    assert contains(xs, v) == (v in xs)


@given(st.lists(st.integers()), st.integers())
def test_count_eq_counts(xs, v):
    assert count_eq(xs, v) == len([x for x in xs if x == v])


@given(st.lists(st.integers()))
def test_filter_positive_only_positive(xs):
    r = filter_positive(xs)
    assert all(v > 0 for v in r)
    assert len(r) <= len(xs)


@given(st.lists(st.integers()))
def test_double_all(xs):
    r = double_all(xs)
    assert len(r) == len(xs)
    assert sum(r) == 2 * sum(xs)


def test_double_all_example():
    assert double_all([1, -2]) == [2, -4]
