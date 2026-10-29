"""Typer app. Every command takes --json; targets use `path.py::qualname`."""

import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from apex import __version__
from apex.core.config import CONFIG_FILENAME
from apex.core.verdict import ExitCode
from apex.lean.project import LeanProject, LeanUnavailable, find_elan

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
model_provider = "none"            # anthropic | openai | host | none
proof_budget_s = 120               # per theorem
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


def main() -> None:
    app()
