# Apex

Apex takes a Python function plus whatever intent you already have (an English spec, a
docstring, tests, or just the source), turns that intent into Lean 4 theorems, and tries to prove
them. You get either a machine-checked proof or a gap report that says what could not be proven,
why, and what would have to change.

**Status: Week 1 (M0, the Lean harness).** Only the trust anchor exists so far: the cold verdict
compiler, the axiom audit, the canary suite and `apex doctor`. Nothing here verifies Python yet.

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

## Tests

```sh
uv run pytest -m "not lean"   # contracts, guard, parsers, skill spec conformance
uv run pytest -m lean         # M0 canary suite: 10 good + 11 bad theorems, cold verdict path
uv run python scripts/bench_repl.py   # warm REPL vs cold verdict latency
```

Each canary declares its expected outcome in its first line. Bad canaries that cheat
(`sorry`, `admit`, `native_decide`, a user `axiom`, a forged audit line) also assert that the
**axiom audit alone** rejects them, so a guard that happens to fire first can never hide a
regression in the real trust anchor.

## Layout

```
src/apex/core/     contracts: Verdict, IntentModel, Report, ApexConfig (+ JSON Schema export)
src/apex/lean/     pinned project, guard, cold verdict compiler + axiom audit, REPL pool
src/apex/cli/      Typer app: doctor, init, schema, dev compile
lean/              pinned Lake project (Lean v4.35.0, Mathlib v4.35.0)
skill/apex-verify/ canonical Agent Skill (skeleton; validated against the spec in CI)
schemas/           exported JSON Schemas
tests/canaries/    M0 canary theorems
```

## Open decisions

Licence (Apache-2.0 vs MIT; `LICENSE` is a placeholder), package name (`apex-verify` is a
placeholder for PyPI), default engine-mode provider, talk date, team, target domain, maturity
claim and skill distribution channels. See section 12 of the handoff.
