"""Report renderers. Every renderer shows Apex's verdict enum verbatim; none may soften it."""

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from apex.core.report import PropertyResult, Report, function_verdict
from apex.core.verdict import Verdict
from apex.report.explain import target_of

STYLE = {
    Verdict.PROVEN: "bold green",
    Verdict.PROVEN_UNREVIEWED: "green",
    Verdict.PROVEN_CONDITIONAL: "green",
    Verdict.REFUTED: "bold red",
    Verdict.FIDELITY_FAILED: "bold red",
    Verdict.CONTRADICTION: "bold red",
    Verdict.UNPROVEN: "yellow",
    Verdict.OUT_OF_SCOPE: "dim",
    Verdict.FUZZED: "cyan",
    Verdict.MODEL_CHECKED: "cyan",
}


def detail(p: PropertyResult, target: str = "<target>") -> str:
    """The one actionable line per property; `target` makes its command copy-pasteable."""
    match p.verdict:
        case Verdict.PROVEN:
            reviewer = p.review.reviewer if p.review else "?"
            axioms = ", ".join(p.proof.axioms) if p.proof and p.proof.axioms else "none"
            return f"approved by {reviewer}; axioms: {axioms}"
        case Verdict.PROVEN_UNREVIEWED:
            status = p.review.status if p.review else "pending"
            return f"statement {status}: run `apex review {target} --approve {p.id}` in a terminal"
        case Verdict.PROVEN_CONDITIONAL:
            return "under added precondition(s): " + "; ".join(p.added_preconditions)
        case Verdict.FUZZED if p.fuzz:
            z = p.fuzz
            skipped = f", {z.skipped} skipped" if z.skipped else ""
            return (
                f"no counterexample in {z.executions:,} executions ({z.engine}, "
                f"{z.seconds:.0f}s{skipped}); not a proof"
            )
        case Verdict.MODEL_CHECKED if p.model_check:
            m = p.model_check
            bounds = ", ".join(f"{k}={v}" for k, v in m.constants.items()) or "as configured"
            pending = "" if p.review and p.review.status == "approved" else "; spec not approved"
            return (
                f"TLC: {m.distinct_states:,} states ({bounds}); {m.traces_checked} recorded "
                f"traces conform{pending}; not a proof"
            )
        case Verdict.FIDELITY_FAILED if p.model_check and p.model_check.rejected_trace:
            steps = len(p.model_check.rejected_trace)
            return f"the code produced a {steps}-step trace the TLA+ spec does not allow"
        case Verdict.REFUTED if p.counterexample and p.counterexample.found_by == "stress":
            c = p.counterexample
            return f"{c.expected} broke in a real run (run {c.inputs.get('run', '?')}): {c.actual}"
        case Verdict.REFUTED | Verdict.FIDELITY_FAILED if p.counterexample:
            c = p.counterexample
            args = ", ".join(f"{k}={v}" for k, v in c.inputs.items())
            return f"input ({args}): {c.expected}; {c.actual}"
    if p.gap:
        return f"{p.gap.reason_code}: {p.gap.explanation.splitlines()[0]}"
    return ""


def print_report(report: Report, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title=f"Apex {report.apex_version} · exit {report.exit_code}")
    for col in ("function", "property", "verdict", "detail"):
        table.add_column(col, overflow="fold")
    for f in report.functions:
        if (whole := function_verdict(f)) and f.gap:
            table.add_row(
                f.qualname,
                "-",
                f"[{STYLE[whole]}]{whole}[/]",
                escape(f"{f.gap.reason_code}: {f.gap.explanation}"),
            )
        for p in f.properties:
            target = target_of(f, Path.cwd())
            table.add_row(
                f.qualname, p.id, f"[{STYLE[p.verdict]}]{p.verdict}[/]", escape(detail(p, target))
            )
    console.print(table)
    for f in report.functions:
        if f.fidelity:
            ok = "agrees" if f.fidelity.agree else "DISAGREES"
            skipped = (
                f" ({f.fidelity.skipped} more skipped: too slow or too deep for Python)"
                if f.fidelity.skipped
                else ""
            )
            console.print(
                f"  fidelity {f.qualname}: Lean model {ok} with Python on "
                f"{f.fidelity.inputs_checked} generated inputs{skipped}"
            )


def to_markdown(report: Report) -> str:
    def esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ")

    lines = ["| function | property | verdict | detail |", "|---|---|---|---|"]
    for f in report.functions:
        if (whole := function_verdict(f)) and f.gap:
            gap = f"{f.gap.reason_code}: {f.gap.explanation}"
            lines.append(f"| `{f.qualname}` | - | `{whole}` | {esc(gap)} |")
        for p in f.properties:
            d = detail(p, target_of(f, Path.cwd()))
            lines.append(f"| `{f.qualname}` | {p.id} | `{p.verdict}` | {esc(d)} |")
    return "\n".join(lines) + "\n"
