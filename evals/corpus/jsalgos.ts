// Known-good TypeScript list algorithms (names match the Python corpus and specs/).

/**
 * Whether each element is at most the next one.
 * @example is_sorted([1, 2, 2, 3]) // => true
 * @example is_sorted([2, 1]) // => false
 */
export function is_sorted(xs: number[]): boolean {
  let ok = true;
  let prev = 0;
  let started = false;
  for (const x of xs) {
    if (started && x < prev) ok = false;
    prev = x;
    started = true;
  }
  return ok;
}

/**
 * Insert x before the first element that is at least x.
 * @post result.length === xs.length + 1
 * @example insert_sorted(3, [1, 4]) // => [1, 3, 4]
 */
export function insert_sorted(x: number, xs: number[]): number[] {
  const out: number[] = [];
  let placed = false;
  for (const v of xs) {
    if (!placed && x <= v) {
      out.push(x);
      placed = true;
    }
    out.push(v);
  }
  if (!placed) out.push(x);
  return out;
}

/**
 * Sort ascending.
 * @post result.length === xs.length
 * @post is_sorted(result)
 * @example insertion_sort([3, 1, 2, 1]) // => [1, 1, 2, 3]
 */
export function insertion_sort(xs: number[]): number[] {
  let out: number[] = [];
  for (const v of xs) {
    out = insert_sorted(v, out);
  }
  return out;
}

/**
 * The elements in reverse order.
 * @post result.length === xs.length
 * @example reverse_list([1, 2, 3]) // => [3, 2, 1]
 */
export function reverse_list(xs: number[]): number[] {
  let out: number[] = [];
  for (const x of xs) {
    out = [x, ...out];
  }
  return out;
}

/**
 * Brackets as numbers: positive opens, anything else closes.
 * @example balanced([1, 1, -1, -1]) // => true
 * @example balanced([1, -1, -1, 1]) // => false
 * @example balanced([1]) // => false
 */
export function balanced(steps: number[]): boolean {
  let depth = 0;
  let ok = true;
  for (const s of steps) {
    depth += s > 0 ? 1 : -1;
    if (depth < 0) ok = false;
  }
  return ok && depth === 0;
}

/**
 * Running totals.
 * @post result.length === xs.length
 * @example prefix_sums([1, 2, 3]) // => [1, 3, 6]
 */
export function prefix_sums(xs: number[]): number[] {
  const out: number[] = [];
  let s = 0;
  for (const x of xs) {
    s += x;
    out.push(s);
  }
  return out;
}

/**
 * Whether every element is strictly positive.
 * @post result === xs.every((v) => v > 0)
 */
export function all_positive(xs: number[]): boolean {
  return xs.every((x) => x > 0);
}

/**
 * Every element negated.
 * @post negate_all(result).length === xs.length
 */
export function negate_all(xs: number[]): number[] {
  return xs.map((x) => -x);
}

/**
 * How many elements lie in [lo, hi].
 * @post 0 <= result && result <= xs.length
 */
export function count_in_range(xs: number[], lo: number, hi: number): number {
  return xs.filter((x) => lo <= x && x <= hi).length;
}
