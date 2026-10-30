# Apex

Apex takes a Python function plus whatever intent you already have (an English spec, a
docstring, tests, or just the source), turns that intent into Lean 4 theorems, and tries to prove
them. You get either a machine-checked proof or a gap report that says what could not be proven,
why, and what would have to change.

**New here? Start with the [quickstart](docs/QUICKSTART.md), and read
[what Apex cannot prove](docs/LIMITS.md) before trusting a verdict.**

**Status: Phase 5 (hardening).** The eval corpus has the handoff's composition: 121 cases,
including ten functions written by Claude Code and Codex from one-line prompts and left
unedited. False `proven` is 0 and all 121 cases match their expected verdicts. Growing the corpus
surfaced real bugs, now fixed:

- calls into user code that hang (a loop over `range(2**40)`) or hit the recursion limit;
- results past Python's 4300-digit integer limit;
- a fidelity check that ignored stated preconditions;
- a conflict check that ran the code under test;
- pure code labelled impure;
- Lean reserved words (`repeat`) breaking the model.

Phase 4 (MCP server and Agent Skill) and Phase 3 (`apex check`, model proofs, SARIF) are
underneath. Next, Phase 6: TLA+ model checking and fuzzing for what Lean cannot model, as new
non-proof verdicts.

## Quick tour

```sh
apex check src/                       # engine mode: every public function, end to end
apex check src/ --sarif apex.sarif    # ... and a SARIF file for code-scanning UIs
apex explain                          # plain English for each verdict in the last report
apex suggest                          # next steps, with a regression test per counterexample
```

The same pipeline, one verb at a time (what an agent drives in host mode):

```sh
apex analyze  mathx.py::clamp        # purity, Lean model, Intent Model from every source
apex intent propose mathx.py::clamp  # a model formalizes English items without a hint
apex scaffold mathx.py::clamp        # .apex/lean/mathx/clamp/{Model,Spec}.lean, drafted theorems
apex review   mathx.py::clamp --approve P1,P2   # humans only: needs an interactive terminal
apex prove    mathx.py::clamp --tactics-only    # tactic tiers fill open proofs, then verify
apex prove    mathx.py::clamp        # tactic tiers, then a model with Lean's errors fed back
apex refute   mathx.py::clamp        # counterexamples on the real Python
apex verify   mathx.py::clamp        # Lean verdicts + fidelity check + refutation
```

Verdicts and exit codes follow the handoff contract: `proven` needs an approved statement, a cold
Lean compile with clean axioms, and a fidelity check in which the compiled Lean model agrees
with the real Python on 200 Hypothesis-generated inputs. Without approval the best result is
`proven_unreviewed` (exit 2). A counterexample replayed on the real Python gives `refuted`
(exit 1), whatever Lean says.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every approved property is proven |
| 1 | any `refuted`, `fidelity_failed` or `contradiction` (or a gap verdict listed in `fail_on`) |
| 2 | gaps only: `unproven`, `out_of_scope`, `proven_unreviewed`, `proven_conditional` |
| 3 | tool error: Lean missing, bad config or target, a usage error, a crash, `--timeout` exceeded |

Failures always win over gaps. A function that is out of scope, or has no intent at all, counts
as a gap, so exit 0 is never vacuous. Usage errors and crashes exit 3, never Click's 2 or
Python's 1, which CI would read as "gaps" and "refuted".

## Inside a coding agent: MCP server and Agent Skill

```sh
apex skill install                    # .agents/skills/apex-verify, linked from .claude/skills
apex skill install --with-mcp         # ... and the `apex` MCP entry in each harness's config
apex skill install --harness claude --harness codex --user   # home-directory locations
apex mcp                              # the stdio server a harness starts
```

The skill (`skill/apex-verify/`, one canonical folder following the Agent Skills spec) teaches any
coding agent the host-mode loop: analyze, formalize intent, scaffold, get human approval, prove,
refute, report. It uses the `apex_*` MCP tools when the harness has them and the CLI through
`scripts/apex_run.py` when it does not, so a shell is enough. One copy in `.agents/skills` serves
Codex, Gemini CLI, Cursor and VS Code / Copilot; `.claude/skills/apex-verify` links to it.

| `--harness` | Skill directory (project / `--user`) | MCP config written by `--with-mcp` |
|---|---|---|
| `claude` | `.claude/skills` / `~/.claude/skills` | `.mcp.json` / `~/.claude.json` |
| `codex` | `.agents/skills` / `~/.agents/skills` | `~/.codex/config.toml` (`[mcp_servers.apex]`) |
| `gemini` | `.agents/skills` / `~/.agents/skills` | `.gemini/settings.json` / `~/.gemini/settings.json` |
| `cursor` | `.agents/skills` (project only) | `.cursor/mcp.json` |
| `vscode` | `.agents/skills` (project only) | `.vscode/mcp.json` (`servers`) |

Config files are merged, never overwritten: other servers and keys are kept, an `apex` entry
that is not Apex's is left alone and reported, TOML is appended so comments survive, and a skill
folder Apex did not install is never replaced. The command prints every file it writes.

The MCP tools mirror the deterministic verbs (`apex_analyze`, `apex_intent_submit`,
`apex_scaffold`, `apex_prove`, `apex_verify`, `apex_refute`, `apex_explain`, `apex_suggest`,
`apex_review_status`, and `apex_check` when a provider is configured). No tool can approve a
statement. `apex_verify` returns, for each rejected proof, Lean's errors with the line in the
spec file the agent edits and the goal state.

`packaging/` holds a Claude Code plugin and a Gemini CLI extension, generated from the canonical
folder (`uv run python -m apex.skill.packaging`; a test fails if they drift).

## Invariants

1. Lean 4 is the sole trust anchor. Only a successful cold Lean compile can produce `proven`.
2. No `sorry`, `admit` or non-standard axiom in anything reported as proven. The only allowed
   axioms are `propext`, `Classical.choice` and `Quot.sound`, checked with `#print axioms`.
3. LLM output is a proposal, never a verdict.
4. Theorem statements need human approval before they count.
5. The gap report is a first-class output, not an error.
6. A concrete counterexample can yield `refuted` from outside Lean; it can never yield `proven`.
7. Every Lean model is cross-checked against the real Python before its proof is reported.

## Install

```sh
uv build && uv tool install dist/apex_verify-*.whl   # not on PyPI yet; the name is an open decision
apex init                            # one-time: Lean toolchain + prebuilt Mathlib cache (~7 GB)
apex doctor
```

The wheel ships the pinned Lean project as a template. `apex init` copies it to
`~/.apex/lean/<pin>` (`APEX_HOME` overrides `~/.apex`) and fetches the toolchain and Mathlib cache
there, outside the package, so upgrading Apex keeps the cache and every project shares it. Until
then, every Lean verb exits 3 with a pointer to `apex init`; refutation works without Lean.
Windows is supported through WSL.

From a checkout:

```sh
uv sync --all-extras
uv run apex init
```

Lean and Mathlib are pinned in `lean/` (`lean-toolchain`, `lake-manifest.json`; Mathlib
`v4.35.0`). Upgrades are deliberate PRs re-run against the corpus.

## CI

```yaml
- run: apex check src/ --sarif apex.sarif   # exit 1 fails the job; gaps (2) pass unless fail_on says otherwise
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with: { sarif_file: apex.sarif }
```

In SARIF the verdict is the rule ID. `refuted`, `fidelity_failed` and `contradiction` are errors,
`unproven` is a warning, and `proven` (kind `pass`), `proven_unreviewed` (kind `review`) and
`out_of_scope` (kind `notApplicable`) carry level `none`. Approvals in `.apex/reviews.json` are
committed, so `proven` reproduces in CI without a human.

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

Two steps can use a model, and both are opt-in (`model_provider = "none"` by default):
formalizing English prose into hints, and proposing proofs.

| `model_provider` | Reaches the model through |
|---|---|
| `anthropic`, `openai` | an API key in `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (`apex-verify[anthropic]`, `[openai]`) |
| `claude-code`, `codex` | the coding agent CLI already installed and logged in, run headless with its tools off |
| `host` | the agent driving Apex writes hints and proofs itself (`apex intent submit`, `apex verify`) |

**Hints.** The model is shown the signature, docstring and spec items, but not the function body,
so it formalizes the intent rather than the code (`--from-source` opts in to reading the code, at
the lowest confidence). Every hint is parsed against the real signature; invalid ones come back as
rejections. A proposal at best becomes a drafted statement that still needs approval.

**Proofs.** For each theorem the tactic tiers leave open, the model sees the Lean model, the
prelude, the statement and the tactics that already failed, and returns a proof, helper lemmas
named `<ID>_...`, and Mathlib imports. Apex rebuilds the spec around the old statement and refuses
the answer unless every existing statement is byte-for-byte unchanged and nothing but those
lemmas was added; forbidden tokens (`sorry`, `native_decide`, `axiom`, macros, ...) are refused
before Lean runs. A candidate is compiled; Lean's errors and goal states go back for up to
`retries` more attempts. A proof that compiles is written into the spec and then judged by the
cold verdict path like any other. Theorems the real Python already refutes (examples, Hypothesis)
are not sent to the model at all. The report records which model proposed each proof
(`provenance.proof_by`), for debugging only.

## Spec files are a trust boundary

The approved statement text is what a human signs off on, so nothing else in a spec may be able
to change what it means. A spec may contain only imports and `theorem`/`lemma` declarations: no
`def`, `instance`, `notation`, `attribute` or `set_option` (beyond a per-theorem heartbeat cap).
The verdict file always sets `autoImplicit false`, so a typo in a statement is an error rather
than a silently universally quantified variable.

## Tests and evals

```sh
uv run pytest -m "not lean and not install"   # unit tests, exit codes through `python -m apex`
uv run pytest -m lean         # canaries (10 good + 11 bad), end-to-end and engine-mode tests
uv run pytest -m install      # builds the wheel, `uv tool install`s it, drives the binary
uv run python evals/run.py --jobs 4                  # eval corpus + scoreboard (tactics only)
uv run python evals/run.py --jobs 4 --provider claude-code   # + model proofs; closure per prover
uv run python scripts/bench_repl.py   # warm REPL (~6 ms) vs cold verdict (~0.34 s)
```

The eval scoreboard reports false proven (must be 0), statement fidelity (drafted statements
Lean shows equivalent to a hand-written reference), proof closure split by prover, refutation
recall, conflict detection, and which intent sources produced a model. The corpus has 49
known-good functions, 32 planted bugs, 15 spec/code conflicts, 15 out-of-scope functions and 10
AI-generated ones. Tactics only (`--jobs 4`):

| | |
|---|---|
| false proven | 0 |
| cases matching expectations | 121/121 |
| statement fidelity | 56/82 drafted statements equivalent to a reference |
| proof closure (true statements) | 113/217 (111 tactics, 2 supplied) |
| known-good functions fully proven | 9/49 |
| refutation recall | 49/49 |
| conflict detection | 18/18 |
| out-of-scope reason accuracy | 15/15 |
| AI-generated functions inside the Lean subset | 3/10 (the other 7: refutation only) |
| median time per function | 8.2 s |

With `--provider claude-code --retries 2` on the earlier 46-case corpus, a model closed every
true statement the tactics left open (82/82, 26 by the model) with false proven still 0. That
run has not been repeated on the 121-case corpus yet.

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
src/apex/prove/      tactic tiers (one Lean run), model proof search with the fixer loop
src/apex/fidelity/   Hypothesis bridge (Lean model vs Python); refutation (CrossHair, Hypothesis)
src/apex/lean/       pinned project, guard, cold verdict compiler + axiom audit, REPL pool
src/apex/report/     Rich, Markdown and SARIF renderers; explain / suggest
src/apex/pipeline.py the deterministic verbs every surface calls, and `check` (engine mode)
src/apex/cli/        Typer app
src/apex/mcp/        MCP server (stdio): the deterministic verbs as typed tools
src/apex/skill/      `apex skill install` / `apex mcp install`; packaging generator
evals/               corpus (known-good, planted bugs, conflicts, out of scope,
                     AI-generated) + runner
docs/                quickstart, what Apex cannot prove
lean/              pinned Lake project (Lean v4.35.0, Mathlib v4.35.0)
skill/apex-verify/ canonical Agent Skill (validated against the spec in CI)
packaging/         generated Claude Code plugin and Gemini CLI extension
evals/smoke/       harness smoke matrix (real agents, headless)
schemas/           exported JSON Schemas
tests/canaries/    M0 canary theorems
```

## Open decisions

Licence (Apache-2.0 vs MIT; `LICENSE` is a placeholder), package name (`apex-verify` is a
placeholder for PyPI), default engine-mode provider and models (Apex defaults to `none`; with
`anthropic` and no `model` set it uses `claude-opus-5-5`, and `openai` requires a `model`), talk
date, team, target domain, maturity claim and skill distribution channels. See section 12 of the
handoff.
