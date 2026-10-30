# ai_claude: English spec

Each function's prompt, split into requirements. The backticked hints are a person's formal
reading of the prompt; the code was written by the agent from the prompt alone.

## clamp
Write a Python function `clamp(x: int, lo: int, hi: int) -> int` that limits x to the range [lo, hi].
- Requires: the range is not empty. `lo <= hi`
- The result lies in [lo, hi]. `lo <= r <= hi`
- Values already in the range are unchanged. `lo <= x <= hi -> r == x`

## ceil_div
Write a Python function `ceil_div(a: int, b: int) -> int` that divides a by a positive b, rounding up.
- Requires: b is positive. `b > 0`
- The result is a / b rounded up. `(r - 1) * b < a <= r * b`

## dedupe_consecutive
Write a Python function `dedupe_consecutive(xs: list[int]) -> list[int]` that removes consecutive duplicates.
- The result is never longer than the input. `len(r) <= len(xs)`
- A non-empty input keeps at least one element. `len(xs) > 0 -> len(r) > 0`

## is_balanced
Write a Python function `is_balanced(steps: list[int]) -> bool`: each step is +1 (open) or -1 (close); return True if the running total never goes negative and ends at zero.
- No steps is balanced. `len(steps) == 0 -> r`
- A balanced sequence ends at zero. `r -> sum(steps) == 0`

## digit_sum
Write a Python function `digit_sum(n: int) -> int` that returns the sum of the decimal digits of a non-negative integer.
- Requires: n is not negative. `n >= 0`
- The sum is not negative. `r >= 0`
- A single digit is its own sum. `n < 10 -> r == n`
