"""Known-good list algorithms, specified by their docstrings."""


def is_sorted(xs: list[int]) -> bool:
    """Whether each element is at most the next one.

    >>> is_sorted([1, 2, 2, 3])
    True
    >>> is_sorted([2, 1])
    False
    >>> is_sorted([])
    True
    """
    ok = True
    prev = 0
    started = False
    for x in xs:
        if started and x < prev:
            ok = False
        prev = x
        started = True
    return ok


def insert_sorted(x: int, xs: list[int]) -> list[int]:
    """Insert x before the first element that is at least x.

    post: len(__return__) == len(xs) + 1
    post: is_sorted(xs) -> is_sorted(__return__)

    >>> insert_sorted(3, [1, 4])
    [1, 3, 4]
    """
    out = []
    placed = False
    for v in xs:
        if not placed and x <= v:
            out.append(x)
            placed = True
        out.append(v)
    if not placed:
        out.append(x)
    return out


def insertion_sort(xs: list[int]) -> list[int]:
    """Sort ascending.

    post: len(__return__) == len(xs)
    post: is_sorted(__return__)

    >>> insertion_sort([3, 1, 2, 1])
    [1, 1, 2, 3]
    """
    out = []
    for v in xs:
        out = insert_sorted(v, out)
    return out


def reverse_list(xs: list[int]) -> list[int]:
    """The elements in reverse order.

    post: len(__return__) == len(xs)
    post: reverse_list(__return__) == xs

    >>> reverse_list([1, 2, 3])
    [3, 2, 1]
    """
    out = []
    for x in xs:
        out = [x] + out
    return out


def rle_decode(runs: list[tuple[int, int]]) -> list[int]:
    """Expand (value, count) runs; a count below one contributes nothing.

    post: len(__return__) == sum([max(p[1], 0) for p in runs])

    >>> rle_decode([(5, 2), (7, 1)])
    [5, 5, 7]
    """
    out = []
    for p in runs:
        for _ in range(p[1]):
            out.append(p[0])
    return out


def rle_encode(xs: list[int]) -> list[tuple[int, int]]:
    """Run-length encode into (value, count) runs.

    post: rle_decode(__return__) == xs
    post: len(__return__) <= len(xs)

    >>> rle_encode([5, 5, 7])
    [(5, 2), (7, 1)]
    """
    out = []
    cur = 0
    n = 0
    for x in xs:
        if n > 0 and x == cur:
            n += 1
        else:
            if n > 0:
                out.append((cur, n))
            cur = x
            n = 1
    if n > 0:
        out.append((cur, n))
    return out


def balanced(steps: list[int]) -> bool:
    """Brackets encoded as numbers: positive opens, anything else closes. Balanced when no prefix
    closes more than it opened and everything opened is closed.

    >>> balanced([1, 1, -1, -1])
    True
    >>> balanced([1, -1, -1, 1])
    False
    >>> balanced([1])
    False
    """
    depth = 0
    ok = True
    for s in steps:
        if s > 0:
            depth += 1
        else:
            depth -= 1
        if depth < 0:
            ok = False
    return ok and depth == 0


def prefix_sums(xs: list[int]) -> list[int]:
    """Running totals: element i is xs[0] + ... + xs[i].

    post: len(__return__) == len(xs)

    >>> prefix_sums([1, 2, 3])
    [1, 3, 6]
    """
    out = []
    s = 0
    for x in xs:
        s += x
        out.append(s)
    return out


def dedup_adjacent(xs: list[int]) -> list[int]:
    """Collapse runs of equal neighbours to one element.

    post: len(__return__) <= len(xs)
    post: len(xs) > 0 -> len(__return__) > 0

    >>> dedup_adjacent([0, 0, 1, 0])
    [0, 1, 0]
    """
    out = []
    last = 0
    started = False
    for x in xs:
        if not started or x != last:
            out.append(x)
        last = x
        started = True
    return out


def evens(xs: list[int]) -> list[int]:
    """The even elements, in order.

    post: len([v for v in __return__ if v % 2 != 0]) == 0
    post: len(__return__) <= len(xs)
    """
    return [x for x in xs if x % 2 == 0]


def count_matches(xs: list[int], ys: list[int]) -> int:
    """How many (x, y) pairs from xs and ys are equal.

    post: 0 <= __return__ <= len(xs) * len(ys)
    """
    c = 0
    for x in xs:
        for y in ys:
            if x == y:
                c += 1
    return c


def last_or(xs: list[int], d: int) -> int:
    """The last element, or d for the empty list.

    post: len(xs) == 0 -> __return__ == d
    post: len(xs) > 0 -> __return__ in xs
    """
    r = d
    for x in xs:
        r = x
    return r


def repeat(x: int, n: int) -> list[int]:
    """n copies of x (none when n is not positive).

    post: len(__return__) == max(n, 0)
    post: len([v for v in __return__ if v != x]) == 0
    """
    return [x for _ in range(n)]


def all_positive(xs: list[int]) -> bool:
    """Whether every element is strictly positive.

    post: __return__ == (len([v for v in xs if v <= 0]) == 0)
    """
    ok = True
    for x in xs:
        ok = ok and x > 0
    return ok


def negate_all(xs: list[int]) -> list[int]:
    """Every element negated.

    post: negate_all(__return__) == xs
    post: sum(__return__) == -sum(xs)
    """
    return [-x for x in xs]


def sort2(a: int, b: int) -> tuple[int, int]:
    """The two values in ascending order.

    post: __return__[0] <= __return__[1]
    post: __return__[0] + __return__[1] == a + b

    >>> sort2(5, 2)
    (2, 5)
    """
    return (min(a, b), max(a, b))


def max_or(xs: list[int], d: int) -> int:
    """The largest element, or d for the empty list.

    post: len(xs) > 0 -> __return__ in xs
    post: len([v for v in xs if v > __return__]) == 0
    post: len(xs) == 0 -> __return__ == d
    """
    r = d
    started = False
    for x in xs:
        if not started or x > r:
            r = x
        started = True
    return r


def clamp_all(xs: list[int], lo: int, hi: int) -> list[int]:
    """Every element limited to [lo, hi].

    pre: lo <= hi
    post: len(__return__) == len(xs)
    post: len([v for v in __return__ if v < lo or v > hi]) == 0
    """
    return [min(max(x, lo), hi) for x in xs]


def count_in_range(xs: list[int], lo: int, hi: int) -> int:
    """How many elements lie in [lo, hi].

    post: 0 <= __return__ <= len(xs)
    post: __return__ == len([v for v in xs if lo <= v <= hi])
    """
    n = 0
    for x in xs:
        if lo <= x <= hi:
            n += 1
    return n
