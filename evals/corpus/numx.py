"""Known-good integer and boolean functions, specified by their docstrings."""


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
    for i in range(1, n + 1):
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
    for _ in range(n):
        a, b = b, a + b
    return a


def is_even(n: int) -> bool:
    """Whether n is divisible by 2.

    post: __return__ == is_even(n + 2)
    post: __return__ != is_even(n + 1)

    >>> is_even(-4)
    True
    """
    return n % 2 == 0


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
    return max(b, c)


def div_ceil(a: int, b: int) -> int:
    """a / b rounded up, for a positive divisor.

    pre: b > 0
    post: (__return__ - 1) * b < a <= __return__ * b

    >>> div_ceil(7, 2)
    4
    >>> div_ceil(-7, 2)
    -3
    """
    return -((-a) // b)


def saturating_sub(a: int, b: int) -> int:
    """a - b, but never below zero.

    pre: a >= 0 and b >= 0
    post: __return__ >= 0
    post: a >= b -> __return__ == a - b
    post: a < b -> __return__ == 0
    """
    if a >= b:
        return a - b
    return 0


def round_down(x: int, m: int) -> int:
    """The largest multiple of m that is at most x.

    pre: m > 0
    post: __return__ % m == 0
    post: __return__ <= x < __return__ + m

    >>> round_down(-1, 5)
    -5
    """
    return x - x % m


def triangular(n: int) -> int:
    """0 + 1 + ... + n.

    pre: n >= 0
    post: 2 * __return__ == n * (n + 1)
    """
    total = 0
    for i in range(n + 1):
        total += i
    return total


def pow_loop(b: int, e: int) -> int:
    """b to the power e, for a non-negative exponent.

    pre: e >= 0
    post: e == 0 -> __return__ == 1
    post: e >= 1 -> __return__ == b * pow_loop(b, e - 1)

    >>> pow_loop(2, 10)
    1024
    """
    r = 1
    for _ in range(e):
        r *= b
    return r


def between(x: int, lo: int, hi: int) -> bool:
    """Whether x lies in the closed range [lo, hi].

    post: __return__ == (lo <= x and x <= hi)

    >>> between(3, 3, 3)
    True
    """
    return lo <= x <= hi


def exactly_one(a: bool, b: bool) -> bool:
    """Exactly one of a and b.

    post: __return__ == (a != b)
    """
    return (a or b) and not (a and b)


def majority3(a: bool, b: bool, c: bool) -> bool:
    """True when at least two of the three are true.

    post: majority3(a, a, c) == a
    post: __return__ == majority3(b, c, a)
    """
    return (a and b) or (a and c) or (b and c)
