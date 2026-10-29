"""Measure warm-REPL vs cold-verdict latency on the same theorems (Week 1 task).

uv run python scripts/bench_repl.py [--rounds 3] [--json]
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from apex.lean.compiler import compile_and_audit
from apex.lean.project import LeanProject
from apex.lean.repl import ReplPool

CANARIES = Path(__file__).resolve().parents[1] / "tests" / "canaries" / "good"
CASES = {
    "g01_clamp": "clamp_bounds",
    "g03_reverse": "reverse_reverse",
    "g05_max_comm": "pyMax_comm",
    "g08_safe_head_namespaced": "Mathx.safeHead_none_iff",
}


def _strip_imports(src: str) -> str:
    """The REPL base environment already holds the imports."""
    return "\n".join(line for line in src.splitlines() if not line.startswith("import "))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project = LeanProject.locate()
    project.lean_command()  # resolve and cache the lake environment outside the timings

    cold: dict[str, list[float]] = {k: [] for k in CASES}
    for _ in range(args.rounds):
        for stem, thm in CASES.items():
            src = (CANARIES / f"{stem}.lean").read_text()
            started = time.monotonic()
            res = compile_and_audit(src, [thm], project=project)
            cold[stem].append(time.monotonic() - started)
            assert res.theorems[0].outcome.value == "proved", res

    started = time.monotonic()
    pool = ReplPool(size=1, project=project)
    pool_start = time.monotonic() - started
    warm: dict[str, list[float]] = {k: [] for k in CASES}
    try:
        for _ in range(args.rounds):
            for stem in CASES:
                src = _strip_imports((CANARIES / f"{stem}.lean").read_text())
                started = time.monotonic()
                res = pool.check(src)
                warm[stem].append(time.monotonic() - started)
                assert res.ok, res.messages
    finally:
        pool.close()

    rows = {
        stem: {
            "cold_median_s": round(statistics.median(cold[stem]), 3),
            "warm_median_s": round(statistics.median(warm[stem]), 3),
        }
        for stem in CASES
    }
    summary = {"pool_start_s": round(pool_start, 2), "rounds": args.rounds, "cases": rows}
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    print(f"REPL pool start (one-time, includes import): {pool_start:.1f}s")
    print(f"{'case':28} {'cold (verdict)':>15} {'warm (repl)':>12}")
    for stem, r in rows.items():
        print(f"{stem:28} {r['cold_median_s']:>14.3f}s {r['warm_median_s']:>11.3f}s")


if __name__ == "__main__":
    main()
