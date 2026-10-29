"""Report renderers. Every renderer shows Apex's verdict enum verbatim; none may soften it."""

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from apex.core.report import PropertyResult, Report, function_verdict
from apex.core.verdict import Verdict

STYLE = {
    Verdict.PROVEN: "bold green",
    Verdict.PROVEN_UNREVIEWED: "green",
    Verdict.PROVEN_CONDITIONAL: "green",
    Verdict.REFUTED: "bold red",
    Verdict.FIDELITY_FAILED: "bold red",
    Verdict.CONTRADICTION: "bold red",
    Verdict.UNPROVEN: "yellow",
    Verdict.OUT_OF_SCOPE: "dim",
}


def detail(p: PropertyResult) -> str:
    """The one actionable line per property."""
    match p.verdict:
        case Verdict.PROVEN:
            reviewer = p.review.reviewer if p.review else "?"
            axioms = ", ".join(p.proof.axioms) if p.proof and p.proof.axioms else "none"
            return f"approved by {reviewer}; axioms: {axioms}"
        case Verdict.PROVEN_UNREVIEWED:
            status = p.review.status if p.review else "pending"
            return f"statement {status}: run `apex review --approve {p.id}` in a terminal"
        case Verdict.PROVEN_CONDITIONAL:
            return "under added precondition(s): " + "; ".join(p.added_preconditions)
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
            table.add_row(
                f.qualname, p.id, f"[{STYLE[p.verdict]}]{p.verdict}[/]", escape(detail(p))
            )
    console.print(table)
    for f in report.functions:
        if f.fidelity:
            ok = "agrees" if f.fidelity.agree else "DISAGREES"
            console.print(
                f"  fidelity {f.qualname}: Lean model {ok} with Python on "
                f"{f.fidelity.inputs_checked} generated inputs"
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
            lines.append(f"| `{f.qualname}` | {p.id} | `{p.verdict}` | {esc(detail(p))} |")
    return "\n".join(lines) + "\n"
