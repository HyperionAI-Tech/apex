"""Known-good list functions whose intent lives in Hypothesis tests (test_listx.py)."""


def list_max(xs: list[int]) -> int | None:
    found = False
    best = 0
    for x in xs:
        if not found or x > best:
            best = x
            found = True
    if found:
        return best
    return None


def contains(xs: list[int], v: int) -> bool:
    found = False
    for x in xs:
        if x == v:
            found = True
    return found


def count_eq(xs: list[int], v: int) -> int:
    n = 0
    for x in xs:
        if x == v:
            n += 1
    return n


def filter_positive(xs: list[int]) -> list[int]:
    return [x for x in xs if x > 0]


def double_all(xs: list[int]) -> list[int]:
    return [2 * x for x in xs]
