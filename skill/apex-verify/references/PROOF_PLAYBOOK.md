# Writing proofs (host mode)

Apex's tactic tiers (`apex prove <target> --tactics-only`) close most arithmetic and simple
properties. For what stays open, you write the proof in the spec file and Apex checks it.

## The files

- `.apex/lean/<module>/<function>/Model.lean`: the Lean model Apex generated. Read it; never edit
  it (it is regenerated). Definitions are in namespace `<module>`.
- `.apex/lean/<module>/<function>/Spec.lean`: the theorems. Replace `sorry` with a proof. You may
  add helper lemmas (`theorem` / `lemma` named like `P3_step`) and Mathlib imports. Nothing else
  is allowed in the file (no `def`, `instance`, `notation`, `set_option` beyond a heartbeat cap).
- Never change a theorem's statement: its approval is tied to the exact text.

## Tactic tiers, cheapest first

1. `decide` for closed, finite facts; `rfl` when both sides compute to the same value.
2. `omega` for linear integer arithmetic (after `intro`). Unfold model definitions first:
   `simp only [f]` or `unfold f`, then `split <;> omega` for `if` chains.
3. `simp [f]`, `simp_all [f]` for list and option goals; add `<;> omega` for the arithmetic left.
4. Induction for folds and recursion over lists: generalize the accumulator in a helper lemma
   (`∀ acc, xs.foldl g acc = ...`), prove it by `induction xs` with `intro acc`, then specialize.
5. Python builtins are prelude functions: `Apex.pyFloorDiv` (`//`), `Apex.pyMod` (`%`),
   `Apex.pyAbs`, `Apex.pyRange`, `Apex.pySum` (a `foldl`). Unfold them by name.

## Reading Lean errors

`apex verify` (or the `apex_verify` tool) returns, per failed property, `lean_errors` with the
file, line, column, message and the goal state. Fix the cause the goal shows:

- `unsolved goals ... ⊢ <goal>`: the goal after your tactics; pick the tactic for its shape.
- `omega could not prove the goal`: a non-linear term or an unfolded definition is left; unfold
  it, or prove the non-linear step with a lemma.
- `unknown identifier`: a lemma name that does not exist at this Mathlib version; search with a
  different tactic (`simp?`, `exact?` locally) or prove it inline.
- `motive is not type correct` / `failed to generalize`: generalize the accumulator first.

Forbidden anywhere: `sorry`, `admit`, `native_decide`, `axiom`, `unsafe`, `implemented_by`,
`#eval`, macros and custom syntax. The verdict path rejects them; do not try.

Retry within the configured budget (`retries` in apex.toml), then stop and report the property
as `unproven` with Apex's reason.
