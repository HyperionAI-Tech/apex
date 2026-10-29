"""Eval corpus runner and scoreboard. Printed on every PR; gates every milestone after M1.

    uv run python evals/run.py [--case SUBSTRING] [--json]

Each case runs in a fresh temporary workspace: scaffold, add the reference spec, record the
reference statements as approved (they are the team's hand-written ground truth, which is the
review), prove with tactic tiers only, then verify.

Exit 1 when the false-proven rate is non-zero (a property expected to be refuted or
contradictory came out proven) or any case misses its expected verdict. Until CrossHair lands
(Phase 2), a planted bug that is `unproven` instead of `refuted` only lowers refutation recall.
"""

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from apex.core.verdict import LEAN_ONLY_VERDICTS, Verdict
from apex.lean.project import LeanProject
from apex.pipeline import Workspace, function_id, load_spec, prove_tactics, scaffold, verify

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
REVIEWER = "evals: reference statement"


@dataclass
class CaseResult:
    target: str
    category: str
    verdicts: dict[str, str] = field(default_factory=dict)
    expected: dict[str, str] = field(default_factory=dict)
    reason: str | None = None
    expected_reason: str | None = None
    tactic_closed: list[str] = field(default_factory=list)
    supplied: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    @property
    def false_proven(self) -> list[str]:
        """Properties expected to fail (refuted, contradiction) that came out proven anyway."""
        must_fail = {p for p, v in self.expected.items() if v in ("refuted", "contradiction")}
        return [
            p for p in must_fail if Verdict(self.verdicts.get(p, "unproven")) in LEAN_ONLY_VERDICTS
        ]

    def matches(self, prop: str) -> bool:
        want, got = self.expected[prop], self.verdicts.get(prop)
        # Until refutation lands, an unrefuted planted bug is a recall miss, not a failure.
        return got == want or (want == "refuted" and got == "unproven")

    @property
    def ok(self) -> bool:
        if self.error or self.false_proven:
            return False
        if self.category == "out_of_scope":
            return self.reason == self.expected_reason
        return all(self.matches(p) for p in self.expected)


def run_case(case: dict, project: LeanProject) -> CaseResult:
    res = CaseResult(
        target=case["target"],
        category=case["category"],
        expected=case.get("expected", {}),
        expected_reason=case.get("reason"),
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="apex-eval-") as tmp:
        work = Path(tmp)
        for src in CORPUS.glob("*.py"):
            shutil.copy(src, work / src.name)
        (work / "apex.toml").write_text("[apex]\n")
        ws = Workspace.discover(work)
        target = str(work / case["target"])
        try:
            sc = scaffold(target, ws, project)
            if sc.gap is not None or sc.spec_path is None:
                res.reason = str(sc.gap.reason_code) if sc.gap else "no model"
                return res
            spec_path = Path(sc.spec_path)
            if ref := case.get("spec"):
                spec_path.write_text(spec_path.read_text() + "\n" + (CORPUS / ref).read_text())
            spec = load_spec(spec_path)
            res.supplied = [d.name for d in spec.properties if d.has_proof]
            from apex.analyze.target import load_function

            info = load_function(target)
            store = ws.review_store
            for d in spec.properties:
                store.approve(
                    qualname=function_id(info),
                    property_id=d.name,
                    statement=d.full_statement,
                    function_sha256=info.sha256,
                    statement_sha256=d.statement_sha256,
                    reviewer=REVIEWER,
                )
            res.tactic_closed = list(prove_tactics(target, ws, project=project).closed)
            report = verify([target], ws, project=project, write_report=False)
            fn = report.functions[0]
            res.verdicts = {p.id: str(p.verdict) for p in fn.properties}
            if fn.gap:
                res.reason = str(fn.gap.reason_code)
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
        finally:
            res.seconds = round(time.monotonic() - started, 2)
    return res


def scoreboard(results: list[CaseResult]) -> dict:
    good = [r for r in results if r.category == "known_good"]
    bugs = [r for r in results if r.category == "planted_bug"]
    oos = [r for r in results if r.category == "out_of_scope"]
    good_props = [(r, p) for r in good for p in r.expected]
    proven = [(r, p) for r, p in good_props if r.verdicts.get(p) == Verdict.PROVEN]
    by_tactic = [(r, p) for r, p in proven if p in r.tactic_closed]
    by_supplied = [(r, p) for r, p in proven if p in r.supplied]
    bug_props = [(r, p) for r in bugs for p in r.expected]
    refuted = [(r, p) for r, p in bug_props if r.verdicts.get(p) == Verdict.REFUTED]
    fully_proven = [r for r in good if r.ok]
    false_proven = sum(len(r.false_proven) for r in results)
    return {
        "false_proven": false_proven,
        "proof_closure": f"{len(proven)}/{len(good_props)}",
        "closed_by_tactics": len(by_tactic),
        "closed_by_supplied_proof": len(by_supplied),
        "refutation_recall": f"{len(refuted)}/{len(bug_props)}",
        "out_of_scope_reason_accuracy": f"{sum(r.ok for r in oos)}/{len(oos)}",
        "statement_fidelity": "n/a (reference statements; no generated statements until P2)",
        "median_seconds_per_function": round(statistics.median(r.seconds for r in results), 2)
        if results
        else 0,
        "m1_known_good_fully_proven": f"{len(fully_proven)}/{len(good)}",
        "m1_gate": len(fully_proven) >= 5 and false_proven == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Only run cases whose target contains this text.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cases = tomllib.loads((ROOT / "cases.toml").read_text())["case"]
    if args.case:
        cases = [c for c in cases if args.case in c["target"]]
    project = LeanProject.locate()
    results = []
    for case in cases:
        r = run_case(case, project)
        results.append(r)
        if not args.json:
            mark = "ok  " if r.ok else "FAIL"
            got = r.reason or ", ".join(f"{p}={v}" for p, v in r.verdicts.items())
            print(f"{mark} {r.category:12} {r.target:28} {r.seconds:6.1f}s  {got}")
            if r.error:
                print(f"       error: {r.error}")
            if r.false_proven:
                print(f"       FALSE PROVEN: {', '.join(r.false_proven)}")
            for p, want in r.expected.items():
                if not r.matches(p):
                    print(f"       {p}: expected {want}, got {r.verdicts.get(p)}")
    board = scoreboard(results)
    if args.json:
        print(json.dumps({"scoreboard": board, "cases": [asdict(r) for r in results]}, indent=2))
    else:
        print("\nscoreboard")
        for k, v in board.items():
            print(f"  {k:32} {v}")
    failed = board["false_proven"] > 0 or any(not r.ok for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
