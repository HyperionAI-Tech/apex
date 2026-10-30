# JavaScript and TypeScript targets

Targets look like `src/shapes.ts::clamp` (also `.mts`, `.js`, `.mjs`). Apex needs Node.js 22.6+.

## Writing intent

In JSDoc, with hints as JavaScript expressions (`result` is the return value, `==>` means
"implies"):

```ts
/**
 * @pre lo <= hi
 * @post lo <= result && result <= hi
 * @post lo <= x && x <= hi ==> result === x
 * @example clamp(5, 0, 3) // => 3
 */
export function clamp(x: number, lo: number, hi: number): number { ... }
```

- Tests are read from `<module>.test.ts` / `.spec.ts` (or `__tests__/`): `expect(f(...)).toBe(v)`
  becomes an example, and a fast-check `fc.property(...)` becomes a property.
- `<module>.spec.md` English specs use the same JS hint syntax.
- In hints, `===` on arrays means equal contents, and `%` means JavaScript's truncating `%`.

## What to expect

- **Types are required.** TypeScript, or JSDoc `@param {number} x` / `@returns {number}`.
  Untyped functions are a gap.
- **Export the function.** Apex only runs exported functions.
- **`number` proofs come back `proven_conditional`.** They hold while every intermediate value
  is a safe integer. Report them as exactly that; never as `proven`. `bigint` code can be
  `proven`.
- **Refused constructs:** `==`, bitwise operators, `this`, classes, `try`, `switch`, strings,
  floats (a bare `/`), indexing, `while`, early returns in loops. The gap names the line; many
  fixes are small rewrites (`for (const x of xs)` with an accumulator, `Math.floor(a / b)`).
- **Fuzzing:** `apex fuzz <target> --engine jazzer` (coverage-guided; `apex init --js`) or the
  default Hypothesis engine.
- **Not yet for JS:** CrossHair and TLA+.
