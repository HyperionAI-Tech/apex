---
name: apex-verify
description: >-
  Formally verify Python, JavaScript and TypeScript functions using Apex (Lean 4
  proofs, TLA+ model checking, fuzzing). Use when the user asks to prove, verify, or
  formally check a function; when you have just written or changed a pure function
  and correctness matters; or when asked for a
  gap report, counterexample, or proof of a property such as bounds, sorting,
  or invariants. Produces proven / refuted / gap verdicts from Apex, never your own.
license: MIT
compatibility: Requires Python 3.12+, uv, and network access on first run to fetch the Lean toolchain and Mathlib cache (several GB of disk).
metadata:
  author: hyperion-ai
  version: "0.1.0"
  apex-min-version: "0.1.0"
allowed-tools: Bash(apex:*) Bash(uv:*) Read
---

# Apex verify

Apex proves or refutes properties of pure Python, JavaScript and TypeScript functions with
Lean 4, and gathers evidence (fuzzing, TLA+) where Lean cannot reach. You propose (formal hints,
proofs, specs); Apex checks; only Apex's verdicts count. Targets look like `path.py::function`
or `path.ts::function`. For JS/TS, read [references/JAVASCRIPT.md](references/JAVASCRIPT.md)
first: hints are JavaScript expressions there.

## Workflow

1. **Bootstrap.** Run `python scripts/ensure_apex.py`. If it reports the Lean toolchain missing,
   tell the user the one-time cost it prints before running `apex init --yes`.
2. **Pick the channel.** If `apex_*` MCP tools are available, use them. Otherwise run the CLI
   through `python scripts/apex_run.py <verb> <args>`, which prints a short summary and saves the
   full JSON under `.apex/`.
3. **Analyze.** `apex analyze <target>` (`apex_analyze`). If it is out of scope, report the reason
   code and the pure core; a pure function can still be refuted (step 7) and fuzzed (step 8).
   For concurrent code (`NEEDS_CONCURRENCY_TOOL`, threads, locks), use a TLA+ bundle instead:
   [references/TLA.md](references/TLA.md).
4. **Draft statements.** For English items listed under "needs a formal hint", write hints as in
   [references/INTENT.md](references/INTENT.md) and submit them (`apex intent submit <target>
   --file proposals.json`, or `apex_intent_submit`). Then `apex scaffold <target>` drafts a Lean
   theorem per hint. Show each to the user as the English sentence next to the Lean; see
   [references/REVIEW.md](references/REVIEW.md).
5. **Get approval.** Ask the user to run `apex review <target> --approve <ids>` in their own
   terminal. It requires an interactive confirmation, so you cannot run it for them. If no person
   is present, continue: results will be `proven_unreviewed`.
6. **Prove.** `apex prove <target> --tactics-only` (`apex_prove`) first. For what remains, write
   the proof in the spec file as in [references/PROOF_PLAYBOOK.md](references/PROOF_PLAYBOOK.md),
   run `apex verify <target>` (`apex_verify`), read the `lean_errors` (line, goal state) and retry
   up to the configured budget.
7. **Refute.** `apex refute <target>` (`apex_refute`). A counterexample ends that property: fix the
   code or the intent, never the theorem. `apex suggest <target>` writes a regression test that
   replays it.
8. **Fuzz what Lean left open.** For properties still `unproven` or `out_of_scope` on a pure
   function, `apex fuzz <target> --seconds 30` (`apex_fuzz`) runs a recorded campaign; `verify`
   then reports `fuzzed` (evidence, not proof) or `refuted`.
9. **Report.** Present Apex's report table verbatim (`apex verify <target> --markdown`, or the
   table `scripts/apex_run.py` prints), then summarise in one paragraph. What each verdict means
   and how to word it: [references/VERDICTS.md](references/VERDICTS.md); gap reason codes:
   [references/GAP_CODES.md](references/GAP_CODES.md); the supported Python subset:
   [references/LEAN_SUBSET.md](references/LEAN_SUBSET.md).

## Hard rules

- Never describe anything as proven unless Apex's verdict says `proven`. `fuzzed` and
  `model_checked` are evidence: say what ran and within which bounds.
- Never edit `.apex/reviews.json`. Approval is the user's, in their own terminal.
- Never weaken or reword a theorem statement to make a proof pass without new approval.
- Never write a formal hint that describes what the code does instead of what the intent says.
- Never use `sorry`, `admit`, `axiom`, `unsafe` or `native_decide`; the axiom audit rejects them.
- Present Apex's report table verbatim, then summarise in one paragraph.
