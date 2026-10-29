"""Statement fidelity: is a drafted statement the same claim as the hand-written reference?

"Judged equivalent" here means Lean proved `drafted ↔ reference` with a few generic tactics. It
is sound (a proof is a proof) but incomplete: a pair the tactics cannot relate counts as not
equivalent, so the metric is a lower bound.

Used only by the eval runner. Equivalence is never a verdict and never an approval in Apex itself.
"""

from apex.lean.compiler import run_lean
from apex.lean.project import LeanProject
from apex.theorems.spec import SpecDecl

TACTIC = """\
  first
  | exact Iff.rfl
  | (simp only [Int.ofNat_inj, Int.ofNat_le, Int.ofNat_lt, ge_iff_le, gt_iff_lt, List.mem_cons,
      List.not_mem_nil, or_false, decide_eq_decide]; done)
  | (constructor <;> intro hh <;> intros <;>
      first
      | (apply hh <;> first | assumption | omega | simp_all | exact default)
      | simp_all
      | omega
      | (simp_all <;> omega))
  | (simp only [List.mem_cons, List.not_mem_nil, or_false, decide_eq_decide]
      <;> constructor <;> intro hh <;> intros <;> simp_all <;> omega)"""


def closed_prop(decl: SpecDecl) -> str:
    """`(x : Int) (h : P) : Q` as the closed proposition `∀ (x : Int) (h : P), Q`."""
    s, depth = decl.statement, 0
    for i, c in enumerate(s):
        if c in "([{⟨":
            depth += 1
        elif c in ")]}⟩":
            depth -= 1
        elif c == ":" and depth == 0:
            binders, concl = s[:i].strip(), s[i + 1 :].strip()
            return f"(∀ {binders}, {concl})" if binders else f"({concl})"
    raise ValueError(f"no type in statement of {decl.name}")


def equivalent_pairs(
    namespace: str,
    model_text: str,
    pairs: list[tuple[SpecDecl, SpecDecl]],
    project: LeanProject,
    timeout_s: float = 300,
) -> set[tuple[str, str]]:
    """The (drafted, reference) name pairs Lean showed equivalent."""
    if not pairs:
        return set()
    head = (
        f"import Apex.Prelude\nset_option autoImplicit false\n\nnamespace {namespace}\n\n"
        f"{model_text}\n"
    )
    lines = head.count("\n") + 1
    blocks, spans = [], []
    for a, b in pairs:
        block = (
            f"set_option maxHeartbeats 200000 in\ntheorem apex_eqv_{a.name}_{b.name} :\n"
            f"    {closed_prop(a)} ↔\n    {closed_prop(b)} := by\n{TACTIC}\n"
        )
        spans.append((lines, lines + block.count("\n")))
        lines += block.count("\n") + 1
        blocks.append(block)
    text = head + "\n".join(blocks) + f"\nend {namespace}\n"
    run = run_lean(text, project, timeout_s)
    if run.code is None:
        return set()
    errors = [msg for msg, _ in run.messages if msg.severity == "error"]
    bad = {k for msg in errors for k, (lo, hi) in enumerate(spans) if lo <= msg.line <= hi}
    if any(not any(lo <= m.line <= hi for lo, hi in spans) for m in errors):
        return set()  # the model itself failed: nothing was shown
    return {(a.name, b.name) for k, (a, b) in enumerate(pairs) if k not in bad}
