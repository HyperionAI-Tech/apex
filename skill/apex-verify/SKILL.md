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

> Status: early. The deterministic verbs exist (`apex analyze`, `apex scaffold`,
> `apex prove --tactics-only`, `apex verify`, `apex review`); the full host-mode workflow lands
> with the MCP server in Phase 4. The supported Python subset is in
> [references/LEAN_SUBSET.md](references/LEAN_SUBSET.md).

## Hard rules

- Never describe anything as proven unless Apex's verdict says `proven`.
- Never edit `.apex/reviews.json`. Approval is the user's, in their own terminal.
- Never weaken a theorem statement to make a proof pass without new approval.
- Never use `sorry`, `admit`, `axiom`, `unsafe` or `native_decide`; the axiom audit rejects them.
- Present Apex's report table verbatim, then summarise in one paragraph.
