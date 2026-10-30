# ai_ts_claude: English spec

Each function's prompt, split into requirements. Hints are JavaScript expressions (`result` is
the return value, `==>` means "implies"), a person's formal reading of the prompt.

## clamp
Write a TypeScript function `clamp(x: number, lo: number, hi: number): number` that limits x to the range [lo, hi].
- Requires: the range is not empty. `lo <= hi`
- The result lies in [lo, hi]. `lo <= result && result <= hi`
- Values already in the range are unchanged. `lo <= x && x <= hi ==> result === x`

## ceilDiv
Write a TypeScript function `ceilDiv(a: number, b: number): number` that divides the integer a by a positive integer b, rounding up.
- Requires: b is positive. `b > 0`
- The result is a / b rounded up. `(result - 1) * b < a && a <= result * b`

## dedupeConsecutive
Write a TypeScript function `dedupeConsecutive(xs: number[]): number[]` that removes consecutive duplicates.
- The result is never longer than the input. `result.length <= xs.length`
- A non-empty input keeps at least one element. `xs.length > 0 ==> result.length > 0`
