"""numx with planted bugs: same docstrings, one slip each (see the comments)."""


def fact_loop(n: int) -> int:
    """n factorial.

    pre: n >= 0
    post: __return__ >= 1

    >>> fact_loop(5)
    120
    >>> fact_loop(0)
    1
    """
    r = 1
    for i in range(1, n):  # BUG: stops one short
        r *= i
    return r


def fib(n: int) -> int:
    """The n-th Fibonacci number, fib(0) == 0.

    pre: n >= 0
    post: __return__ >= 0

    >>> fib(10)
    55
    >>> fib(1)
    1
    """
    a, b = 0, 1
    for _ in range(n - 1):  # BUG: one step short
        a, b = b, a + b
    return a


def median3(a: int, b: int, c: int) -> int:
    """The middle one of three values.

    post: __return__ in [a, b, c]
    post: min(a, min(b, c)) <= __return__ <= max(a, max(b, c))

    >>> median3(3, 1, 2)
    2
    """
    if a <= b:
        if b <= c:
            return b
        return max(a, c)
    if a <= c:
        return a
    return min(b, c)  # BUG: min instead of max


def div_ceil(a: int, b: int) -> int:
    """a / b rounded up, for a positive divisor.

    pre: b > 0
    post: (__return__ - 1) * b < a <= __return__ * b

    >>> div_ceil(7, 2)
    4
    >>> div_ceil(-7, 2)
    -3
    """
    return a // b + 1  # BUG: wrong when b divides a


def saturating_sub(a: int, b: int) -> int:
    """a - b, but never below zero.

    pre: a >= 0 and b >= 0
    post: __return__ >= 0
    post: a >= b -> __return__ == a - b
    post: a < b -> __return__ == 0
    """
    if a >= b:
        return a - b
    return b - a  # BUG: distance, not saturation


def round_down(x: int, m: int) -> int:
    """The largest multiple of m that is at most x.

    pre: m > 0
    post: __return__ % m == 0
    post: __return__ <= x < __return__ + m

    >>> round_down(-1, 5)
    -5
    """
    return x - abs(x) % m  # BUG: wrong for negative x


def triangular(n: int) -> int:
    """0 + 1 + ... + n.

    pre: n >= 0
    post: 2 * __return__ == n * (n + 1)
    """
    total = 0
    for i in range(n):  # BUG: leaves out n
        total += i
    return total
