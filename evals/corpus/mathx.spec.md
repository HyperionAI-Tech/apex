# mathx: English spec

Each item is one requirement. A trailing `hint` in backticks is its formal reading; items without
one stay text-only until a model or an agent formalizes them.

## clamp
- Requires: lo is at most hi. `lo <= hi`
- The result lies between lo and hi. `lo <= r <= hi`
- Values already inside the range come back unchanged. `lo <= x <= hi -> r == x`

## my_abs
- The result is never negative. `r >= 0`
- The result is x or -x. `r == x or r == -x`
- Negating the input does not change the result. `my_abs(-x) == r`

## sign
- The result is -1, 0 or 1. `r in [-1, 0, 1]`
- Positive inputs give 1. `x > 0 -> r == 1`
- Zero gives 0. `x == 0 -> r == 0`
- Negative inputs give -1. `x < 0 -> r == -1`

## sum_list
- The empty list sums to zero. `sum_list([]) == 0`
- It agrees with Python's built-in sum. `r == sum(xs)`
- It runs in linear time.

## safe_div
- Dividing by zero gives None. `b == 0 -> r is None`
- Otherwise it is floor division. `b != 0 -> r == a // b`

## count_positive
- The count is never negative. `r >= 0`
- The count never exceeds the length of the list. `r <= len(xs)`
- Zeros and negatives are not counted. `r == len([v for v in xs if v > 0])`
