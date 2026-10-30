"""algos with planted bugs: same docstrings, one slip each (see the comments)."""


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
    return out  # BUG: x is lost when it is the largest


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
        out = out + [x]  # BUG: appends instead of prepending
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
    return out  # BUG: the last run is never flushed


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
    return ok  # BUG: unclosed brackets pass


def dedup_adjacent(xs: list[int]) -> list[int]:
    """Collapse runs of equal neighbours to one element.

    post: len(__return__) <= len(xs)
    post: len(xs) > 0 -> len(__return__) > 0

    >>> dedup_adjacent([0, 0, 1, 0])
    [0, 1, 0]
    """
    out = []
    last = 0
    for x in xs:
        if x != last:  # BUG: leading zeros are dropped
            out.append(x)
        last = x
    return out


def all_positive(xs: list[int]) -> bool:
    """Whether every element is strictly positive.

    post: __return__ == (len([v for v in xs if v <= 0]) == 0)
    """
    ok = True
    for x in xs:
        ok = ok and x >= 0  # BUG: zero counts as positive
    return ok


def max_or(xs: list[int], d: int) -> int:
    """The largest element, or d for the empty list.

    post: len(xs) > 0 -> __return__ in xs
    post: len([v for v in xs if v > __return__]) == 0
    post: len(xs) == 0 -> __return__ == d
    """
    r = d
    for x in xs:
        if x > r:  # BUG: d competes with the elements
            r = x
    return r
