"""Known-good functions whose intent is written as contracts: PEP 316 `pre:`/`post:` docstring
lines and leading asserts."""


def wrap(x: int, n: int) -> int:
    """Wrap x into the range [0, n).

    post: 0 <= __return__ < n
    post: (x - __return__) % n == 0
    """
    assert n > 0
    return x % n


def abs_diff(a: int, b: int) -> int:
    """Distance between a and b.

    post: __return__ >= 0
    post: __return__ == a - b or __return__ == b - a
    """
    if a > b:
        return a - b
    return b - a


def bounded_add(a: int, b: int, cap: int) -> int:
    """a + b, but never more than cap.

    post: __return__ <= cap
    post: a + b <= cap -> __return__ == a + b
    """
    return min(a + b, cap)


def max3(a: int, b: int, c: int) -> int:
    """The largest of three.

    post: __return__ >= a and __return__ >= b and __return__ >= c
    post: __return__ in [a, b, c]
    """
    return max(a, max(b, c))


def midpoint(lo: int, hi: int) -> int:
    """A point between lo and hi.

    pre: lo <= hi
    post: lo <= __return__ <= hi
    """
    return lo + (hi - lo) // 2


def checked_square(x: int) -> int:
    """x squared.

    post: __return__ >= 0
    """
    return x * x
