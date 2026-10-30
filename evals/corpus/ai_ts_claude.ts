// AI-generated corpus entries, unedited: Claude Code 2.1.314 (`claude -p`), 2026-10-31. Default model.
// Each function is the agent's whole reply to its prompt in ai_ts_claude.spec.md, plus
// "Reply with only the code, in one TypeScript code block." Only the Markdown fence was
// removed. The spec's backticked hints are a person's formal reading of each prompt.

export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(Math.max(x, lo), hi);
}

export function ceilDiv(a: number, b: number): number {
  const r = a % b;
  const q = (a - r) / b;
  return r > 0 ? q + 1 : q;
}

export function dedupeConsecutive(xs: number[]): number[] {
  const result: number[] = [];
  for (let i = 0; i < xs.length; i++) {
    if (i === 0 || !Object.is(xs[i], xs[i - 1])) {
      result.push(xs[i]);
    }
  }
  return result;
}
