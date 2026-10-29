"""Planted bugs: the same functions with off-by-one, wrong comparison, missed empty case."""


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
