# ai_codex: English spec

Each function's prompt, split into requirements. The backticked hints are a person's formal
reading of the prompt; the code was written by the agent from the prompt alone.

## count_between
Write a Python function `count_between(xs: list[int], lo: int, hi: int) -> int` that counts the elements strictly between lo and hi.
- The count is the number of elements strictly between lo and hi. `r == len([v for v in xs if lo < v < hi])`
- The count is between zero and the length. `0 <= r <= len(xs)`

## run_length_encode
Write a Python function `run_length_encode(xs: list[int]) -> list[tuple[int, int]]` that run-length encodes a list into (value, count) pairs.
- The counts add up to the length of the input. `sum([p[1] for p in r]) == len(xs)`
- Every count is positive. `len([p for p in r if p[1] <= 0]) == 0`
- There are never more runs than elements. `len(r) <= len(xs)`

## second_largest
Write a Python function `second_largest(xs: list[int]) -> int | None` that returns the second largest distinct value, or None if there isn't one.
- The result is an element. `r is None or r in xs`
- Some element is larger than the result. `r is None or len([v for v in xs if v > r]) > 0`

## merge_sorted
Write a Python function `merge_sorted(a: list[int], b: list[int]) -> list[int]` that merges two sorted lists into one sorted list.
- Every element is kept. `len(r) == len(a) + len(b)`
- Nothing is changed. `sum(r) == sum(a) + sum(b)`

## is_leap_year
Write a Python function `is_leap_year(year: int) -> bool` for the Gregorian calendar.
- Years divisible by 400 are leap years. `year % 400 == 0 -> r`
- Other century years are not. `year % 100 == 0 and year % 400 != 0 -> not r`
- Years not divisible by 4 are not. `year % 4 != 0 -> not r`
