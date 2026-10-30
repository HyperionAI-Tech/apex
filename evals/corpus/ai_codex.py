"""AI-generated corpus entries, unedited: Codex CLI 0.153.3 (`codex exec`), 2026-10-30.

Default model. Each function is the agent's whole reply to its prompt in ai_codex.spec.md,
plus "Reply with only the code, in one Python code block." Only the Markdown fence was
removed. The spec's backticked hints are a person's formal reading of each prompt.
"""


def count_between(xs: list[int], lo: int, hi: int) -> int:
    return sum(lo < x < hi for x in xs)


def run_length_encode(xs: list[int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for value in xs:
        if result and result[-1][0] == value:
            result[-1] = (value, result[-1][1] + 1)
        else:
            result.append((value, 1))
    return result


def second_largest(xs: list[int]) -> int | None:
    largest = second = None
    for x in xs:
        if largest is None or x > largest:
            largest, second = x, largest
        elif x != largest and (second is None or x > second):
            second = x
    return second


def merge_sorted(a: list[int], b: list[int]) -> list[int]:
    result: list[int] = []
    i = j = 0

    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(b[j])
            j += 1

    result.extend(a[i:])
    result.extend(b[j:])
    return result


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
