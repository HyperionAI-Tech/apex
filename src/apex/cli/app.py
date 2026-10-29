"""Typer app. Every command takes --json; targets use `path.py::qualname`."""

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from apex import __version__
from apex.core.config import CONFIG_FILENAME
from apex.core.verdict import ExitCode
from apex.lean.project import LeanProject, LeanUnavailable, find_elan

if TYPE_CHECKING:
    from apex.core.report import Report
    from apex.intent.proposals import ProposalCheck

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Formal verification of Python functions with Lean 4.",
)
dev = typer.Typer(help="Developer commands for the Lean harness.", no_args_is_help=True)
app.add_typer(dev, name="dev", hidden=True)

console = Console()
err = Console(stderr=True)

JsonFlag = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]

_STATUS_STYLE = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
    "proved": "green",
    "failed": "red",
    "timeout": "yellow",
}

CONFIG_TEMPLATE = """\
[apex]
schema_version = "1"
# How LLM steps reach a model (default: none; every deterministic verb works without one):
#   anthropic | openai   API key from ANTHROPIC_API_KEY / OPENAI_API_KEY
#   claude-code | codex  the coding agent CLI you already have installed and logged in
#   host                 the agent driving Apex proposes, via `apex intent submit`
model_provider = "none"
# model = "..."                    # optional; unset means the provider's default
proof_budget_s = 120               # per theorem
refute_budget_s = 10               # CrossHair time per property
retries = 4                        # fixer-loop iterations
confidence_floor = 0.6             # below this, a property is flagged SPEC_AMBIGUOUS
fail_on = ["refuted", "fidelity_failed", "contradiction"]
review_store = ".apex/reviews.json"
ignore = ["tests/**", "migrations/**"]
"""


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_cb, is_eager=True, help="Print the Apex version."
        ),
    ] = False,
) -> None:
    pass


@app.command()
def doctor(
    as_json: JsonFlag = False,
    warm: Annotated[
        bool, typer.Option("--warm", help="Also start a REPL and time warm-up.")
    ] = False,
) -> None:
    """Check Lean, the Mathlib cache, Pyright and optional tools."""
    from apex.cli.doctor import run_doctor

    report = run_doctor(warm_repl=warm)
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        table = Table(title=f"apex doctor · {__version__}")
        for col in ("check", "status", "detail"):
            table.add_column(col)
        for c in report.checks:
            table.add_row(c.name, f"[{_STATUS_STYLE[c.status]}]{c.status}[/]", c.detail)
        console.print(table)
    raise typer.Exit(ExitCode.OK if report.healthy else ExitCode.TOOL_ERROR)


@app.command()
def init(
    as_json: JsonFlag = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the download confirmation.")
    ] = False,
) -> None:
    """Write apex.toml, then fetch the pinned Lean toolchain and the prebuilt Mathlib cache."""
    steps: list[dict] = []
    cfg = Path.cwd() / CONFIG_FILENAME
    if not cfg.exists():
        cfg.write_text(CONFIG_TEMPLATE)
        steps.append({"step": "config", "status": "written", "path": str(cfg)})
    else:
        steps.append({"step": "config", "status": "exists", "path": str(cfg)})

    if find_elan() is None:
        msg = (
            "elan (the Lean toolchain manager) is not installed. Install it from "
            "https://github.com/leanprover/elan, then rerun `apex init`."
        )
        _emit_init(as_json, steps, msg)
        raise typer.Exit(ExitCode.TOOL_ERROR)

    try:
        project = LeanProject.locate()
        lake = project.require_lake()
    except LeanUnavailable as e:
        _emit_init(as_json, steps, str(e))
        raise typer.Exit(ExitCode.TOOL_ERROR) from None

    if not project.mathlib_cache_present:
        if not yes and not as_json:
            console.print(
                "[bold]One-time setup:[/] downloads the Lean toolchain and the prebuilt "
                "Mathlib cache (about 5 GB on disk, usually a few minutes). It is shared "
                "by every project that uses this Apex install."
            )
            if not typer.confirm("Continue?", default=True):
                raise typer.Exit(ExitCode.TOOL_ERROR)
        for args, name in [(["update"], "lake_update"), (["exe", "cache", "get"], "mathlib_cache")]:
            rc = subprocess.run([str(lake), *args], cwd=project.root).returncode
            steps.append({"step": name, "status": "ok" if rc == 0 else "failed"})
            if rc != 0:
                _emit_init(as_json, steps, f"`lake {' '.join(args)}` failed")
                raise typer.Exit(ExitCode.TOOL_ERROR)
    if not project.prelude_built:
        rc = subprocess.run([str(lake), "build", "Apex"], cwd=project.root).returncode
        steps.append({"step": "build_prelude", "status": "ok" if rc == 0 else "failed"})
        if rc != 0:
            _emit_init(as_json, steps, "`lake build Apex` failed")
            raise typer.Exit(ExitCode.TOOL_ERROR)
    _emit_init(as_json, steps, None)


def _emit_init(as_json: bool, steps: list[dict], error: str | None) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": error is None, "steps": steps, "error": error}, indent=2))
        return
    for s in steps:
        console.print(f"  {s['step']}: {s['status']}" + (f" ({s['path']})" if "path" in s else ""))
    if error:
        err.print(f"[red]error:[/] {error}")
    else:
        console.print("[green]Apex is ready.[/] Run `apex doctor` to confirm.")


@app.command()
def schema(
    out: Annotated[Path, typer.Option(help="Directory to write JSON Schemas into.")] = Path(
        "schemas"
    ),
    as_json: JsonFlag = False,
) -> None:
    """Export the JSON Schema of every public contract."""
    from apex.core.schema import export_schemas

    written = export_schemas(out)
    if as_json:
        typer.echo(json.dumps({"written": [str(p) for p in written]}))
    else:
        for p in written:
            console.print(f"wrote {p}")


@dev.command("compile")
def dev_compile(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    theorem: Annotated[list[str], typer.Option("--theorem", "-t", help="Theorem to audit.")],
    timeout: Annotated[float, typer.Option(help="Seconds before the compile is killed.")] = 120.0,
    as_json: JsonFlag = False,
) -> None:
    """Cold-compile a Lean file through the verdict path and audit the named theorems."""
    from apex.lean.compiler import TheoremOutcome, compile_and_audit

    result = compile_and_audit(file.read_text(), theorem, timeout_s=timeout)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        console.print(f"status: {result.status}  ({result.duration_s:.1f}s)")
        if result.tool_error:
            err.print(f"[red]tool error:[/] {result.tool_error}")
        for t in result.theorems:
            style = _STATUS_STYLE[t.outcome.value]
            axioms = ", ".join(t.audit.axioms) if t.audit else "-"
            console.print(f"  [{style}]{t.outcome.value:8}[/] {t.name}  axioms: {axioms}")
            for r in t.reasons:
                console.print(f"           · {r}")
    if result.status == "tool_error":
        raise typer.Exit(ExitCode.TOOL_ERROR)
    proved = all(t.outcome is TheoremOutcome.PROVED for t in result.theorems)
    raise typer.Exit(ExitCode.OK if proved else ExitCode.FAILURE)


# ---- deterministic verbs ---------------------------------------------------------------------

Target = Annotated[str, typer.Argument(help="path.py::function")]
SpecOpt = Annotated[
    Path | None, typer.Option("--spec", help="Spec file (default: .apex/lean/<mod>/<fn>/Spec.lean)")
]
MarkdownFlag = Annotated[bool, typer.Option("--markdown", help="Markdown table output.")]


def _fail(as_json: bool, message: str) -> NoReturn:
    if as_json:
        typer.echo(json.dumps({"error": message}))
    else:
        err.print(f"[red]error:[/] {message}")
    raise typer.Exit(ExitCode.TOOL_ERROR)


@contextmanager
def _tool_errors(as_json: bool) -> Iterator[None]:
    from apex.analyze.target import TargetError
    from apex.pipeline import ApexToolError

    try:
        yield
    except (ApexToolError, TargetError, LeanUnavailable) as e:
        _fail(as_json, str(e))


EnglishOpt = Annotated[
    Path | None,
    typer.Option(
        "--english",
        help="English spec (Markdown) to register for this function; `<module>.spec.md` "
        "beside the module is read automatically.",
    ),
]


@app.command()
def analyze(target: Target, english: EnglishOpt = None, as_json: JsonFlag = False) -> None:
    """Purity, pure-core slice, and the Intent Model fused from docstring, tests, source and
    English spec."""
    from apex import pipeline

    with _tool_errors(as_json):
        result = pipeline.analyze(target, pipeline.Workspace.discover(), english)
    if as_json:
        typer.echo(result.model_dump_json(indent=2, by_alias=True))
        return
    intent = result.intent
    console.print(
        f"[bold]{intent.function.qualname}[/]  purity: {intent.purity.class_}  "
        f"in scope: {result.in_scope}"
    )
    if result.gap:
        console.print(f"  [yellow]{result.gap.reason_code}[/]: {result.gap.explanation}")
        for step in result.gap.next_steps:
            console.print(f"    · {step}")
    if result.pure_core:
        console.print("  pure core:")
        console.print(result.pure_core, markup=False, highlight=False)
    for c in intent.preconditions:
        src = ", ".join(f"{s.type} {s.loc}" for s in c.sources)
        console.print(f"  {c.id} [precondition] {escape(c.formal_hint or c.text)}  ({src})")
    for p in intent.properties:
        src = ", ".join(f"{s.type} {s.loc}" for s in p.sources)
        hint = f"  `{escape(p.formal_hint)}`" if p.formal_hint and p.kind != "example" else ""
        by = f" by {p.proposed_by}" if p.proposed_by else ""
        console.print(
            f"  {p.id} [{p.kind}] {escape(p.text)}{hint}  ({src}; confidence {p.confidence}{by})"
        )
    if result.needs_formalizing:
        console.print(
            f"  [yellow]needs a formal hint:[/] {', '.join(result.needs_formalizing)} "
            "(`apex intent propose`, or `apex intent submit` from an agent)"
        )
    for c in intent.conflicts:
        console.print(f"  [red]conflict[/] {', '.join(c.property_ids)}: {c.description}")
    if result.lean_model:
        console.print("  Lean model:")
        console.print(result.lean_model, markup=False, highlight=False)


@app.command()
def scaffold(target: Target, as_json: JsonFlag = False) -> None:
    """Write the Lean model and a spec skeleton (theorem stubs with TODO holes)."""
    from apex import pipeline

    with _tool_errors(as_json):
        result = pipeline.scaffold(target, pipeline.Workspace.discover())
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        if result.model_path:
            console.print(f"model: {result.model_path}  compiles: {result.model_compiles}")
            state = (
                "written"
                if result.spec_written
                else f"appended {', '.join(result.appended)}"
                if result.appended
                else "exists, nothing new to add"
            )
            console.print(f"spec:  {result.spec_path}  ({state})")
        if result.gap:
            console.print(f"[yellow]{result.gap.reason_code}[/]: {result.gap.explanation}")
    raise typer.Exit(ExitCode.OK if result.gap is None else ExitCode.GAPS)


def _emit_report(report: "Report", as_json: bool, markdown: bool) -> NoReturn:
    from apex.report.render import print_report, to_markdown

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    elif markdown:
        typer.echo(to_markdown(report))
    else:
        print_report(report, console)
    raise typer.Exit(report.exit_code)


@app.command()
def verify(
    targets: Annotated[list[str], typer.Argument(help="One or more path.py::function")],
    spec: SpecOpt = None,
    fidelity_examples: Annotated[int, typer.Option(help="Inputs for the fidelity check.")] = 200,
    as_json: JsonFlag = False,
    markdown: MarkdownFlag = False,
) -> None:
    """Cold compile, axiom audit and fidelity check of the spec's proofs, giving verdicts."""
    from apex import pipeline

    if spec is not None and len(targets) != 1:
        _fail(as_json, "--spec needs exactly one target")
    with _tool_errors(as_json):
        report = pipeline.verify(
            targets,
            pipeline.Workspace.discover(),
            spec_paths={targets[0]: spec} if spec else None,
            fidelity_n=fidelity_examples,
        )
    _emit_report(report, as_json, markdown)


@app.command()
def refute(
    target: Target,
    prop: Annotated[
        list[str] | None, typer.Option("--property", "-p", help="Only these property IDs.")
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Search the real Python for counterexamples (examples, Hypothesis, CrossHair)."""
    from apex import pipeline

    with _tool_errors(as_json):
        result = pipeline.refute_target(target, pipeline.Workspace.discover(), prop)
    refuted = [p for p in result.properties if p.refuted]
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for p in result.properties:
            if p.refuted and p.counterexample:
                cx = p.counterexample
                args = ", ".join(f"{k}={v}" for k, v in cx.inputs.items())
                console.print(
                    f"  [red]refuted[/]  {p.id}  {escape(args)} -> {escape(cx.actual or '')}"
                    f"  (expected {escape(cx.expected or '')}; found by {cx.found_by})"
                )
            else:
                how = ", ".join(p.searched_by) or "not searched"
                note = f"; {p.note}" if p.note else ""
                console.print(f"  no counterexample  {p.id}  ({how}{escape(note)})")
    raise typer.Exit(ExitCode.FAILURE if refuted else ExitCode.OK)


intent_app = typer.Typer(
    help="Formal hints proposed by a model (engine mode) or by the agent driving Apex (host "
    "mode). Proposals are checked, never trusted.",
    no_args_is_help=True,
)
app.add_typer(intent_app, name="intent")


def _emit_check(check: "ProposalCheck", as_json: bool) -> NoReturn:
    if as_json:
        typer.echo(check.model_dump_json(indent=2))
    else:
        acc = check.accepted
        console.print(
            f"accepted {len(acc.properties)} properties and {len(acc.preconditions)} "
            f"preconditions from {escape(acc.proposed_by)}"
        )
        for p in acc.properties:
            item = f"{p.item} " if p.item else ""
            console.print(f"  + {item}{escape(p.text)}  `{escape(p.formal_hint)}`  ({p.basis})")
        for c in acc.preconditions:
            console.print(f"  + requires `{escape(c.formal_hint)}`  ({c.basis})")
        for r in check.rejected:
            console.print(
                f"  [red]rejected[/] {r.kind} {r.index}: `{escape(r.formal_hint)}`: "
                f"{escape(r.reason)}"
            )
        console.print("Run `apex scaffold` to draft theorems from the new hints.")
    raise typer.Exit(ExitCode.OK if not check.rejected else ExitCode.GAPS)


@intent_app.command("propose")
def intent_propose(
    target: Target,
    provider: Annotated[
        str | None,
        typer.Option(help="anthropic, openai, claude-code or codex (default: apex.toml)."),
    ] = None,
    from_source: Annotated[
        bool,
        typer.Option(
            "--from-source",
            help="Also show the model the function body and accept intent read off the code "
            "(lower confidence: it assumes the code is right).",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Ask a model to formalize the English spec items and docstring prose into formal hints."""
    from apex import pipeline

    with _tool_errors(as_json):
        check = pipeline.propose_intent(
            target, pipeline.Workspace.discover(), provider=provider, from_source=from_source
        )
    _emit_check(check, as_json)


@intent_app.command("submit")
def intent_submit(
    target: Target,
    file: Annotated[Path, typer.Option("--file", "-f", help="ProposalSet JSON (`-` for stdin).")],
    replace: Annotated[
        bool, typer.Option("--replace", help="Drop previously stored proposals first.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Host mode: check formal hints written by the agent driving Apex, and store the valid ones.

    Schema: `apex schema` writes proposal_set.schema.json.
    """
    from pydantic import ValidationError

    from apex import pipeline
    from apex.intent.proposals import ProposalSet

    raw = sys.stdin.read() if str(file) == "-" else file.read_text()
    try:
        proposals = ProposalSet.model_validate_json(raw)
    except ValidationError as e:
        _fail(as_json, f"not a valid ProposalSet: {e}")
    with _tool_errors(as_json):
        check = pipeline.submit_proposals(
            target, pipeline.Workspace.discover(), proposals, replace=replace
        )
    _emit_check(check, as_json)


@app.command()
def prove(
    target: Target,
    tactics_only: Annotated[
        bool, typer.Option("--tactics-only", help="No LLM: tactic tiers.")
    ] = False,
    spec: SpecOpt = None,
    as_json: JsonFlag = False,
    markdown: MarkdownFlag = False,
) -> None:
    """Fill open proofs with the tactic tiers, write them into the spec, then verify."""
    from apex import pipeline

    if not tactics_only:
        _fail(
            as_json,
            "engine-mode proving needs a model provider (Phase 3); use "
            "--tactics-only, or write proofs yourself and run `apex verify`",
        )
    ws = pipeline.Workspace.discover()
    with _tool_errors(as_json):
        result = pipeline.prove_tactics(target, ws, spec_path=spec)
        if not as_json and not markdown:
            closed = ", ".join(result.closed) or "none"
            console.print(
                f"tactic tiers closed: {closed}; still open: "
                f"{', '.join(result.open) or 'none'}  ({result.duration_s}s)"
            )
        report = pipeline.verify([target], ws, spec_paths={target: spec} if spec else None)
    _emit_report(report, as_json, markdown)


@app.command()
def review(
    target: Target,
    approve: Annotated[
        str | None, typer.Option("--approve", help="Comma-separated property IDs to approve.")
    ] = None,
    spec: SpecOpt = None,
    as_json: JsonFlag = False,
) -> None:
    """Show statements in English and Lean with review status; --approve records approval.

    Approval demands an interactive terminal, so a coding agent's shell cannot approve on a
    human's behalf.
    """
    from apex import pipeline
    from apex.analyze.target import load_function
    from apex.theorems.review import current_reviewer

    ws = pipeline.Workspace.discover()
    with _tool_errors(as_json):
        info = load_function(target)
        spec_path = spec or ws.spec_path(info)
        parsed = pipeline.load_spec(spec_path)
    store, fid = ws.review_store, pipeline.function_id(info)
    rows = [
        (d, store.status(fid, info.sha256, d.name, d.statement_sha256)) for d in parsed.properties
    ]

    if approve is None:
        if as_json:
            typer.echo(
                json.dumps(
                    [
                        {
                            "id": d.name,
                            "english": d.doc,
                            "lean": d.full_statement,
                            "status": r.status,
                            "reviewer": r.reviewer,
                        }
                        for d, r in rows
                    ],
                    indent=2,
                )
            )
            return
        for d, r in rows:
            console.print(f"[bold]{d.name}[/] {escape(f'[{r.status}]')}  {escape(d.doc or '')}")
            console.print(f"    {d.full_statement}", markup=False, highlight=False)
        return

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _fail(
            as_json,
            "approval requires an interactive terminal. Ask the user to run "
            f"`apex review {target} --approve {approve}` themselves.",
        )
    ids = [i.strip() for i in approve.split(",") if i.strip()]
    chosen = [(d, r) for d, r in rows if d.name in ids]
    if missing := set(ids) - {d.name for d, _ in chosen}:
        _fail(as_json, f"no such properties in {spec_path}: {', '.join(sorted(missing))}")
    reviewer = current_reviewer()
    console.print(
        f"You ({reviewer}) are approving these statements as the intended meaning "
        f"of {fid}. Proofs about them will be reported as [bold]proven[/]."
    )
    for d, _ in chosen:
        console.print(f"\n[bold]{d.name}[/]  {escape(d.doc or '')}")
        console.print(f"    {d.full_statement}", markup=False, highlight=False)
    if typer.prompt("\nType 'approve' to confirm") != "approve":
        _fail(as_json, "not approved")
    for d, _ in chosen:
        store.approve(
            qualname=fid,
            property_id=d.name,
            statement=d.full_statement,
            function_sha256=info.sha256,
            statement_sha256=d.statement_sha256,
            reviewer=reviewer,
        )
    console.print(f"[green]approved[/] {', '.join(d.name for d, _ in chosen)} in {store.path}")


@app.command()
def report(as_json: JsonFlag = False, markdown: MarkdownFlag = False) -> None:
    """Show the last report."""
    from apex.core.report import Report
    from apex.pipeline import Workspace

    path = Workspace.discover().last_report
    if not path.is_file():
        _fail(as_json, "no report yet; run `apex verify` first")
    _emit_report(Report.model_validate_json(path.read_text()), as_json, markdown)


def main() -> None:
    app()
