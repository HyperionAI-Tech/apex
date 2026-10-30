"""Outside the Lean subset (while loops, recursion Lean cannot show terminates, indexing), but
the real Python can still be refuted."""


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


def gcd(a: int, b: int) -> int:
    """Greatest common divisor.

    pre: a >= 0 and b >= 0
    post: __return__ >= 0
    """
    if b == 0:
        return a
    return gcd(b, a % b)


def fact_rec(n: int) -> int:
    """n factorial, recursively.

    pre: n >= 0
    post: __return__ >= 1
    """
    if n <= 0:
        return 1
    return n * fact_rec(n - 1)


def binary_search(xs: list[int], t: int) -> int:
    """Index of t in the sorted list xs, or -1.

    post: __return__ >= -1
    post: __return__ < len(xs)
    """
    lo = 0
    hi = len(xs) - 1
    for _ in range(len(xs)):
        if lo > hi:
            break
        mid = (lo + hi) // 2
        if xs[mid] == t:
            return mid
        if xs[mid] < t:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def mix(n: int) -> int:
    """A non-negative mixing of n's digits.

    pre: n >= 0
    post: __return__ >= 0
    """
    total = 0
    while n > 0:
        total = (total * 31 + n % 10) % 10007
        n //= 10
    if pow(total, 5, 10007) == 1234:
        return -1  # BUG: a leftover sentinel; random testing and CrossHair rarely reach it
    return total
