"""Eval corpus runner and scoreboard. Printed on every PR; gates every milestone after M1.

    uv run python evals/run.py [--case SUBSTRING] [--json] [--jobs N]
    uv run python evals/run.py --provider claude-code [--retries 2]   # engine mode: + a model

Each case runs in a fresh temporary workspace with the whole corpus copied in:

1. analyze: fuse the case's intent sources (English spec, docstring, tests, source) into an
   Intent Model, and record which sources contributed;
2. scaffold: draft a Lean statement for every formal hint (P1, ...) and example (E1, ...);
3. append the hand-written reference statements, renamed R1, R2, ... (ground truth);
4. review, mechanized: every R is approved (a person wrote it), and a drafted P is approved only
   if Lean proves it equivalent to some R. That equivalence rate is the statement-fidelity score;
5. prove with tactic tiers, then (with --provider) let a model try what they left open, then
   verify (Lean verdicts, fidelity check, refutation). Closure is reported per prover.

Exit 1 on any false proven (a planted-bug or conflict property proven although it was not
expected to be, or anything expected refuted/contradictory proven), or any case that misses its
expected verdicts.
"""

import argparse
import json
import re
import shutil
import statistics
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path

from equivalence import equivalent_pairs

from apex.analyze.target import load_function
from apex.core.verdict import EVIDENCE_VERDICTS, LEAN_ONLY_VERDICTS, Verdict
from apex.js import is_js
from apex.lean.project import LeanProject
from apex.llm.providers import Provider
from apex.pipeline import (
    Workspace,
    _analyze,
    function_id,
    fuzz_target,
    load_spec,
    prove_llm,
    prove_tactics,
    scaffold,
    tla_check_target,
    verify,
)

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
REVIEWER = "evals: reference statement"
EQUIV_REVIEWER = "evals: equivalent to a reference statement"
EXAMPLE_REVIEWER = "evals: literal example from a doctest or test"
BUNDLE_REVIEWER = "evals: reference TLA+ bundle"
CONFIG = "[apex]\nrefute_budget_s = 3\nllm_parallel = 2\n"
_HOLDS = {"proven", "proven_unreviewed", "proven_conditional", "unproven"}
"""`holds`: the statement is true of this code. Proving it is right; failing to is a closure gap."""
_MAY_PROVE = {"proven", "proven_unreviewed", "proven_conditional", "holds"}
SOURCE_KINDS = {"spec": "english", "docstring": "docstring", "test": "tests", "source": "source"}
STRICT = ("planted_bug", "conflict", "ai_generated", "concurrent")
"""Categories where anything proven must be expected proven (or holds)."""
CORPUS_TARGET = {
    "known_good": 50,
    "planted_bug": 30,
    "conflict": 15,
    "out_of_scope": 15,
    "ai_generated": 10,
    "concurrent": 4,
}
"""The handoff's composition (~120 functions), for the scoreboard's corpus line."""


@dataclass
class CaseResult:
    target: str
    category: str
    inputs: list[str] = field(default_factory=list)
    """Intent sources the case is meant to exercise."""
    sources: list[str] = field(default_factory=list)
    """Intent sources that actually contributed to the fused Intent Model."""
    verdicts: dict[str, str] = field(default_factory=dict)
    expected: dict[str, str] = field(default_factory=dict)
    reason: str | None = None
    expected_reason: str | None = None
    drafted: list[str] = field(default_factory=list)
    equivalent: dict[str, str] = field(default_factory=dict)
    """Drafted property -> the reference it was shown equivalent to."""
    references: list[str] = field(default_factory=list)
    tactic_closed: list[str] = field(default_factory=list)
    llm_closed: list[str] = field(default_factory=list)
    llm_attempts: int = 0
    supplied: list[str] = field(default_factory=list)
    counterexamples: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    error: str | None = None

    @property
    def false_proven(self) -> list[str]:
        """Proven although the corpus says it must not be: the one number that must stay 0.

        On planted-bug, conflict and AI-generated entries anything proven must be expected
        `proven` (or `holds`: true of this code, whether or not the tactics close it).
        """
        out = []
        for pid, got in self.verdicts.items():
            if Verdict(got) not in LEAN_ONLY_VERDICTS:
                continue
            want = self.expected.get(pid)
            if want in ("refuted", "contradiction", "unproven") or (
                self.category in STRICT and want not in _MAY_PROVE
            ):
                out.append(pid)
        return out

    @property
    def false_evidence(self) -> list[str]:
        """`fuzzed` or `model_checked` on a property the corpus says must fail: the second
        number that must stay 0 (evidence verdicts are gaps, but they must not hide a bug)."""
        return [
            pid
            for pid, got in self.verdicts.items()
            if Verdict(got) in EVIDENCE_VERDICTS
            and self.expected.get(pid) in ("refuted", "contradiction", "fidelity_failed")
        ]

    def matches(self, pid: str) -> bool:
        want, got = self.expected[pid], self.verdicts.get(pid)
        if want == "holds":
            return got in _HOLDS
        return got == want

    @property
    def missing_inputs(self) -> list[str]:
        return [i for i in self.inputs if i not in self.sources]

    @property
    def ok(self) -> bool:
        if self.error or self.false_proven or self.false_evidence or self.missing_inputs:
            return False
        if self.expected_reason is not None and self.reason != self.expected_reason:
            return False
        return all(self.matches(p) for p in self.expected)


def _reference_text(path: Path) -> str:
    """Reference theorems renamed P<k> -> R<k>, so they sit beside the drafted P<k>."""
    return re.sub(r"\btheorem P(\d+)\b", r"theorem R\1", path.read_text())


def run_case(
    case: dict, project: LeanProject, llm: Provider | None = None, retries: int = 2
) -> CaseResult:
    res = CaseResult(
        target=case["target"],
        category=case["category"],
        inputs=case.get("inputs", []),
        expected=case.get("expected", {}),
        expected_reason=case.get("reason"),
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="apex-eval-") as tmp:
        work = Path(tmp)
        for pattern in ("*.py", "*.md", "*.ts", "*.js", "*.mjs"):
            for src in CORPUS.glob(pattern):
                shutil.copy(src, work / src.name)
        if bundle := case.get("tla"):
            info = load_function(str(work / case["target"]))
            shutil.copytree(
                CORPUS / bundle,
                Workspace.discover(work).tla_dir(info),
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        (work / "apex.toml").write_text(CONFIG + f"retries = {retries}\n")
        ws = Workspace.discover(work)
        target = str(work / case["target"])
        try:
            a = _analyze(target, ws)
            kinds = {s.type for p in a.intent.properties for s in p.sources}
            kinds |= {s.type for c in a.intent.preconditions for s in c.sources}
            res.sources = sorted(SOURCE_KINDS[k] for k in kinds if k in SOURCE_KINDS)

            sc = scaffold(target, ws, project)
            if sc.gap is not None or sc.spec_path is None:
                res.reason = str(sc.gap.reason_code) if sc.gap else "no model"
            else:
                spec_path = Path(sc.spec_path)
                if ref := case.get("reference"):
                    spec_path.write_text(
                        spec_path.read_text() + "\n" + _reference_text(CORPUS / ref)
                    )
                spec = load_spec(spec_path)
                res.supplied = [d.name for d in spec.properties if d.has_proof]
                drafted = [d for d in spec.properties if d.doc and "[hint:" in d.doc]
                refs = [d for d in spec.properties if d.name.startswith("R")]
                res.drafted = [d.name for d in drafted]
                res.references = [d.name for d in refs]

                info = load_function(target)
                store = ws.review_store
                assert a.model is not None
                pairs = [(p, r) for p in drafted for r in refs]
                for p_name, r_name in sorted(
                    equivalent_pairs(a.model.namespace, a.model.text, pairs, project)
                ):
                    res.equivalent.setdefault(p_name, r_name)
                approve = {d.name: REVIEWER for d in refs}
                # Example theorems are literal renderings of a doctest or test assertion.
                approve |= {
                    d.name: EXAMPLE_REVIEWER
                    for d in spec.properties
                    if d.name.startswith("E") and d.doc
                }
                approve |= {d.name: EQUIV_REVIEWER for d in drafted if d.name in res.equivalent}
                for d in spec.properties:
                    if d.name in approve:
                        store.approve(
                            qualname=function_id(info),
                            property_id=d.name,
                            statement=d.full_statement,
                            function_sha256=info.sha256,
                            statement_sha256=d.statement_sha256,
                            reviewer=approve[d.name],
                        )
                tactics = prove_tactics(target, ws, project=project)
                res.tactic_closed = list(tactics.closed)
                if llm is not None and tactics.open:
                    proved = prove_llm(target, ws, llm, project=project)
                    res.llm_closed = list(proved.closed)
                    res.llm_attempts = sum(proved.attempts.values())

            if seconds := case.get("fuzz"):
                fuzz_target(
                    target,
                    ws,
                    budget_s=float(seconds),
                    engine=case.get("fuzz_engine"),
                    max_executions=case.get("fuzz_executions"),
                )
            if case.get("tla"):
                _approve_bundle(target, ws)
                tla_check_target(target, ws, runs=int(case.get("tla_runs", 200)))
            report = verify([target], ws, project=project, write_report=False)
            fn = report.functions[0]
            res.verdicts = {p.id: str(p.verdict) for p in fn.properties}
            res.counterexamples = {
                p.id: ", ".join(f"{k}={v}" for k, v in p.counterexample.inputs.items())
                + f" ({p.counterexample.found_by})"
                for p in fn.properties
                if p.counterexample and p.verdict == Verdict.REFUTED
            }
            if fn.gap:
                res.reason = str(fn.gap.reason_code)
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
        finally:
            res.seconds = round(time.monotonic() - started, 2)
    return res


def _approve_bundle(target: str, ws: Workspace) -> None:
    """A reference bundle is ground truth written by a person, like a reference statement."""
    from apex.tla.bundle import load_bundle

    info = load_function(target)
    b = load_bundle(ws.tla_dir(info))
    for inv, pid in zip(b.invariants, b.property_ids, strict=True):
        ws.review_store.approve(
            qualname=function_id(info),
            property_id=pid,
            statement=b.statement(inv),
            function_sha256=info.sha256,
            statement_sha256=b.sha256,
            reviewer=BUNDLE_REVIEWER,
        )


def scoreboard(results: list[CaseResult], *, full: bool = False) -> dict:
    """`full`: every case in cases.toml ran (the M5 gate is about the full corpus)."""
    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)
    good, conflicts = by_cat.get("known_good", []), by_cat.get("conflict", [])
    oos = by_cat.get("out_of_scope", [])

    # Closure is over statements that are true and checkable: text-only items are excluded.
    good_props = [(r, p) for r in good for p, v in r.expected.items() if v in ("proven", "holds")]
    # A JavaScript `number` proof is proven_conditional (safe-integer assumption): still closed.
    closed = (Verdict.PROVEN, Verdict.PROVEN_CONDITIONAL)
    proven = [(r, p) for r, p in good_props if r.verdicts.get(p) in closed]
    by_tactic = [(r, p) for r, p in proven if p in r.tactic_closed]
    by_supplied = [(r, p) for r, p in proven if p in r.supplied]
    by_llm = [(r, p) for r, p in proven if p in r.llm_closed]
    drafted = [(r, p) for r in good for p in r.drafted if r.references]
    equiv = [(r, p) for r, p in drafted if p in r.equivalent]
    covered = sum(len(set(r.equivalent.values())) for r in good)
    n_refs = sum(len(r.references) for r in good if r.drafted)

    want_ref = [(r, p) for r in results for p, v in r.expected.items() if v == "refuted"]
    refuted = [(r, p) for r, p in want_ref if r.verdicts.get(p) == Verdict.REFUTED]
    want_con = [(r, p) for r in conflicts for p, v in r.expected.items() if v == "contradiction"]
    contra = [(r, p) for r, p in want_con if r.verdicts.get(p) == Verdict.CONTRADICTION]

    produced = {kind: sum(kind in r.sources for r in results) for kind in SOURCE_KINDS.values()}
    fully_proven = [
        r
        for r in good
        if r.ok and r.expected and all(r.verdicts.get(p) == Verdict.PROVEN for p in r.expected)
    ]
    false_proven = sum(len(r.false_proven) for r in results)
    false_evidence = sum(len(r.false_evidence) for r in results)
    ai = by_cat.get("ai_generated", [])
    got = [v for r in results for v in r.verdicts.values()]
    return {
        "false_proven": false_proven,
        "false_evidence": false_evidence,
        "cases_ok": f"{sum(r.ok for r in results)}/{len(results)}",
        "languages": ", ".join(
            f"{lang} {sum(r.ok for r in rs)}/{len(rs)}"
            for lang, rs in (
                ("python", [r for r in results if not is_js(r.target.split("::")[0])]),
                ("javascript", [r for r in results if is_js(r.target.split("::")[0])]),
            )
            if rs
        ),
        "corpus": ", ".join(
            f"{cat} {len(by_cat.get(cat, []))}/{n}" for cat, n in CORPUS_TARGET.items()
        ),
        "statement_fidelity": f"{len(equiv)}/{len(drafted)} drafted statements shown "
        "equivalent to a reference",
        "reference_coverage": f"{covered}/{n_refs} references matched by a drafted statement",
        "proof_closure": f"{len(proven)}/{len(good_props)}",
        "closed_by_tactics": len(by_tactic),
        "closed_by_supplied_proof": len(by_supplied),
        "closed_by_llm": len(by_llm),
        "llm_proposals": sum(r.llm_attempts for r in results),
        "refutation_recall": f"{len(refuted)}/{len(want_ref)}",
        "conflict_detection": f"{len(contra)}/{len(want_con)}",
        "out_of_scope_reason_accuracy": f"{sum(r.reason == r.expected_reason for r in oos)}"
        f"/{len(oos)}",
        "intent_sources_producing_a_model": produced,
        "median_seconds_per_function": round(statistics.median(r.seconds for r in results), 2)
        if results
        else 0,
        "known_good_fully_proven": f"{len(fully_proven)}/{len(good)}",
        "ai_generated_in_subset": f"{sum(r.reason is None for r in ai)}/{len(ai)}",
        "evidence_verdicts": f"fuzzed {got.count('fuzzed')}, model_checked "
        f"{got.count('model_checked')}",
        "m1_gate": len(fully_proven) >= 5 and false_proven == 0,
        "m2_gate": false_proven == 0
        and len(contra) == len(want_con) > 0
        and all(produced[k] > 0 for k in ("english", "tests", "source", "docstring")),
        "m5_gate": full and false_proven == 0 and all(r.ok for r in results),
        "m6_gate": full
        and false_proven == 0
        and false_evidence == 0
        and all(r.ok for r in results),
    }


def _exit_with_parent() -> None:
    """Pool workers outlive a killed parent on macOS (no parent-death signal), and a worker
    stuck in a case would then spin for days. Each worker exits once its parent is gone."""
    import os
    import threading

    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        os._exit(1)

    threading.Thread(target=watch, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Only run cases whose target contains this text.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--provider", help="Engine mode: a model proves what the tactic tiers leave open."
    )
    parser.add_argument("--retries", type=int, default=2, help="Fixer iterations per theorem.")
    parser.add_argument("--jobs", type=int, default=1, help="Cases run at the same time.")
    args = parser.parse_args()

    cases = tomllib.loads((ROOT / "cases.toml").read_text())["case"]
    full = not args.case
    if args.case:
        cases = [c for c in cases if args.case in c["target"]]
    project = LeanProject.locate()
    llm = None
    if args.provider:
        from apex.core.config import ApexConfig
        from apex.llm.providers import get_provider

        llm = get_provider(ApexConfig(model_provider=args.provider))
    run = partial(run_case, project=project, llm=llm, retries=args.retries)
    if args.jobs > 1:  # processes: Hypothesis is not thread-safe
        with ProcessPoolExecutor(max_workers=args.jobs, initializer=_exit_with_parent) as pool:
            results = list(pool.map(run, cases))
    else:
        results = [run(c) for c in cases]
    for r in results:
        if not args.json:
            mark = "ok  " if r.ok else "FAIL"
            got = ", ".join(f"{p}={v}" for p, v in r.verdicts.items())
            if r.reason:
                got = f"{r.reason}; {got}" if got else r.reason
            by_llm = f"  [model closed {', '.join(r.llm_closed)}]" if r.llm_closed else ""
            print(f"{mark} {r.category:12} {r.target:30} {r.seconds:6.1f}s  {got}{by_llm}")
            if r.error:
                print(f"       error: {r.error}")
            if r.false_proven:
                print(f"       FALSE PROVEN: {', '.join(r.false_proven)}")
            if r.false_evidence:
                print(f"       FALSE EVIDENCE: {', '.join(r.false_evidence)}")
            if r.missing_inputs:
                print(f"       intent sources missing: {', '.join(r.missing_inputs)}")
            for p, want in r.expected.items():
                if not r.matches(p):
                    print(f"       {p}: expected {want}, got {r.verdicts.get(p)}")
            for p, cx in r.counterexamples.items():
                print(f"       {p} counterexample: {cx}")
    board = scoreboard(results, full=full)
    if args.json:
        print(json.dumps({"scoreboard": board, "cases": [asdict(r) for r in results]}, indent=2))
    else:
        print("\nscoreboard")
        for k, v in board.items():
            print(f"  {k:34} {v}")
    failed = board["false_proven"] > 0 or board["false_evidence"] > 0
    failed = failed or any(not r.ok for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
