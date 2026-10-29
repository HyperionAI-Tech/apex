"""Outside the Lean subset (while loops), but the real Python can still be refuted."""


def isqrt(n: int) -> int:
    """Integer square root.

    pre: 0 <= n <= 1000000
    post: __return__ * __return__ <= n < (__return__ + 1) * (__return__ + 1)
    """
    r = 0
    while (r + 1) * (r + 1) <= n:
        r += 1
    return r


def isqrt_bug(n: int) -> int:
    """Integer square root.

    pre: 0 <= n <= 1000000
    post: __return__ * __return__ <= n < (__return__ + 1) * (__return__ + 1)
    """
    r = 0
    while r * r < n:  # overshoots when n is not a perfect square
        r += 1
    return r
