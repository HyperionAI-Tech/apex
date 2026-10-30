"""Spec/code conflicts, round two.

The first five have intent sources that disagree with each other (expected: contradiction). The
last six have intent that agrees with itself but not with the code (expected: refuted).
"""


def triple(x: int) -> int:
    """Three times x.

    >>> triple(2)
    6
    """
    return 3 * x


def dec(x: int) -> int:
    return x - 1


def twice_len(xs: list[int]) -> int:
    """post: __return__ == 2 * len(xs)"""
    return 2 * len(xs)


def is_pos(x: int) -> bool:
    """Whether x is strictly positive.

    >>> is_pos(1)
    False
    """
    return x > 0


def sum3(a: int, b: int, c: int) -> int:
    """The sum of three values.

    >>> sum3(1, 1, 1)
    4
    """
    return a + b + c


def nonneg_diff(a: int, b: int) -> int:
    """The gap between a and b, which is never negative.

    post: __return__ >= 0
    """
    return a - b


def count_big(xs: list[int], t: int) -> int:
    n = 0
    for x in xs:
        if x >= t:
            n += 1
    return n


def avg_floor(a: int, b: int) -> int:
    """The average of a and b, rounded down.

    >>> avg_floor(3, 4)
    3
    """
    return (a + b + 1) // 2


def first_or(xs: list[int], d: int) -> int:
    r = 0
    started = False
    for x in xs:
        if not started:
            r = x
        started = True
    return r


def abs_max(a: int, b: int) -> int:
    """The larger magnitude of the two.

    post: __return__ == max(abs(a), abs(b))
    """
    return max(a, b)


def wrap_index(i: int, n: int) -> int:
    """i wrapped into range(n), Python-style.

    pre: n > 0
    post: 0 <= __return__ < n
    """
    if i >= 0:
        return i % n
    return i
