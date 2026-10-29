"""Typer app. Every command takes --json; targets use `path.py::qualname`."""

import json
import os
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
from apex.lean.project import LeanProject, LeanUnavailable, bundled_project, find_elan

if TYPE_CHECKING:
    from apex.core.report import Report
    from apex.intent.proposals import ProposalCheck
    from apex.pipeline import Workspace
    from apex.skill.install import SkillInstallResult

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
proof_budget_s = 120               # per theorem: Lean time for one compile of its proof
refute_budget_s = 10               # CrossHair time per property
retries = 4                        # fixer-loop iterations (model proofs fed Lean's errors)
llm_parallel = 4                   # theorems a model works on at the same time
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
        if (bundle := bundled_project()) and (
            project.is_bundle or project.root == LeanProject.installed_for(bundle).root
        ):
            # Installed with `uv tool install`: the pinned project lives under ~/.apex, outside
            # the package, so upgrades keep the Mathlib cache.
            project = LeanProject(bundle).install()
        steps.append({"step": "lean_project", "status": "ok", "path": str(project.root)})
        lake = project.require_lake()
    except LeanUnavailable as e:
        _emit_init(as_json, steps, str(e))
        raise typer.Exit(ExitCode.TOOL_ERROR) from None

    out = sys.stderr if as_json else None  # --json keeps stdout for the JSON document alone
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
            rc = subprocess.run([str(lake), *args], cwd=project.root, stdout=out).returncode
            steps.append({"step": name, "status": "ok" if rc == 0 else "failed"})
            if rc != 0:
                _emit_init(as_json, steps, f"`lake {' '.join(args)}` failed")
                raise typer.Exit(ExitCode.TOOL_ERROR)
    if not project.prelude_built:
        rc = subprocess.run([str(lake), "build", "Apex"], cwd=project.root, stdout=out).returncode
        steps.append({"step": "build_prelude", "status": "ok" if rc == 0 else "failed"})
        if rc != 0:
            _emit_init(as_json, steps, "`lake build Apex` failed")
            raise typer.Exit(ExitCode.TOOL_ERROR)
        project.mark_prelude_built()
    _emit_init(as_json, steps, None)


def _emit_init(as_json: bool, steps: list[dict], error: str | None) -> None:
    if as_json:
        from apex.core.outputs import InitResult, InitStep

        result = InitResult(
            ok=error is None, steps=[InitStep.model_validate(s) for s in steps], error=error
        )
        typer.echo(result.model_dump_json(indent=2))
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
        from apex.core.outputs import SchemaExport

        typer.echo(SchemaExport(written=[str(p) for p in written]).model_dump_json())
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
        from apex.core.outputs import ToolErrorOutput

        typer.echo(ToolErrorOutput(error=message).model_dump_json())
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


def _workspace(as_json: bool) -> "Workspace":
    """The workspace for the current directory; an invalid apex.toml is a tool error (exit 3)."""
    import tomllib

    from pydantic import ValidationError

    from apex.pipeline import Workspace

    try:
        return Workspace.discover()
    except (ValidationError, tomllib.TOMLDecodeError) as e:
        _fail(as_json, f"invalid {CONFIG_FILENAME}: {e}")


SarifOpt = Annotated[
    Path | None,
    typer.Option("--sarif", help="Also write the report as SARIF 2.1.0 to this file."),
]


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
        result = pipeline.analyze(target, _workspace(as_json), english)
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
        result = pipeline.scaffold(target, _workspace(as_json))
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


def _emit_report(
    report: "Report", as_json: bool, markdown: bool, sarif: Path | None = None
) -> NoReturn:
    from apex.report.render import print_report, to_markdown

    if sarif is not None:
        from apex.report.sarif import to_sarif

        root = _workspace(as_json).root
        sarif.parent.mkdir(parents=True, exist_ok=True)
        sarif.write_text(json.dumps(to_sarif(report, root), indent=2) + "\n")
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
    sarif: SarifOpt = None,
) -> None:
    """Cold compile, axiom audit and fidelity check of the spec's proofs, giving verdicts."""
    from apex import pipeline

    if spec is not None and len(targets) != 1:
        _fail(as_json, "--spec needs exactly one target")
    with _tool_errors(as_json):
        report = pipeline.verify(
            targets,
            _workspace(as_json),
            spec_paths={targets[0]: spec} if spec else None,
            fidelity_n=fidelity_examples,
        )
    _emit_report(report, as_json, markdown, sarif)


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
        result = pipeline.refute_target(target, _workspace(as_json), prop)
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
            target, _workspace(as_json), provider=provider, from_source=from_source
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
            target, _workspace(as_json), proposals, replace=replace
        )
    _emit_check(check, as_json)


ProviderOpt = Annotated[
    str | None,
    typer.Option(help="anthropic, openai, claude-code or codex (default: apex.toml)."),
]


@app.command()
def prove(
    target: Target,
    tactics_only: Annotated[
        bool, typer.Option("--tactics-only", help="No LLM: tactic tiers only.")
    ] = False,
    provider: ProviderOpt = None,
    spec: SpecOpt = None,
    as_json: JsonFlag = False,
    markdown: MarkdownFlag = False,
    sarif: SarifOpt = None,
) -> None:
    """Fill open proofs (tactic tiers, then a model unless --tactics-only), then verify.

    Model proofs go through Lean's fast loop with its errors fed back (`retries` times), and are
    written into the spec only when Lean accepts them; the verdict still comes from `verify`.
    """
    from apex import pipeline
    from apex.llm.providers import ProviderError, get_provider

    ws = _workspace(as_json)
    llm = None
    if not tactics_only:
        try:
            llm = get_provider(ws.config, provider)
        except ProviderError as e:
            _fail(as_json, f"{e}. Or run `apex prove {target} --tactics-only`.")
    quiet = as_json or markdown
    with _tool_errors(as_json):
        result = pipeline.prove_tactics(target, ws, spec_path=spec)
        if not quiet:
            console.print(
                f"tactic tiers closed: {', '.join(result.closed) or 'none'}; still open: "
                f"{', '.join(result.open) or 'none'}  ({result.duration_s}s)"
            )
        if llm is not None and result.open:
            if not quiet:
                console.print(f"asking {llm.name} for {', '.join(result.open)} ...")
            res = pipeline.prove_llm(target, ws, llm, spec_path=spec)
            if not quiet:
                console.print(
                    f"{llm.name} closed: {', '.join(res.closed) or 'none'}; still open: "
                    f"{', '.join(res.open) or 'none'}  ({res.duration_s}s)"
                )
                for pid, why in res.skipped.items():
                    console.print(f"  {pid}: not attempted ({why})")
                for pid, why in res.errors.items():
                    first = why.splitlines()[0] if why else ""
                    console.print(
                        f"  {pid}: {res.attempts.get(pid, 0)} attempts; last: {escape(first)}"
                    )
        report = pipeline.verify(
            [target],
            ws,
            spec_paths={target: spec} if spec else None,
            mode="engine" if llm else "host",
            provider=llm.name if llm else None,
        )
    _emit_report(report, as_json, markdown, sarif)


@app.command()
def check(
    paths: Annotated[
        list[str], typer.Argument(help="Files, directories, or path.py::function targets.")
    ],
    provider: ProviderOpt = None,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Skip every model step, even if one is configured.")
    ] = False,
    include_private: Annotated[
        bool, typer.Option("--include-private", help="Also check `_private` functions.")
    ] = False,
    timeout: Annotated[
        float | None,
        typer.Option(help="Seconds for the whole run; past it, Apex stops with exit code 3."),
    ] = None,
    as_json: JsonFlag = False,
    markdown: MarkdownFlag = False,
    sarif: SarifOpt = None,
) -> None:
    """Engine mode: the full pipeline over files or a package.

    For each function: analyze, formalize English intent (model), scaffold, prove (tactic tiers,
    then a model), and verify with refutation. With no provider the model steps are skipped.
    """
    from apex import pipeline

    ws = _workspace(as_json)
    quiet = as_json or markdown

    def show(step: "pipeline.CheckStep") -> None:
        if not quiet:
            console.print(f"  [dim]{escape(step.target)}[/] {step.step}: {escape(step.detail)}")

    with _tool_errors(as_json):
        run = pipeline.check(
            paths,
            ws,
            provider=provider,
            use_llm=not no_llm,
            include_private=include_private,
            timeout_s=timeout,
            on_step=show,
        )
    _emit_report(run.report, as_json, markdown, sarif)


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

    ws = _workspace(as_json)
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
            with _tool_errors(as_json):
                status = pipeline.review_status(target, ws, spec_path)
            typer.echo(status.model_dump_json(indent=2))
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


def _last_report(as_json: bool) -> "Report":
    from apex.core.report import Report

    path = _workspace(as_json).last_report
    if not path.is_file():
        _fail(as_json, "no report yet; run `apex verify` or `apex check` first")
    return Report.model_validate_json(path.read_text())


@app.command()
def report(
    as_json: JsonFlag = False, markdown: MarkdownFlag = False, sarif: SarifOpt = None
) -> None:
    """Show the last report (exit code as recorded)."""
    _emit_report(_last_report(as_json), as_json, markdown, sarif)


def _advice(kind: str, target: str | None, prop: list[str] | None, as_json: bool) -> None:
    from apex.core.intent import IntentModel
    from apex.core.report import FunctionResult
    from apex.report.explain import advise

    rep = _last_report(as_json)
    ws = _workspace(as_json)

    def intent_for(f: FunctionResult) -> IntentModel | None:
        module, name = f.qualname.split(".", 1)
        path = ws.apex_dir / "intent" / module / name / "intent.json"
        return IntentModel.model_validate_json(path.read_text()) if path.is_file() else None

    result = advise(rep, ws.root, kind, only=target, properties=prop, intent_for=intent_for)  # type: ignore[arg-type]
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    if not result.items:
        console.print("nothing to " + kind + (f" for {target}" if target else ""))
    for a in result.items:
        head = f"{a.function} {a.property}" if a.property else a.function
        console.print(f"[bold]{escape(head)}[/]  {a.verdict}")
        console.print(f"  {escape(a.meaning)}")
        for line in a.explanation.splitlines():
            console.print(f"  {escape(line)}")
        for step in a.suggestions:
            console.print(f"  · {escape(step)}")
        if a.snippet:
            console.print("  regression test:")
            console.print(a.snippet, markup=False, highlight=False)


AdviceTarget = Annotated[
    str | None, typer.Argument(help="Only this function (path.py::name or name).")
]
AdviceProps = Annotated[
    list[str] | None, typer.Option("--property", "-p", help="Only these property IDs.")
]


@app.command()
def explain(
    target: AdviceTarget = None, prop: AdviceProps = None, as_json: JsonFlag = False
) -> None:
    """Plain-English explanation of each verdict in the last report."""
    _advice("explain", target, prop, as_json)


@app.command()
def suggest(
    target: AdviceTarget = None, prop: AdviceProps = None, as_json: JsonFlag = False
) -> None:
    """Concrete code or spec changes for everything not proven in the last report, with a
    regression test for each counterexample."""
    _advice("suggest", target, prop, as_json)


# ---- surfaces: MCP server and Agent Skill ---------------------------------------------------

mcp_app = typer.Typer(
    help="The MCP server: the deterministic verbs as typed tools over stdio.",
    invoke_without_command=True,
)
app.add_typer(mcp_app, name="mcp")
skill_app = typer.Typer(help="The apex-verify Agent Skill.", no_args_is_help=True)
app.add_typer(skill_app, name="skill")

HarnessOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--harness",
        help="claude, codex, gemini, cursor, vscode or all (repeatable; default: all).",
    ),
]
UserFlag = Annotated[
    bool, typer.Option("--user", help="Install into home-directory locations, not the project.")
]


def _harnesses(values: list[str] | None) -> list[str]:
    from apex.skill.install import HARNESSES

    if not values or "all" in values:
        return list(HARNESSES)
    return values


def _emit_install(result: "SkillInstallResult", as_json: bool) -> NoReturn:
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for path in result.written:
            console.print(f"wrote {path}")
        for path in result.unchanged:
            console.print(f"[dim]unchanged {path}[/]")
        for why in result.refused:
            console.print(f"[yellow]left alone[/] {escape(why)}")
    raise typer.Exit(ExitCode.OK if not result.refused else ExitCode.GAPS)


@mcp_app.callback()
def mcp(ctx: typer.Context) -> None:
    """Run the MCP server on stdio (what a harness starts). `apex mcp install` configures one."""
    if ctx.invoked_subcommand is None:
        from apex.mcp.server import main as serve

        serve()


@mcp_app.command("install")
def mcp_install(
    harness: HarnessOpt = None, user: UserFlag = False, as_json: JsonFlag = False
) -> None:
    """Merge an `apex` entry into each harness's MCP config (never overwrites other entries)."""
    from apex.skill.install import InstallError, install_mcp

    try:
        result = install_mcp(_harnesses(harness), user=user)
    except InstallError as e:
        _fail(as_json, str(e))
    _emit_install(result, as_json)


@skill_app.command("install")
def skill_install(
    harness: HarnessOpt = None,
    user: UserFlag = False,
    with_mcp: Annotated[
        bool, typer.Option("--with-mcp", help="Also write each harness's MCP config entry.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Install apex-verify: one copy in .agents/skills, linked from .claude/skills.

    Refuses to replace a skill of the same name that Apex did not install. Prints every file it
    writes.
    """
    from apex.skill.install import InstallError, install_skill

    try:
        result = install_skill(_harnesses(harness), user=user, with_mcp=with_mcp)
    except InstallError as e:
        _fail(as_json, str(e))
    _emit_install(result, as_json)


def main() -> None:
    """Entry point. Exit codes are a contract (0 proven, 1 failures, 2 gaps, 3 tool error), so a
    usage error or a crash must never surface as 1 or 2, which Click and Python use by default."""
    try:
        rc = app(standalone_mode=False)
    except typer.Abort:
        err.print("[red]aborted[/]")
        sys.exit(ExitCode.TOOL_ERROR)
    except KeyboardInterrupt:
        sys.exit(ExitCode.TOOL_ERROR)
    except Exception as e:  # a crash is a tool error, never a verdict
        if hasattr(e, "show") and hasattr(e, "exit_code"):  # Click's usage errors (exit 2)
            e.show()  # type: ignore[attr-defined]
            sys.exit(ExitCode.TOOL_ERROR)
        err.print(f"[red]internal error:[/] {type(e).__name__}: {escape(str(e))}")
        if os.environ.get("APEX_DEBUG"):
            raise
        sys.exit(ExitCode.TOOL_ERROR)
    sys.exit(rc if isinstance(rc, int) else 0)
