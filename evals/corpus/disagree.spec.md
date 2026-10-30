# disagree: English spec

## dec
- The result is one less than x. `r == x - 1`

## is_pos
- Positive inputs give True. `x > 0 -> r`

## count_big
- Counts the elements strictly greater than t. `r == len([v for v in xs if v > t])`

## first_or
- The empty list gives the default. `len(xs) == 0 -> r == d`
