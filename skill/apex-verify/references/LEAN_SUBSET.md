# Supported Python subset (MVP)

Apex translates only what it can model exactly. Anything else is reported as a gap with a reason
code, never approximated.

## Types (annotations are required)

| Python | Lean |
|---|---|
| `int` | `Int` (arbitrary precision, like Python) |
| `bool` | `Bool` |
| `list[T]` | `List T` |
| `tuple[A, B, ...]` | `A × B × ...` (right-nested) |
| `T \| None`, `Optional[T]` | `Option T` |

`float`, `str`, `dict`, `set` and classes are out of scope.

## Expressions

- `+ - *` on ints; `//` and `%` with Python's floor semantics (`Apex.pyFloorDiv`, `Apex.pyMod`);
  `**` with a non-negative integer literal exponent.
- Comparisons (including chains like `lo <= x <= hi`), `and`, `or`, `not`, `in` on lists,
  `is None` / `is not None` on Optional values.
- `x if c else y`, list and tuple literals, `xs + ys`, constant tuple indexing `t[0]`,
  slices with non-negative literal bounds `xs[1:]`, `xs[:k]`.
- One-generator list comprehensions, with optional `if` filters.
- `len`, `abs`, two-argument `min`/`max`, `sum`, and calls to other top-level functions in the
  same module (translated too, and part of the function's identity for review).

## Statements

- Leading `assert` statements are preconditions: the model leaves them out, drafted statements
  assume them, and the fidelity check only compares inputs that pass them.
- `return`, assignment (including tuple unpacking), annotated assignment, `+=`-style updates.
- `if` / `elif` / `else`.
- `for x in xs:` and `for i in range(a, b):` without `break`, `continue` or `return` inside.
  Loops become `List.foldl` over the variables they update.
- `xs.append(v)` on a local list.
- Recursion, if Lean can see it terminates.

## Common gaps and fixes

| Reason code | Typical cause | Fix |
|---|---|---|
| `UNSUPPORTED_CONSTRUCT` | `xs[i]`, truthiness (`if xs:`), falling off the end, `assert` after other code, `raise` | Iterate instead of indexing; compare explicitly (`len(xs) == 0`); return on every path or return `Optional` |
| `NONTERMINATION` | `while` loops, recursion Lean cannot see shrinking | `for` over a `range`, or recurse on `n - 1` with a base case |
| `FLOAT_SEMANTICS` | `/`, float literals, `math.*` | Integer or fixed-point arithmetic |
| `IMPURE_IO` | `print`, files, globals, randomness, mutating arguments | Verify the pure core Apex extracts, or move the side effects out |
| `NEEDS_CONCURRENCY_TOOL` | threads, asyncio | Model the protocol in TLA+; verify the pure step function here |
