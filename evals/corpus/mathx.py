"""Known-good textbook functions for the M1 corpus."""


def clamp(x: int, lo: int, hi: int) -> int:
    """Return x limited to the closed range [lo, hi].

    >>> clamp(5, 0, 3)
    3
    >>> clamp(-2, 0, 3)
    0
    """
    if x < lo:
        return lo
    elif x > hi:
        return hi
    return x


def my_abs(x: int) -> int:
    """Absolute value.

    >>> my_abs(-4)
    4
    """
    if x < 0:
        return -x
    return x


def sign(x: int) -> int:
    """-1, 0 or 1 according to the sign of x."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def sum_list(xs: list[int]) -> int:
    """Sum of the elements.

    >>> sum_list([1, 2, 3])
    6
    """
    total = 0
    for x in xs:
        total += x
    return total


def safe_div(a: int, b: int) -> int | None:
    """Floor division, or None when dividing by zero.

    >>> safe_div(7, 2)
    3
    >>> safe_div(-7, 2)
    -4
    """
    if b == 0:
        return None
    return a // b


def count_positive(xs: list[int]) -> int:
    """How many elements are strictly positive."""
    n = 0
    for x in xs:
        if x > 0:
            n += 1
    return n
