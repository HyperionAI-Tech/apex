"""AI-generated corpus entries, unedited: Claude Code 2.1.314 (`claude -p`), 2026-10-30.

Default model. Each function is the agent's whole reply to its prompt in ai_claude.spec.md,
plus "Reply with only the code, in one Python code block." Only the Markdown fence was
removed. The spec's backticked hints are a person's formal reading of each prompt.
"""


def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(x, hi))


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def dedupe_consecutive(xs: list[int]) -> list[int]:
    result: list[int] = []
    for x in xs:
        if not result or result[-1] != x:
            result.append(x)
    return result


def is_balanced(steps: list[int]) -> bool:
    total = 0
    for step in steps:
        total += step
        if total < 0:
            return False
    return total == 0


def digit_sum(n: int) -> int:
    if n < 0:
        raise ValueError("n must be non-negative")
    total = 0
    while n:
        n, d = divmod(n, 10)
        total += d
    return total
