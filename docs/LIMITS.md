# What Apex cannot prove

Apex is conservative: when it cannot model or decide something faithfully, it says so with a
gap verdict and a reason code. It never guesses. This page lists where those gaps are, so that a
verdict means neither more nor less than it says. Read it before relying on `proven`.

## What `proven` does and does not mean

`proven` means all of the following:

- a person approved the Lean statement;
- Lean checked a proof of it in a cold compile, using no axioms beyond `propext`,
  `Classical.choice` and `Quot.sound`;
- the Lean model of the function agreed with the real Python on 200 generated inputs.

It does **not** mean:

- **The statement is what you wanted.** Lean proves the statement as written. If the statement
  is weaker than your intent (`r >= 0` when you meant `r == abs(x)`), the proof is of the weaker
  thing. Approval exists for this reason: read the Lean before you approve it.
- **The translation is proven correct.** The proof is about Apex's Lean model of your function,
  not about CPython. The translator is part of what you trust. Apex reduces that risk in two
  ways: it refuses any construct it cannot model exactly, and it runs the compiled model against
  the real Python on generated inputs before reporting any proof. That check is testing, not
  proof. A disagreement on an input it never generated would go unnoticed.
- **The function terminates or raises nothing on inputs outside the preconditions.** Leading
  `assert`s are preconditions, so the guarantee is partial correctness on inputs that pass them.
  Where Python would raise on a failed precondition, there is no result to be wrong about.
- **Anything about performance.** Time, memory, and complexity ("runs in linear time") have no
  formal reading. Such requirements stay text-only (`SPEC_AMBIGUOUS`).

`proven_unreviewed` is the same result without the approval, and it does not count as proven.
`unproven` means no proof was found within budget. It is not evidence that the property is
false.

## Python that Apex cannot model

The translator accepts a deliberately small subset. Anything outside it makes the whole function
`out_of_scope`. Refutation still runs on the real Python, so a bug can still be caught.

| Supported | Not supported (reason code) |
|---|---|
| `int` (unbounded, like Python), `bool`, `list[int]`-style lists, fixed tuples, `Optional` returns | `str`, `float` (`FLOAT_SEMANTICS`), `dict`, `set`, classes, `Optional` *parameters* narrowed with `is None` |
| `if` / `elif` / `else`, conditional expressions, `and` / `or` / `not` | `while` loops (`NONTERMINATION`) |
| `for` over a list or `range(...)`, nested loops, list comprehensions with filters | `return`, `break` or `continue` inside a loop: accumulate a result instead |
| `+ - * // % **`, comparisons (also chained), `in` on lists | indexing `xs[i]` (can raise `IndexError`); slices only with literal bounds |
| `len`, `abs`, `sum`, `min`/`max` of two values, `any` | `sorted`, `set`, `enumerate`, `zip`, `reversed`, `list(...)`, `isinstance` |
| calls to other functions in the same module | calls into other modules (`from math import gcd`), lambdas, nested functions |
| local variables, `out.append(...)` on a local list | module-level constants and globals; mutating an argument (`IMPURE_IO`) |
| leading `assert`s (as preconditions) | `assert` later in the body, `raise`, `try` |
| | recursion, in practice: Lean's automatic termination check does not see that `n - 1`, `a % b` or `xs[1:]` decreases (`NONTERMINATION`); use a `for` loop |
| | I/O, randomness, clocks (`IMPURE_IO`), threads (`NEEDS_CONCURRENCY_TOOL`), `async` |
| | Python truthiness of lists (`if xs:`): compare explicitly, `len(xs) == 0` |

Many of these are fixable by rewriting. `for i in range(n)` instead of `while`, and an
accumulator instead of an early `return`, both stay in the subset. The gap message names the
line and suggests the rewrite.

How much real code is in the subset? In the eval corpus, ten functions were written by Claude
Code and Codex from one-line prompts and left unedited. Only 3 of the 10 fall in the subset.
The rest use early returns, indexing, `while`, truthiness, `None` as a running value, or
`raise`. For those, Apex's answer is refutation (no counterexample was found, and all ten are in
fact correct), not proof. Writing code to the subset is a choice you make; Apex will not
silently approximate code outside it.

## What refutation can and cannot say

`refuted` is strong: a concrete input that Apex ran on the real Python and watched break the
property. Finding no counterexample is weak. Hypothesis and CrossHair search under a budget.
CrossHair's symbolic search finds some needle-in-a-haystack inputs that random testing misses,
but it is not exhaustive.

Some inputs are skipped rather than reported, and the skip is noted on the property:

- **Recursion depth.** An input on which the Python exceeds its recursion limit is skipped. Lean's
  model has no call stack, and running out of stack is a resource limit like memory, not a
  different function. Whether it happens depends on how deep in the stack the call starts, so
  reporting it would give a different answer from run to run.
- **Slow calls.** An input on which the function runs past 0.5 s is skipped, and after three
  slow calls the search stops calling the function. A loop over `range(n)` with `n` near 2**40
  would otherwise never return. Apex cannot tell slow from non-terminating, so it never claims
  either.

A function that only goes wrong on inputs that are huge, deeply recursive, or slow can
therefore pass refutation.

## What the intent sources can and cannot give

- An English requirement without a formal hint in backticks stays text-only (`SPEC_AMBIGUOUS`)
  until a person or a model formalizes it, and a model's formalization counts only after a
  person approves it.
- Source code alone yields only its leading `assert`s. What a function is *for* cannot be read
  off code without assuming the code is right.
- When sources disagree (a doctest says `f(2) == 6`, a test says `f(2) == 5`), both properties
  are `contradiction`. Apex does not pick a winner.

## Out of scope for this version

- Languages other than Python (JavaScript and TypeScript are planned, not built).
- Impure code: I/O, concurrency and floating point are reported, with a recommendation where
  one exists (for example TLA+ for concurrency), but not modelled.
- A second proof backend (SMT) or regression detection on previously proven code.
- Lean and Mathlib are pinned. Upgrading them is a deliberate change, re-run against the corpus.
