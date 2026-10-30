# Gap reason codes and their next steps

Every gap carries a reason code, an explanation and next steps. `apex suggest <target>` prints
them for the last report.

| Code | Means | Next step |
|---|---|---|
| `UNSUPPORTED_CONSTRUCT` | A construct outside the Lean subset (see LEAN_SUBSET.md), or the generated model failed | Rewrite with the subset, or verify the extracted pure core |
| `IMPURE_IO` | I/O, globals, randomness, mutation of arguments | Move the computation into a pure function and verify that |
| `FLOAT_SEMANTICS` | Floats or true division | Use integers (fixed point), or a float-aware tool |
| `NONTERMINATION` | Lean cannot see that a loop or recursion ends | Use `for` over a list or `range()` with an accumulator; recursion is not supported yet |
| `BUDGET_EXHAUSTED` | No proof found yet, or Lean timed out | Run `apex prove`; write the proof in the spec (PROOF_PLAYBOOK.md) and `apex verify` |
| `PROOF_REJECTED` | Lean rejected the proof, or a disallowed axiom / token | Read `lean_errors` (line, goal) and fix the proof |
| `NO_STATEMENT` | A property has a hint but no theorem yet | `apex scaffold <target>` appends the drafted theorem |
| `SPEC_AMBIGUOUS` | Text-only intent, confidence below the floor, or no intent at all | Write a formal hint (INTENT.md) and `apex intent submit` |
| `SPEC_CONFLICT` | Intent sources disagree | Ask the user which source is right; fix the other |
| `NEEDS_CONCURRENCY_TOOL` | Threads, shared state, locks | Write a TLA+ bundle (TLA.md), ask the user to approve it, `apex tla check <target>` |
| `SPEC_VIOLATION_NOT_REPRODUCED` | TLC broke an invariant in the spec; no real run showed it | Read TLC's trace (`--json`): add a `check_<Name>` to the harness or more runs if the code can take it; tighten the spec if it cannot |
| `MODEL_CHECK_FAILED` | TLC could not check the bundle (parse error, budget), or the runs could not be checked against it | Fix the bundle; `apex tla check` again |
