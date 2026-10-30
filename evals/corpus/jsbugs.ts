// Planted bugs in TypeScript: the same JSDoc intent as jsmath.ts / jsalgos.ts, one slip each.

/**
 * Return x limited to the closed range [lo, hi].
 * @pre lo <= hi
 * @post lo <= result && result <= hi
 * @post lo <= x && x <= hi ==> result === x
 * @example clamp(5, 0, 3) // => 3
 */
export function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(x, hi - 1)); // BUG: off by one at the top
}

/**
 * Absolute value.
 * @post result >= 0
 * @post result === x || result === -x
 * @example my_abs(-4) // => 4
 */
export function my_abs(x: number): number {
  return x < -1 ? -x : x; // BUG: -1 stays negative
}

/**
 * Sum of the elements.
 * @post xs.length === 0 ==> result === 0
 * @example sum_list([1, 2, 3]) // => 6
 */
export function sum_list(xs: number[]): number {
  let total = 0;
  for (const x of xs.slice(1)) {
    total += x; // BUG: skips the first element
  }
  return total;
}

/**
 * Whether n is odd.
 * @post result === !is_even(n)
 * @example is_odd(3) // => true
 */
export function is_odd(n: number): boolean {
  return n % 2 === 1; // BUG: in JavaScript -3 % 2 is -1, so negative odd numbers are "even"
}

/** Whether n is even. */
export function is_even(n: number): boolean {
  return n % 2 === 0;
}

/**
 * Brackets as numbers: positive opens, anything else closes.
 * @example balanced([1, 1, -1, -1]) // => true
 * @example balanced([1]) // => false
 */
export function balanced(steps: number[]): boolean {
  let depth = 0;
  let ok = true;
  for (const s of steps) {
    depth += s > 0 ? 1 : -1;
    if (depth < 0) ok = false;
  }
  return ok; // BUG: unclosed brackets pass
}

/**
 * Whether every element is strictly positive.
 * @post result === xs.every((v) => v > 0)
 */
export function all_positive(xs: number[]): boolean {
  return xs.every((x) => x >= 0); // BUG: zero counts as positive
}

/**
 * The elements in reverse order.
 * @post result.length === xs.length
 * @example reverse_list([1, 2, 3]) // => [3, 2, 1]
 */
export function reverse_list(xs: number[]): number[] {
  let out: number[] = [];
  for (const x of xs) {
    out = [...out, x]; // BUG: appends instead of prepending
  }
  return out;
}

/**
 * A non-negative mixing of n's digits.
 * @pre n >= 0
 * @post result >= 0
 */
export function mix(n: number): number {
  let total = 0;
  let m = n;
  while (m > 0) {
    total = (total * 31 + (m % 10)) % 10007;
    m = Math.floor(m / 10);
  }
  if (total === 4242) return -1; // BUG: a leftover sentinel only a fuzzing campaign reaches
  return total;
}
