---
name: apex-verify
description: >-
  Formally verify Python functions with Lean 4 using Apex. Use when the user asks
  to prove, verify, or formally check a Python function; when you have just written
  or changed a pure Python function and correctness matters; or when asked for a
  gap report, counterexample, or proof of a property such as bounds, sorting,
  or invariants. Produces proven / refuted / gap verdicts from Apex, never your own.
license: Apache-2.0
compatibility: Requires Python 3.12+, uv, and network access on first run to fetch the Lean toolchain and Mathlib cache (several GB of disk).
metadata:
  author: hyperion-ai
  version: "0.1.0"
  apex-min-version: "0.1.0"
allowed-tools: Bash(apex:*) Bash(uv:*) Read
---

# Apex verify

> Status: early. The deterministic verbs and engine mode (`apex check`) exist; the MCP server and
> the full host-mode workflow land in Phase 4. The supported Python subset is in
> [references/LEAN_SUBSET.md](references/LEAN_SUBSET.md); where properties come from and how to
> write formal hints is in [references/INTENT.md](references/INTENT.md).

## Workflow (a shell is enough)

1. `apex analyze <path.py::fn>`: purity, the Lean model, and the Intent Model fused from the
   English spec, docstring, tests and source. If it is `out_of_scope`, report the reason and the
   pure core, and stop (a pure function can still be refuted: step 5).
2. If items need a formal hint, write them as described in references/INTENT.md and run
   `apex intent submit <target> --file proposals.json`. Fix anything it rejects.
3. `apex scaffold <target>`: drafts a Lean theorem per formal hint. Show each to the user as the
   English sentence next to the Lean, and ask them to run `apex review <target> --approve <ids>`
   in their own terminal. Without them, results are `proven_unreviewed`.
4. `apex prove <target> --tactics-only`, then `apex verify <target>`. For what stays open, write
   the proof in the spec file and re-run `apex verify`, reading the goal states it reports.
5. `apex refute <target>` searches the real Python for counterexamples. A counterexample ends that
   property: fix the code or the intent, never the theorem.
6. Present Apex's report table verbatim (`apex verify <target> --markdown`), then summarise.
   `apex suggest <target>` lists the next step for each gap and a regression test per
   counterexample; `apex explain` words each verdict for the user.

## Hard rules

- Never describe anything as proven unless Apex's verdict says `proven`.
- Never edit `.apex/reviews.json`. Approval is the user's, in their own terminal.
- Never weaken a theorem statement to make a proof pass without new approval.
- Never write a formal hint that describes what the code does instead of what the intent says.
- Never use `sorry`, `admit`, `axiom`, `unsafe` or `native_decide`; the axiom audit rejects them.
- Present Apex's report table verbatim, then summarise in one paragraph.
