// Known-good TypeScript functions. Names match the Python corpus so the hand-written Lean
// reference statements in specs/ apply unchanged (the Lean model of each is the same function).

/**
 * Return x limited to the closed range [lo, hi].
 * @pre lo <= hi
 * @post lo <= result && result <= hi
 * @post lo <= x && x <= hi ==> result === x
 * @example clamp(5, 0, 3) // => 3
 * @example clamp(-2, 0, 3) // => 0
 */
export function clamp(x: number, lo: number, hi: number): number {
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}

/**
 * Absolute value.
 * @post result >= 0
 * @post result === x || result === -x
 * @example my_abs(-4) // => 4
 */
export function my_abs(x: number): number {
  return x < 0 ? -x : x;
}

/** -1, 0 or 1 according to the sign of x. */
export function sign(x: number): number {
  if (x > 0) return 1;
  if (x < 0) return -1;
  return 0;
}

/**
 * Sum of the elements.
 * @post xs.length === 0 ==> result === 0
 * @example sum_list([1, 2, 3]) // => 6
 */
export function sum_list(xs: number[]): number {
  let total = 0;
  for (const x of xs) {
    total += x;
  }
  return total;
}

/**
 * Floor division, or null when dividing by zero.
 * @post b === 0 ==> result === null
 * @example safe_div(7, 2) // => 3
 * @example safe_div(-7, 2) // => -4
 */
export function safe_div(a: number, b: number): number | null {
  if (b === 0) return null;
  return Math.floor(a / b);
}

/**
 * How many elements are strictly positive.
 * @post result >= 0
 * @post result <= xs.length
 */
export function count_positive(xs: number[]): number {
  return xs.filter((x) => x > 0).length;
}

/**
 * The largest of three values.
 * @post result >= a && result >= b && result >= c
 * @post result === a || result === b || result === c
 */
export function max3(a: number, b: number, c: number): number {
  return Math.max(a, Math.max(b, c));
}

/**
 * Whether n is even (JavaScript's `%` keeps the sign of n: -3 % 2 is -1).
 * @example is_even(-4) // => true
 * @example is_even(-3) // => false
 */
export function is_even(n: number): boolean {
  return n % 2 === 0;
}

/**
 * The exact product of the elements, as a bigint.
 * @post xs.length === 0 ==> result === 1n
 * @example product([2n, 3n, 7n]) // => 42n
 */
export function product(xs: bigint[]): bigint {
  return xs.reduce((acc, x) => acc * x, 1n);
}

/**
 * Sum of a bigint list.
 * @post xs.length === 0 ==> result === 0n
 * @example sum_big([1n, 2n, 3n]) // => 6n
 */
export function sum_big(xs: bigint[]): bigint {
  let t = 0n;
  for (const x of xs) {
    t += x;
  }
  return t;
}
