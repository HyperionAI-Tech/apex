# Verdicts: what each one means and how to word it

Apex gives every property exactly one verdict. Use the verdict word as Apex gives it; never
round a gap up to a pass.

| Verdict | What it means | Say to the user |
|---|---|---|
| `proven` | Lean proved an approved statement; axioms clean; the Lean model agreed with the real Python on generated inputs | "Apex proved P1." |
| `proven_unreviewed` | As `proven`, but no person approved the statement | "Lean proved P1, but the statement is not approved yet, so it does not count as proven. Please read it and run `apex review <target> --approve P1` in your terminal if it says what you mean." |
| `proven_conditional` | Proven only under an assumption the source does not state | "P1 holds only if <precondition>; the code does not check that." |
| `refuted` | An input on which the real Python breaks the property, replayed in Python | "P1 is false: `f(x=0)` returns -1." Then fix the code or the intent. |
| `fidelity_failed` | The Lean model and the Python disagree on an input; any proof was discarded | "Apex could not model this function faithfully for input ...; this is a translator limitation, not a proof." |
| `contradiction` | Two intent sources disagree | "Your docstring and your test disagree about f(5); which is right?" |
| `unproven` | No proof, no counterexample within budget | "P1 is not proven (reason code ...). It is a gap, not a pass." |
| `out_of_scope` | Impure or unsupported construct | "Apex cannot verify f: <reason>. The pure core is ..." |

Exit codes: 0 every approved property proven; 1 any refuted, fidelity_failed or contradiction;
2 gaps only; 3 tool error.

Rules:
- `refuted` ends that property. Change the code (if the property is the intent) or the source of
  the intent (if the code is right). Never weaken the theorem to make the counterexample go away.
- Present Apex's table verbatim (`apex verify <target> --markdown`, or `scripts/apex_run.py`),
  then summarise in one paragraph.
