# Apex

Apex takes a Python function plus whatever intent you already have (an English spec, a
docstring, tests, or just the source), turns that intent into Lean 4 theorems, and tries to prove
them. You get either a machine-checked proof or a gap report that says what could not be proven,
why, and what would have to change.

**Status: Phase 1 (the Python spine).** The deterministic verbs work end to end on the supported
subset: `analyze`, `scaffold`, `prove --tactics-only`, `verify`, `review`. The eval corpus passes
the M1 gate (6/6 known-good functions fully proven, zero false proven). No LLM is involved yet:
statements come from a human or a coding agent, and proofs from the tactic tiers or a person.

## Quick tour

```sh
apex analyze  mathx.py::clamp        # purity, Intent Model skeleton (doctests, tests), Lean model
apex scaffold mathx.py::clamp        # .apex/lean/mathx/clamp/{Model,Spec}.lean
# write properties as `theorem P1 ...` in Spec.lean, then:
apex review   mathx.py::clamp        # statements in English + Lean, with review status
apex review   mathx.py::clamp --approve P1,P2   # humans only: needs an interactive terminal
apex prove    mathx.py::clamp --tactics-only    # tactic tiers fill open proofs, then verify
apex verify   mathx.py::clamp        # cold compile + axiom audit + fidelity check -> verdicts
```

Verdicts and exit codes follow the handoff contract: `proven` needs an approved statement, a cold
Lean compile with clean axioms, and a fidelity check in which the compiled Lean model agrees
with the real Python on 200 Hypothesis-generated inputs. Without approval the best result is
`proven_unreviewed` (exit 2).

## Invariants

1. Lean 4 is the sole trust anchor. Only a successful cold Lean compile can produce `proven`.
2. No `sorry`, `admit` or non-standard axiom in anything reported as proven. The only allowed
   axioms are `propext`, `Classical.choice` and `Quot.sound`, checked with `#print axioms`.
3. LLM output is a proposal, never a verdict.
4. Theorem statements need human approval before they count.
5. The gap report is a first-class output, not an error.
6. A concrete counterexample can yield `refuted` from outside Lean; it can never yield `proven`.
7. Every Lean model is cross-checked against the real Python before its proof is reported.

## Setup

```sh
uv sync --all-extras
uv run apex init     # pinned Lean toolchain + prebuilt Mathlib cache (~7 GB on disk)
uv run apex doctor
```

Lean and Mathlib are pinned in `lean/` (`lean-toolchain`, `lake-manifest.json`; Mathlib
`v4.35.0`). Upgrades are deliberate PRs re-run against the corpus.

## The verdict path

`apex.lean.compiler.compile_and_audit(source, theorem_names)` is the only code that can say a
theorem was proved. It:

1. screens the source (`apex.lean.guard`) for the holes the axiom audit cannot see:
   `set_option debug.skipKernelTC`, metaprogramming that could forge output (`run_cmd`, `macro`,
   `#eval`, …), `#exit`, and imports outside the pinned libraries;
2. appends a footer of `#print axioms` lines and compiles the file cold, in a clean temp dir;
3. accepts audit output only from messages positioned *inside that footer*, mapped by line, so
   nothing the submission prints can impersonate a clean audit;
4. marks a theorem `proved` only if Lean reported no errors anywhere, the guard found nothing,
   and every axiom is on the allowlist. Timeouts kill the whole process group.

The warm REPL pool (`apex.lean.repl`, via LeanInteract) is for the fast proof-search loop only and
can never produce a verdict.

`lake env` startup can cost far more than the compile itself (35–85 s against ~0.4 s on the dev
machine), so the environment it resolves is cached in `lean/.lake/apex-lean-env.json`, keyed on
the toolchain, manifest and lakefile, and `lean` is invoked directly with it.

The prelude (`lean/Apex/Prelude.lean`) is core-only on purpose: every verdict is a cold compile,
so whatever the prelude imports is paid on every check. Generated files import only the Mathlib
modules a given proof needs.

## Spec files are a trust boundary

The approved statement text is what a human signs off on, so nothing else in a spec may be able
to change what it means. A spec may contain only imports and `theorem`/`lemma` declarations: no
`def`, `instance`, `notation`, `attribute` or `set_option` (beyond a per-theorem heartbeat cap).
The verdict file always sets `autoImplicit false`, so a typo in a statement is an error rather
than a silently universally quantified variable.

## Tests and evals

```sh
uv run pytest -m "not lean"   # translator, purity, spec parser, review store, contracts, guard
uv run pytest -m lean         # canaries (10 good + 11 bad) and end-to-end pipeline tests
uv run python evals/run.py    # eval corpus + scoreboard; exits 1 on any false proven
uv run python scripts/bench_repl.py   # warm REPL (~6 ms) vs cold verdict (~0.34 s)
```

Each canary declares its expected outcome in its first line. Bad canaries that cheat
(`sorry`, `admit`, `native_decide`, a user `axiom`, a forged audit line) also assert that the
**axiom audit alone** rejects them, so a guard that happens to fire first can never hide a
regression in the real trust anchor.

## Layout

```
src/apex/core/       contracts: Verdict, IntentModel, Report, ApexConfig (+ JSON Schema export)
src/apex/analyze/    target resolution, supported types, purity classifier + pure-core slicer
src/apex/intent/     Intent Model skeleton: doctest and test-assertion mining, conflicts
src/apex/translate/  Python -> Lean model for the MVP subset; value <-> Lean literals
src/apex/theorems/   spec-file parser (trust boundary) and the review store
src/apex/prove/      tactic tiers, probed in one Lean run
src/apex/fidelity/   Hypothesis bridge: compiled Lean model vs real Python
src/apex/lean/       pinned project, guard, cold verdict compiler + axiom audit, REPL pool
src/apex/report/     Rich and Markdown renderers
src/apex/pipeline.py the deterministic verbs every surface calls
src/apex/cli/        Typer app
evals/               corpus (known-good, planted bugs, conflicts, out of scope) + runner
lean/              pinned Lake project (Lean v4.35.0, Mathlib v4.35.0)
skill/apex-verify/ canonical Agent Skill (skeleton; validated against the spec in CI)
schemas/           exported JSON Schemas
tests/canaries/    M0 canary theorems
```

## Open decisions

Licence (Apache-2.0 vs MIT; `LICENSE` is a placeholder), package name (`apex-verify` is a
placeholder for PyPI), default engine-mode provider, talk date, team, target domain, maturity
claim and skill distribution channels. See section 12 of the handoff.
