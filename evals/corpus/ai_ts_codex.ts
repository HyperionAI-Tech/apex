// AI-generated corpus entries, unedited: Codex CLI 0.153.3 (`codex exec`), 2026-10-31. Default model.
// Each function is the agent's whole reply to its prompt in ai_ts_codex.spec.md, plus
// "Reply with only the code, in one TypeScript code block." Only the Markdown fence was
// removed. The spec's backticked hints are a person's formal reading of each prompt.

export function countBetween(xs: number[], lo: number, hi: number): number {
  return xs.filter(x => x > lo && x < hi).length;
}

export function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

export function sumOfSquares(xs: number[]): number {
  return xs.reduce((sum, x) => sum + x * x, 0);
}
