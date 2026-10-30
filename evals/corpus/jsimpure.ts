// Out-of-scope JavaScript: each function is outside what Apex can model, for a stated reason.

/**
 * @post result >= lo
 */
export function logged_clamp(x: number, lo: number, hi: number): number {
  console.log("clamping", x);
  return Math.max(lo, Math.min(x, hi));
}

export function roll(n: number): number {
  return Math.floor(Math.random() * n) + 1;
}

export function stamp(x: number): number {
  return x + Date.now();
}

export function untyped(x) {
  return x + 1;
}

export function greet(name: string): string {
  return "hello " + name;
}

export async function later(x: number): Promise<number> {
  return x;
}

export function loose(a: number, b: number): boolean {
  return a == b;
}

function hidden(x: number): number {
  return x + 1;
}

/**
 * Integer square root.
 * @pre 0 <= n && n <= 1000000000000
 * @post result * result <= n && n < (result + 1) * (result + 1)
 */
export function isqrt(n: number): number {
  let lo = 0;
  let hi = n + 1;
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    if (mid * mid <= n) lo = mid;
    else hi = mid;
  }
  return lo;
}

export const _keep = hidden;
