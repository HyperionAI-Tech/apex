"""Planted bugs: the mathx functions with off-by-one, wrong comparison, missed empty case."""


def clamp(x: int, lo: int, hi: int) -> int:
    """Return x limited to the closed range [lo, hi]."""
    if x < lo:
        return lo
    elif x >= hi:
        return hi - 1  # off-by-one
    return x


def my_abs(x: int) -> int:
    """Absolute value."""
    if x < -1:  # wrong comparison: my_abs(-1) == -1
        return -x
    return x


def sign(x: int) -> int:
    """-1, 0 or 1 according to the sign of x."""
    if x > 0:
        return 1
    if x <= 0:  # wrong comparison: sign(0) == -1
        return -1
    return 0


def sum_list(xs: list[int]) -> int:
    """Sum of the elements."""
    total = 0
    for x in xs[1:]:  # skips the first element
        total += x
    return total


def safe_div(a: int, b: int) -> int | None:
    """Floor division, or None when dividing by zero."""
    if b == 0:
        return 0  # wrong sentinel
    return a // b


def count_positive(xs: list[int]) -> int:
    """How many elements are strictly positive."""
    n = 0
    for x in xs:
        if x >= 0:  # counts zeros
            n += 1
    return n
