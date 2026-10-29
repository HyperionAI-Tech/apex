"""Tactic tiers: cheap, deterministic proof attempts that need no model.

Every open property is tried against every tier in a single probe file, each candidate as its own
`theorem` with a heartbeat cap, so one Lean run answers "which tiers close which properties".
Probe results are only a search signal. The chosen proofs go back through the full verdict path.
"""

import re
from dataclasses import dataclass

from apex.lean.compiler import run_lean
from apex.lean.guard import scan
from apex.lean.project import LeanProject
from apex.theorems.spec import Spec, SpecDecl

PROBE_HEARTBEATS = 200_000


def tiers(defs: list[str], decl: SpecDecl, model: str = "") -> list[str]:
    """Candidate proofs, cheapest first.

    The unfold set is the model's definitions plus any prelude helpers (`Apex.pySum`, ...) that
    the statement or the model mentions, so equivalences with Python builtins can close.
    """
    prelude = sorted(set(re.findall(r"\bApex\.py\w+", decl.statement + "\n" + model)))
    defs = [*defs, *prelude]
    d = " ".join(defs)
    unfold = "; ".join(f"(try unfold {f})" for f in defs)
    splits = "split <;> (try split) <;> (try split) <;> (try split)"
    out = [
        "by decide",
        "by intros; rfl",
        "by intros; omega",
        f"by intros; simp [{d}]",
        f"by intros; simp [{d}] <;> omega",
        f"by intros; {unfold}; {splits} <;> omega",
        f"by intros; {unfold}; {splits} <;> simp_all <;> omega",
        f"by intros; simp only [{d}]; {splits} <;> simp_all <;> omega",
        f"by intros; simp_all [{d}]",
    ]
    # Structural induction for list-valued arguments, the usual shape of fold properties.
    for var in re.findall(r"\(([\w' ]+) : List [^)]*\)", decl.statement):
        for v in var.split():
            out.append(f"by induction {v} <;> simp_all [{d}]")
            out.append(f"by induction {v} <;> simp_all [{d}] <;> omega")
    return out


@dataclass
class ProbeResult:
    closed: dict[str, str]
    """Property name -> first tier that closed it."""
    tried: dict[str, int]
    duration_s: float


def probe(
    header: str,
    model: str,
    namespace: str,
    spec: Spec,
    defs: list[str],
    targets: list[SpecDecl],
    project: LeanProject,
    timeout_s: float,
) -> ProbeResult:
    """Try every tier on every target in one cold Lean run."""
    lines = [header, f"namespace {namespace}", "", model]
    helpers = "".join(h.text + "\n" for h in spec.helpers)
    lines.append(helpers)
    body = "\n".join(lines) + "\n"
    spans: list[tuple[str, str, int, int]] = []  # (property, proof, first line, last line)
    tried: dict[str, int] = {}
    for decl in targets:
        candidates = tiers(defs, decl, model)
        tried[decl.name] = len(candidates)
        for k, proof in enumerate(candidates):
            if scan(proof):  # tiers are ours, but never let a forbidden token into a probe
                continue
            start = body.count("\n") + 1
            block = (
                f"set_option maxHeartbeats {PROBE_HEARTBEATS} in\n"
                f"theorem {decl.name}__apex_tier{k} {decl.statement} :=\n  {proof}\n\n"
            )
            body += block
            spans.append((decl.name, proof, start, start + block.count("\n") - 1))
    body += f"end {namespace}\n"

    run = run_lean(body, project, timeout_s)
    if run.code is None:
        return ProbeResult({}, tried, run.duration_s)
    bad_lines = [m.line for m, _ in run.messages if m.severity == "error"]
    shared_error = any(line < spans[0][2] for line in bad_lines) if spans else True
    closed: dict[str, str] = {}
    if not shared_error:  # an error in the model or helpers poisons every candidate
        for name, proof, lo, hi in spans:
            if name not in closed and not any(lo <= line <= hi for line in bad_lines):
                closed[name] = proof
    return ProbeResult(closed, tried, run.duration_s)
