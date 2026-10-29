# Apex

Apex takes a Python function plus whatever intent you already have (an English spec, a
docstring, tests, or just the source), turns that intent into Lean 4 theorems, and tries to prove
them. You get either a machine-checked proof or a gap report that says what could not be proven,
why, and what would have to change.

**Status: Phase 2 (three inputs + fusion).** English specs, docstrings, tests and the source
each produce an Intent Model; the sources are fused, and disagreements between them are reported
as `contradiction`. Formal hints are turned into Lean statements deterministically, and every
property is also attacked on the real Python (examples, Hypothesis, CrossHair). The eval corpus
passes the M2 gate: 46/46 cases, zero false proven, 22/22 planted bugs refuted, 8/8 conflicts
detected. A model is only needed to formalize English prose, and can be reached through an API
key or a coding agent CLI you already have.

## Quick tour

```sh
apex analyze  mathx.py::clamp        # purity, Lean model, Intent Model from every source
apex intent propose mathx.py::clamp  # a model formalizes English items without a hint
apex scaffold mathx.py::clamp        # .apex/lean/mathx/clamp/{Model,Spec}.lean, drafted theorems
apex review   mathx.py::clamp --approve P1,P2   # humans only: needs an interactive terminal
apex prove    mathx.py::clamp --tactics-only    # tactic tiers fill open proofs, then verify
apex refute   mathx.py::clamp        # counterexamples on the real Python
apex verify   mathx.py::clamp        # Lean verdicts + fidelity check + refutation
```

Verdicts and exit codes follow the handoff contract: `proven` needs an approved statement, a cold
Lean compile with clean axioms, and a fidelity check in which the compiled Lean model agrees
with the real Python on 200 Hypothesis-generated inputs. Without approval the best result is
`proven_unreviewed` (exit 2). A counterexample replayed on the real Python gives `refuted`
(exit 1), whatever Lean says.

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

## Intent: four sources, one model

| Source | Read as |
|---|---|
| English spec (`<module>.spec.md`, or `--english FILE`) | One requirement per list item; a trailing hint in backticks makes it formal |
| Docstring | Doctests (examples); PEP 316 `pre:` / `post:` lines (formal) |
| Tests (`test_<module>.py`) | `assert f(...) == v` and `parametrize` rows (examples); Hypothesis `@given` tests (properties, with strategy bounds and `assume` as premises) |
| Source | Leading `assert`s (preconditions) |

A *formal hint* is a Python boolean expression over the parameters and `r` (the result), with
`->` for implication: `lo <= hi -> lo <= r <= hi`. The grammar is an allowlisted subset, parsed
before anything evaluates it (`skill/apex-verify/references/INTENT.md`). The same hint is
evaluated on the real Python for conflicts and counterexamples, and translated by the same code
that translates the function into the Lean statement a person approves.

Fusion merges the same claim from several sources, keeps property IDs stable across runs, and
reports `contradiction` when sources disagree: two examples with the same input, or an example
that a property's hint rejects. Nothing in a conflict is checked against the code until a person
decides which source is right.

## Models: proposals only, reached however you like

Only one step needs a model: turning English prose into formal hints. It is opt-in
(`model_provider = "none"` by default) and always goes through the same checks:

| `model_provider` | Reaches the model through |
|---|---|
| `anthropic`, `openai` | an API key in `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (`apex-verify[anthropic]`, `[openai]`) |
| `claude-code`, `codex` | the coding agent CLI already installed and logged in, run headless with its tools off |
| `host` | the agent driving Apex writes the hints itself and runs `apex intent submit` |

The model is shown the signature, docstring and spec items, but not the function body, so it
formalizes the intent rather than the code (`--from-source` opts in to reading the code, at the
lowest confidence). Every hint it returns is parsed against the real signature; invalid ones come
back as rejections. A proposal at best becomes a drafted statement that still needs approval.

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

The eval scoreboard reports false proven (must be 0), statement fidelity (drafted statements
Lean shows equivalent to a hand-written reference: 35/35), proof closure (55/82 true statements
closed by tactics), refutation recall (22/22, one of them only CrossHair finds), conflict
detection (8/8), and which intent sources produced a model.

Each canary declares its expected outcome in its first line. Bad canaries that cheat
(`sorry`, `admit`, `native_decide`, a user `axiom`, a forged audit line) also assert that the
**axiom audit alone** rejects them, so a guard that happens to fire first can never hide a
regression in the real trust anchor.

## Layout

```
src/apex/core/       contracts: Verdict, IntentModel, Report, ApexConfig (+ JSON Schema export)
src/apex/analyze/    target resolution, supported types, purity classifier + pure-core slicer
src/apex/intent/     English, docstring, test and source miners; hint language; fusion;
                     proposals (the model-facing contract)
src/apex/llm/        providers: API keys (Anthropic, OpenAI) or agent CLIs (Claude Code, Codex)
src/apex/translate/  Python -> Lean model for the MVP subset; value <-> Lean literals
src/apex/theorems/   spec-file parser (trust boundary), statement drafting, review store
src/apex/prove/      tactic tiers, probed in one Lean run
src/apex/fidelity/   Hypothesis bridge (Lean model vs Python); refutation (CrossHair, Hypothesis)
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
placeholder for PyPI), default engine-mode provider and models (Apex defaults to `none`; with
`anthropic` and no `model` set it uses `claude-opus-5-5`, and `openai` requires a `model`), talk
date, team, target domain, maturity claim and skill distribution channels. See section 12 of the
handoff.
