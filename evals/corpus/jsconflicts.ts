// Spec/code conflicts in TypeScript.

/**
 * Twice x.
 * @example double(2) // => 4
 */
export function double(x: number): number {
  return x + x;
}

/**
 * One more than x.
 * @post result === x + 1
 * @example inc(1) // => 3
 */
export function inc(x: number): number {
  return x + 1;
}

/**
 * The gap between a and b, which is never negative.
 * @post result >= 0
 */
export function gap(a: number, b: number): number {
  return a - b;
}
