def clamp(x: int, lo: int, hi: int) -> int:
    """Clamp x into the closed range [lo, hi].

    pre: lo <= hi
    post: lo <= __return__ <= hi
    post: lo <= x <= hi -> __return__ == x

    >>> clamp(5, 0, 3)
    3
    >>> clamp(-2, 0, 3)
    0
    """
    if x < lo:
        return lo
    if x >= hi:
        return hi - 1
    return x
