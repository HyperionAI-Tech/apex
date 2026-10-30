# Apex architecture

The [README](../README.md#how-it-works) gives the overview: pipeline, layers and design
principles. This page covers the parts that carry trust, in enough detail to review them.

## Contents

- [One pipeline, three surfaces](#one-pipeline-three-surfaces)
- [The verdict path](#the-verdict-path)
- [Spec files are a trust boundary](#spec-files-are-a-trust-boundary)
- [Translation](#translation)
- [Intent: mining and fusion](#intent-mining-and-fusion)
- [Statements and approval](#statements-and-approval)
- [Proof search](#proof-search)
- [The fidelity bridge](#the-fidelity-bridge)
- [Refutation](#refutation)
- [Fuzzing campaigns](#fuzzing-campaigns)
- [TLA+ for concurrent code](#tla-for-concurrent-code)
- [Workspace layout](#workspace-layout)
- [Pinning and installation](#pinning-and-installation)

## One pipeline, three surfaces

`apex.pipeline` holds the deterministic verbs: analyze, scaffold, prove, verify, refute, fuzz,
check, the TLA+ checks, and review status. The CLI (`apex.cli`), the MCP server (`apex.mcp`) and
the Agent Skill (`skill/apex-verify`, through the CLI or MCP) all call these functions, so the
surfaces cannot disagree. Every `--json` output is a pydantic model with a published JSON Schema
in `schemas/`. A test fails if a command has no schema, and CI fails if a schema drifts.

Two rules are enforced by what is *not* exposed:

- **No surface can approve a statement.** Approval is `apex review --approve`, and it requires an
  interactive terminal (stdin and stdout both TTYs), so an agent's shell cannot approve on a
  person's behalf. MCP has no approve tool at all.
- **Model steps run only in engine mode, and only with a configured provider.** In host mode
  the driving agent does the model work and the tools check it.

The MCP server serializes tool calls. Hypothesis is not thread-safe, and the TLA+ recorder sets
process-wide tracing and switch-interval state.

## The verdict path

`apex.lean.compiler.compile_and_audit(source, theorem_names)` is the only code that can report
that a theorem was proved. It:

1. **Screens the source** (`apex.lean.guard`) for what the axiom audit cannot see:
   - `set_option debug.skipKernelTC`;
   - metaprogramming that could forge output (`run_cmd`, `macro`, `#eval`, ...);
   - `#exit`;
   - imports outside the pinned libraries.
2. **Appends a footer** of `#print axioms` lines and compiles the file **cold**, in a clean temp
   directory.
3. **Reads the audit only from messages positioned inside that footer**, mapped by line, so
   nothing the submission prints can impersonate a clean audit.
4. **Marks a theorem proved only if all of these hold:**
   - Lean reported no errors anywhere;
   - the guard found nothing;
   - every axiom is in `propext`, `Classical.choice`, `Quot.sound`.

   Timeouts kill the whole process group.

The warm REPL pool (`apex.lean.repl`, via LeanInteract) speeds up proof search only and can never
produce a verdict.

The canary suite (`tests/canaries/`) includes proofs that cheat:

- `sorry` and `admit`;
- `native_decide`;
- a user `axiom`;
- a forged audit line.

Each one asserts that the **axiom audit alone** rejects it, so a guard that happens to fire first
cannot hide a regression in the real trust anchor.

Performance notes:

- **`lake env` startup** can cost far more than the compile itself (tens of seconds against
  ~0.4 s). The environment it resolves is cached in `lean/.lake/apex-lean-env.json`, keyed on the
  toolchain, manifest and lakefile, and `lean` is invoked directly.
- **The prelude** (`lean/Apex/Prelude.lean`) is core-only, because every verdict is a cold
  compile and whatever it imports is paid each time. Generated files import only the Mathlib
  modules a proof needs.

## Spec files are a trust boundary

A person approves statement text, so nothing else in a spec may change what that text means.

- **What a spec may contain.** Only imports and `theorem`/`lemma` declarations. No `def`,
  `instance`, `notation`, `attribute` or `set_option`, except a per-theorem heartbeat cap.
- **Implicit variables are off.** The verdict file always sets `autoImplicit false`, so a typo in
  a statement is an error rather than a silently universally quantified variable.
- **Hashes.** `apex.theorems.spec` parses declarations and hashes each statement. Approval is
  keyed by (function hash, property ID, statement hash), so an edit to either the function or
  the statement voids it.

## Translation

`apex.translate.lean` turns a function, and the module functions it calls, into a Lean model.

| Supported | Not supported |
|---|---|
| ints (Python's floor `//` and `%`) and bools | `str`, `float`, `dict`, `set`, classes |
| lists, fixed tuples, `Optional` returns | indexing |
| `if` / `elif` / `else`, conditional expressions | `while` |
| `for` over lists and `range` (rewritten as `foldl`) | `return`, `break` or `continue` inside a loop |
| list comprehensions, slices with literal bounds | exceptions |
| `len`, `abs`, `sum`, two-argument `min`/`max`, `any` | globals, nested functions |

Anything else raises a `TranslationError` with a reason code and the line, which the report
shows as a gap. Lean reserved words used as Python names (`repeat`, `using`, ...) are escaped as
`«name»`. The list was checked by compiling `def <name>` for every Python keyword, builtin and
common identifier, with and without Mathlib.

Leading `assert`s are preconditions, not code. The model leaves them out, and drafted statements
assume them (partial correctness).

## Intent: mining and fusion

Each intent source has a miner in `apex.intent`:

| Miner | Reads |
|---|---|
| `docstring` | doctests, `pre:` / `post:` |
| `tests` | literal asserts and `parametrize` rows; Hypothesis `@given` bodies, with strategy bounds and `assume` as premises |
| `english` | `.spec.md` list items, with optional backticked hints |
| `source` | leading `assert`s |

`apex.intent.fuse` merges them:

- **Merging.** Canonically equal hints merge into one property that keeps every source.
- **Stable IDs.** IDs stay the same across runs as long as a hint is unchanged, so approvals
  and theorem names keep pointing at the same claim.
- **Conflicts.**
  - Two examples with the same input and different outputs conflict.
  - An example conflicts with a property when the property's hint, evaluated on the example,
    is false.
  - A hint that calls a module function is never checked this way: evaluating it would run the
    code under test, so a bug would pass for disagreeing sources.
  - Conflicted properties are `contradiction` and are not checked against the code.

Formal hints use an allowlisted Python subset, parsed before anything evaluates them
(`apex.intent.hints`). The same hint is evaluated on the real Python (for conflicts and
refutation) and translated by the function's translator into the Lean statement.

## Statements and approval

`apex.theorems.draft` builds one Lean theorem per formal hint.

- **Shape.** The parameters are binders, preconditions and premises become hypotheses, and the
  conclusion has the result replaced by the model's call.
- **Which preconditions.** A precondition is included only when the claim mentions everything
  it constrains.
- **Examples.** These become closed theorems such as `clamp 5 0 3 = 3`.
- **Review store.** Statements live in `Spec.lean` and approvals in `.apex/reviews.json`.
  `apex review` shows each statement in English and in Lean. TLA+ invariants are reviewed the
  same way, and approving one approves its whole bundle.

## Proof search

**Tactic tiers** (`apex.prove.tactics`) try fixed, cheap tactics in one Lean run:

- `decide`;
- `omega`;
- `simp` with the model's definitions;
- splitting `if`s;
- structural induction over list arguments.

**The model fixer loop** (`apex.prove.llm`) handles theorems the tiers leave open. The model
sees the prelude, the Lean model, existing lemmas, the statement and the tactics that already
failed, and it returns a proof, helper lemmas named `<ID>_...`, and imports. The answer is
refused before Lean runs unless:

- the spec rebuilds with every existing statement byte-for-byte unchanged;
- only those lemmas were added;
- no forbidden token appears.

A candidate is compiled, and Lean's errors and goal states go back for up to `retries` more
attempts. A proof that compiles is written into the spec and judged by the verdict path like any
other. Theorems the real Python already refutes are never sent to a model. Who proposed each
proof is recorded in the report's provenance, for debugging only.

## The fidelity bridge

A proof is about the Lean model, so `apex.fidelity.bridge` checks the model against the real
function before any proof is reported:

1. Generate 200 inputs. Integers mix small, medium and large ranges.
2. Keep only the inputs that satisfy the preconditions. Outside them the function promises
   nothing (Python may raise where Lean's total model returns a value).
3. Run the Python on them, smallest first. An input that hits the recursion limit, or needs more
   than 20 ms in CPython, is skipped and counted. Lean's interpreter is much slower on big lists.
4. Evaluate the compiled Lean model on the kept inputs with `#eval`.
5. Any disagreement, a Python exception, or argument mutation turns every proof of that function
   into `fidelity_failed`.

## Refutation

`apex.fidelity.refute` searches the real Python, cheapest search first:

1. the mined examples;
2. Hypothesis (300 inputs, filtered by preconditions and premises);
3. CrossHair, symbolic execution in a subprocess under a time budget.

Every input any search reports is **replayed in-process by the same evaluator** before it
becomes a counterexample, so `refuted` always means "Apex watched it happen".

Resource limits are skipped, never reported:

- A per-call budget (0.5 s, enforced with a per-thread trace function so it works off the main
  thread) catches loops over huge ranges.
- `RecursionError` is a resource limit like memory, and depends on the caller's stack depth.
- After three slow calls a search stops calling the function.

## Fuzzing campaigns

`apex.fidelity.fuzz` runs a time-budgeted campaign per property and reuses refutation's checks:
the same preconditions, skips and replay.

- **Hypothesis engine.** Seeded batches of 500 inputs until the budget runs out; failures are
  shrunk.
- **Atheris engine.** A subprocess (`python -m apex.fidelity.fuzz`), because libFuzzer exits the
  process when it is done.
  - The function, its module's helpers, the harness and the precondition check are
    instrumented. libFuzzer quits when its first, empty input covers nothing, and that input
    often fails a precondition.
  - Values come from libFuzzer's bytes with the same small/medium/large integer mix.
  - Any input it reports is replayed in the parent.

Evidence is stored in `.apex/fuzz/<module>/<function>/evidence.json`, keyed by the function's
hash and the property's hint text. `verify` uses it only while both still match:

- a clean campaign turns `unproven` or `out_of_scope` into `fuzzed`;
- a recorded counterexample is replayed again and becomes `refuted`.

## TLA+ for concurrent code

`apex.tla` checks concurrent code against a per-function bundle in `.apex/tla/<module>/<function>/`:

| File | Contract |
|---|---|
| `Spec.tla` | module `Spec` with `Init`, `Next`, `Spec`; PlusCal is translated on a copy |
| `Spec.cfg` | `SPECIFICATION Spec`, `CONSTANTS` (the bounds), `INVARIANT(S)`, each becoming `T_<Name>` |
| `harness.py` | `setup()`, `run(state)` (calls the real code), `observe(state)`, optional `check_<Name>(obs, done)` |

`apex tla check` runs four steps:

1. **Model check.**
   - It runs TLC once per invariant, without `-continue`, because TLC's first counterexample per
     invariant is a shortest one.
   - Success is never read from TLC's summary line. With `-continue`, TLC prints "No error has
     been found" even after reporting violations.
   - Each TLC, SANY or PlusCal run gets its own `java.io.tmpdir`. Parallel runs sharing the
     system one delete each other's unpacked standard modules.
2. **Record.**
   - It runs the harness 200 times with a tiny thread switch interval and random short sleeps at
     line events.
   - A trace function in every thread the run starts snapshots `observe(state)` after each line
     executed in the code's own files, compared by real path (macOS temp directories go through
     a symlink). It keeps a snapshot whenever the observed state changes.
   - Cached modules that would shadow the code under test are dropped first, so a long-lived
     process never checks stale code.
3. **Validate traces.** For each distinct trace, TLC gets a module `ApexTrace` that extends
   `Spec` with a counter `tl`.
   - `Next` steps that leave every observed variable unchanged are allowed, because the recording
     cannot see them.
   - Other steps must match the next recorded state.
   - If TLC can reach the end of the trace (reported as a violation of `TraceUnfinished`), the
     run is a behavior of the spec. If it cannot, the spec does not describe the code.
4. **Observe violations.** `check_<Name>(obs, done)` is evaluated on every recorded observation,
   so an invariant can be watched breaking in the real code.

Verdict precedence per invariant:

1. observed breaking in a real run: `refuted`;
2. a rejected trace: `fidelity_failed`;
3. TLC or conformance could not complete: `unproven` (`MODEL_CHECK_FAILED`);
4. a TLC violation no run showed: `unproven` (`SPEC_VIOLATION_NOT_REPRODUCED`, with TLC's trace);
5. otherwise: `model_checked`.

A check that cannot complete is never `model_checked`. Results are keyed by the bundle hash and
the function hash.

`apex tla draft` asks a model for a bundle. The draft is validated by structure, the PlusCal
translation and a SANY parse, with errors fed back for up to three attempts. It is written
pending review.

## Workspace layout

```
apex.toml                         configuration (optional)
.apex/
  reviews.json                    approvals (commit this)
  last_report.json                the last verify/check report
  intent/<mod>/<fn>/              fused Intent Model, proposals, proof provenance
  lean/<mod>/<fn>/Model.lean      generated model (regenerated every run; do not edit)
  lean/<mod>/<fn>/Spec.lean       statements and proofs (yours to edit; parsed as a trust boundary)
  fuzz/<mod>/<fn>/evidence.json   the latest fuzzing campaign per property
  tla/<mod>/<fn>/                 Spec.tla, Spec.cfg, harness.py, evidence.json
```

## Pinning and installation

These are pinned:

- Lean (`lean/lean-toolchain`);
- Mathlib (`lean/lake-manifest.json`, `v4.35.0`);
- TLC (`apex.tla.tlc`: v1.7.4, SHA-256 checked on install and on every use).

The wheel ships the Lean project as a template. `apex init` copies it to `~/.apex/lean/<pin>`
and fetches the toolchain and Mathlib cache there, outside the package, so every project and
every Apex upgrade shares them. Upgrading a pin is a deliberate change, re-run against the eval
corpus.
