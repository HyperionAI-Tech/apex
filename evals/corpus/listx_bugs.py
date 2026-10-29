"""Planted bugs in the listx functions; the intent is the same tests (test_listx_bugs.py)."""


def list_max(xs: list[int]) -> int | None:
    found = False
    best = 0
    for x in xs:
        if x > best:  # forgets the first element: all-negative lists give 0
            best = x
        found = True
    if found:
        return best
    return None


def contains(xs: list[int], v: int) -> bool:
    found = False
    for x in xs[1:]:  # skips the first element
        if x == v:
            found = True
    return found


def count_eq(xs: list[int], v: int) -> int:
    n = 0
    for x in xs:
        if x != v:  # inverted comparison
            n += 1
    return n


def filter_positive(xs: list[int]) -> list[int]:
    return [x for x in xs if x >= 0]  # keeps zeros


def double_all(xs: list[int]) -> list[int]:
    return [x + 2 for x in xs]  # adds instead of multiplying
