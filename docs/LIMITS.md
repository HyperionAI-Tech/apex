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
  proof. A disagreement on an input it never generated would go unnoticed. The check is also
  thinner for loop-heavy functions: an input CPython needs more than 20 ms for is not sent to
  Lean, whose evaluator is much slower. The report says how many inputs were compared and how
  many were skipped.
- **Anything about inputs outside the preconditions.** Leading `assert`s, `pre:` lines and
  "Requires:" items are preconditions. Drafted statements assume them, refutation searches only
  inside them, and the fidelity check compares the model with the Python only where they hold.
  Outside them the function promises nothing. Python may raise there (`a // b` at `b == 0`)
  while Lean's total model returns a value. A hand-written statement that does not assume a
  precondition can therefore be proven about inputs the Python never handles. Check for this
  when you approve one.
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
| | I/O, randomness, clocks (`IMPURE_IO`), threads (`NEEDS_CONCURRENCY_TOOL`: see TLA+ below), `async` |
| | Python truthiness of lists (`if xs:`): compare explicitly, `len(xs) == 0` |

Many of these are fixable by rewriting. `for i in range(n)` instead of `while`, and an
accumulator instead of an early `return`, both stay in the subset. The gap message names the
line and suggests the rewrite.

How much real code is in the subset? In the eval corpus, ten functions were written by Claude
Code and Codex from one-line prompts and left unedited. Only 3 of the 10 fall in the subset.
The rest use early returns, indexing, `while`, truthiness, `None` as a running value, or
`raise`. For those, Apex's answer is refutation and a fuzzing campaign (no counterexample, and all ten
are in fact correct): `fuzzed`, not `proven`. Writing code to the subset is a choice you make; Apex will not
silently approximate code outside it.

## JavaScript and TypeScript

JS and TS functions are lowered into the same subset as Python, so the table above applies,
with JavaScript's own rules on top:

- **`number` proofs are conditional.**
  - A JS `number` is a double, and integer arithmetic is exact only up to 2⁵³ − 1. The Lean
    model computes with exact integers, so a proof about `number` code transfers only while
    every intermediate value is a safe integer.
  - Those proofs are reported `proven_conditional`, with that precondition stated, and never
    reach exit 0.
  - Searches skip inputs whose result leaves the safe range, and fidelity inputs are drawn below
    2²⁶ so products of two stay exact.
  - An intermediate overflow whose final result is back in range is not detected.
  - `bigint` is exact, and its proofs can be fully `proven`.
- **Types are required.** TypeScript annotations, or JSDoc `@param {T}` / `@returns {T}` in
  plain JS. An untyped function is a gap.
- **Only exported functions run.** Refutation, fuzzing and the fidelity check all execute the
  real code in Node, through the module's exports.
- **Semantics Apex keeps rather than approximates:**
  - `%` truncates toward zero;
  - only `Math.floor(a / b)` and `Math.trunc(a / b)` are integer division (a bare `/` is a
    float);
  - `===` on arrays is identity, so it's refused in code (in hints it means equal contents);
  - `==` coerces types, so it's refused;
  - bitwise operators are 32-bit, so they're refused.
- **Not modelled:** strings, objects and classes, `this`, `try`, `switch`, indexing, `while`,
  early returns in loops, default and rest parameters, generators. As in Python, many functions
  fit after a small rewrite.
- **Not yet for JS:** CrossHair (Python-only symbolic execution) and TLA+ (async and event-loop
  concurrency). Fuzzing uses Hypothesis or Jazzer.js.
- **Agent-written code.** Of six TypeScript functions written by Claude Code and Codex from
  one-line prompts, four fall inside the subset (as `proven_conditional`, because they use
  `number`). The other two use a bare `/` or array indexing, and are fuzzed.

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

## Evidence from outside Lean

`fuzzed` and `model_checked` exist for code Lean cannot model. Both are gaps (exit 2): they
never pass a CI gate that requires proof, and they never turn into `proven`.

**`fuzzed`** means one recorded campaign tried N inputs and found no counterexample:

- It says nothing about inputs the campaign never generated. Integers are drawn mostly small;
  a bug that needs a specific large value can be missed.
- It is only as good as the property's hint. A vacuous hint (`r == r`) fuzzes clean.
- It inherits refutation's skips: inputs past the recursion limit or the per-call time budget are
  left out (the count is in the evidence).
- Measured on this machine (macOS, Python 3.12, Atheris 3.0 built with Homebrew LLVM 14), as
  tested in the corpus:
  - **Hypothesis.** About 1,000 executions per second on simple integer code.
  - **Atheris.** About 13,000 per second on the same code.
  - **Exact-value needles.** On branches that compare exact values (`xs[0] == 17 and xs[1] ==
    42 ...`), neither fuzzer beat CrossHair, which refutation already runs.
  - **Where campaigns help.** Bugs that symbolic execution cannot invert, such as a sentinel
    behind `pow(total, 5, 10007) == 1234`. Default refutation misses it. Atheris found it in
    about 2 s, and Hypothesis in about 13,000 executions.
  - **When they miss.** A 20-second Hypothesis campaign on the same bug ran only 6,423
    executions on a busy machine, found nothing, and reported `fuzzed`. The corpus gives that
    case 60 s. This is the reason `fuzzed` is a gap and never a pass.

**`model_checked`** means TLC exhausted the spec's state space *within the bounds in Spec.cfg*,
and every distinct run Apex recorded of the real code was a behavior of the spec:

- **Bounds.** Two threads and two iterations is not N threads. Larger bounds can hold
  violations the bounded check never reaches.
- **Recorded runs are samples.** Varying the schedule makes rare interleavings more likely to
  show up, but cannot force every one.
- **Observation granularity.** Apex snapshots shared state between lines of the code. A single
  line that changes two observed variables shows up as one jump, which the spec must allow as
  one step.
- **The harness is trusted.** If `observe()` reports the wrong thing, conformance is checked
  against the wrong thing. That is why the harness is part of the approved bundle, and any edit
  voids the approval.
- **Invariants only.** Temporal properties (`PROPERTY` in Spec.cfg) are not checked.
- **What the hash covers.** Evidence lapses when the function's source or any bundle file
  changes, but not when a helper class it uses changes.
- **TLC's own violations do not refute.** A violation TLC finds in the spec counts only once a
  real run shows it (`check_<Name>` in the harness). Otherwise it stays a gap that carries TLC's
  trace.

## What the intent sources can and cannot give

- An English requirement without a formal hint in backticks stays text-only (`SPEC_AMBIGUOUS`)
  until a person or a model formalizes it, and a model's formalization counts only after a
  person approves it.
- Source code alone yields only its leading `assert`s. What a function is *for* cannot be read
  off code without assuming the code is right.
- When sources disagree (a doctest says `f(2) == 6`, a test says `f(2) == 5`), both properties
  are `contradiction`. Apex does not pick a winner. A property whose hint calls a function in the
  module (`reverse_list(r) == xs`) is never checked for contradictions. Evaluating it would run
  the code under test, and a bug there would look like disagreeing sources. Refutation still
  checks it against the code.

## Out of scope for this version

- Languages other than Python (JavaScript and TypeScript are planned, not built).
- Impure code beyond concurrency: I/O and floating point are reported, not modelled. Concurrency
  gets TLA+ bundles (evidence, above), not proofs.
- A second proof backend (SMT) or regression detection on previously proven code.
- Lean and Mathlib are pinned. Upgrading them is a deliberate change, re-run against the corpus.
