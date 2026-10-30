# ai_ts_codex: English spec

Each function's prompt, split into requirements. Hints are JavaScript expressions (`result` is
the return value, `==>` means "implies"), a person's formal reading of the prompt.

## countBetween
Write a TypeScript function `countBetween(xs: number[], lo: number, hi: number): number` that counts the elements strictly between lo and hi.
- The count is the number of elements strictly between lo and hi. `result === xs.filter((v) => lo < v && v < hi).length`
- The count is between zero and the length. `0 <= result && result <= xs.length`

## isLeapYear
Write a TypeScript function `isLeapYear(year: number): boolean` for the Gregorian calendar.
- Years divisible by 400 are leap years. `year % 400 === 0 ==> result`
- Other century years are not. `year % 100 === 0 && year % 400 !== 0 ==> !result`
- Years not divisible by 4 are not. `year % 4 !== 0 ==> !result`

## sumOfSquares
Write a TypeScript function `sumOfSquares(xs: number[]): number` that returns the sum of the squares of the elements.
- The sum is never negative. `result >= 0`
- The empty list sums to zero. `xs.length === 0 ==> result === 0`
