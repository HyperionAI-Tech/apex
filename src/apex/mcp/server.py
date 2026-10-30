"""The MCP server (`apex mcp`): the deterministic verbs as typed tools, over stdio.

Tools mirror the CLI one to one and call the same pipeline functions, so a harness with MCP gets
typed results instead of parsing a shell. Two rules are enforced by what is *not* here:

- No tool can approve a statement. Approval is `apex review --approve` in an interactive
  terminal, so an agent can never self-approve; `apex_review_status` only reads.
- Model steps only run in `apex_check`, and only when a provider is configured. In host mode the
  agent does the model work and these tools check it.

The server works on the project it was started in (its working directory, where `apex.toml`
is discovered). Calls are serialized: Hypothesis, which the fidelity check and refutation use, is
not thread-safe, and the SDK runs synchronous tools in worker threads.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from apex import __version__
from apex.core.intent import IntentModel
from apex.core.outputs import ReviewStatus
from apex.core.report import FunctionResult, Report
from apex.fidelity.fuzz import FuzzResult
from apex.fidelity.refute import RefuteResult
from apex.intent.proposals import ProposalCheck, ProposalSet
from apex.report.explain import AdviceList, advise
from apex.tla.check import TlaCheckResult

INSTRUCTIONS = """\
Apex formally verifies pure Python functions with Lean 4. Targets look like `path.py::function`.
Verdicts come only from Apex: report them verbatim and never describe anything as proven unless
the verdict is `proven`. `proven_unreviewed` means Lean proved a statement no person has approved;
ask the user to run `apex review <target> --approve <ids>` in their own terminal (no tool can
approve). A `refuted` verdict carries an input on which the real Python breaks the property: fix
the code or the intent, never the theorem. Typical loop: apex_analyze -> apex_intent_submit (if
items need formal hints) -> apex_scaffold -> apex_prove -> write remaining proofs into the spec and
apex_verify -> apex_refute -> apex_suggest."""

_lock = threading.Lock()


@contextmanager
def _errors() -> Iterator[None]:
    """Tool errors (bad target, Lean missing, bad config) come back as tool results the agent
    can read and act on, not as protocol errors."""
    from pydantic import ValidationError

    from apex.analyze.target import TargetError
    from apex.lean.project import LeanUnavailable
    from apex.pipeline import ApexToolError

    try:
        yield
    except (ApexToolError, TargetError, LeanUnavailable, ValidationError) as e:
        raise ToolError(str(e)) from e


def _run[T](fn: Callable[[], T]) -> T:
    with _lock, _errors():
        return fn()


def _ws():
    from apex.pipeline import Workspace

    return Workspace.discover()


def build_server() -> MCPServer:
    from apex.pipeline import AnalyzeResult, ProveResult, ScaffoldResult

    server = MCPServer(
        name="apex", version=__version__, instructions=INSTRUCTIONS, log_level="WARNING"
    )

    @server.tool()
    def apex_analyze(target: str, english: str | None = None) -> AnalyzeResult:
        """Purity class, pure-core slice, the Lean model, and the Intent Model fused from the
        English spec, docstring, tests and source. `english` registers a Markdown spec file for
        this function. Out-of-scope functions come back with a reason code and next steps."""
        from apex import pipeline

        return _run(lambda: pipeline.analyze(target, _ws(), Path(english) if english else None))

    @server.tool()
    def apex_intent_submit(
        target: str, proposals: ProposalSet, replace: bool = False
    ) -> ProposalCheck:
        """Host mode: submit formal hints you wrote for English spec items or docstring prose.
        Each hint is parsed against the real signature; rejected ones come back with the reason.
        Formalize what the intent says, never what the code does."""
        from apex import pipeline

        return _run(lambda: pipeline.submit_proposals(target, _ws(), proposals, replace=replace))

    @server.tool()
    def apex_scaffold(target: str) -> ScaffoldResult:
        """Write the Lean model and the spec file: a drafted theorem per formal hint and per
        example. Never rewrites an existing spec; appends theorems for new properties."""
        from apex import pipeline

        return _run(lambda: pipeline.scaffold(target, _ws()))

    @server.tool()
    def apex_prove(target: str) -> ProveResult:
        """Run the tactic tiers (decide, omega, simp, induction, ...) on every open theorem in
        the spec and write the proofs that close. No model is involved. Follow with apex_verify."""
        from apex import pipeline

        return _run(lambda: pipeline.prove_tactics(target, _ws()))

    @server.tool()
    def apex_verify(targets: list[str], spec_text: str | None = None) -> Report:
        """Cold Lean compile, axiom audit and fidelity check of the spec's proofs, plus
        refutation on the real Python: the verdicts. With `spec_text` (one target only), the spec
        file is replaced by it first; changing a statement lapses its approval. Failed proofs
        carry Lean's errors with line, column and goal state in `lean_errors`."""
        from apex import pipeline
        from apex.analyze.target import load_function

        def go() -> Report:
            ws = _ws()
            if spec_text is not None:
                if len(targets) != 1:
                    raise pipeline.ApexToolError("spec_text needs exactly one target")
                from apex.theorems.spec import SpecError, parse_spec

                try:
                    parse_spec(spec_text)
                except SpecError as e:
                    raise pipeline.ApexToolError("invalid spec: " + "; ".join(e.problems)) from e
                path = ws.spec_path(load_function(targets[0]))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(spec_text)
            return pipeline.verify(targets, ws)

        return _run(go)

    @server.tool()
    def apex_refute(target: str, properties: list[str] | None = None) -> RefuteResult:
        """Search the real Python for counterexamples (examples, Hypothesis, CrossHair). Each one
        is replayed in Python before it is reported. Needs no Lean."""
        from apex import pipeline

        return _run(lambda: pipeline.refute_target(target, _ws(), properties))

    @server.tool()
    def apex_fuzz(
        target: str,
        properties: list[str] | None = None,
        seconds: float | None = None,
        engine: Literal["hypothesis", "atheris", "jazzer"] | None = None,
    ) -> FuzzResult:
        """Fuzz the real Python for `seconds` per property (default: fuzz_budget_s) and record
        the campaign. apex_verify then reports a property Lean could not decide as `fuzzed`
        (evidence, never proof), or `refuted` if a counterexample was found and replays."""
        from apex import pipeline

        return _run(
            lambda: pipeline.fuzz_target(
                target, _ws(), ids=properties, budget_s=seconds, engine=engine
            )
        )

    @server.tool()
    def apex_tla_check(target: str, runs: int = 200) -> TlaCheckResult:
        """Concurrent code: run TLC on the function's TLA+ bundle and check recorded runs of the
        real code against the spec. The bundle lives in .apex/tla/<module>/<function>/ as
        Spec.tla (module Spec), Spec.cfg (CONSTANTS bounds, INVARIANTs) and harness.py
        (setup/run/observe, optional check_<Invariant>(obs, done)). In host mode, write the
        bundle there yourself; it still needs a person's approval (`apex review`). Results are
        evidence (`model_checked`), never `proven`."""
        from apex import pipeline

        return _run(lambda: pipeline.tla_check_target(target, _ws(), runs=runs))

    @server.tool()
    def apex_check(paths: list[str], include_private: bool = False) -> Report:
        """Engine mode, only when a model provider is configured in apex.toml: the full pipeline
        over files or directories, with Apex's own model calls. In host mode, use the other
        tools and do the model work yourself."""
        from apex import pipeline

        def go() -> Report:
            ws = _ws()
            if ws.config.model_provider in ("none", "host"):
                raise pipeline.ApexToolError(
                    "apex_check is engine mode and model_provider is "
                    f"`{ws.config.model_provider}`; drive the pipeline with the other tools"
                )
            return pipeline.check(paths, ws, include_private=include_private).report

        return _run(go)

    def _advice(
        kind: Literal["explain", "suggest"], target: str | None, properties: list[str] | None
    ) -> AdviceList:
        ws = _ws()
        if not ws.last_report.is_file():
            raise ToolError("no report yet; run apex_verify first")
        report = Report.model_validate_json(ws.last_report.read_text())

        def intent_for(f: FunctionResult) -> IntentModel | None:
            module, name = f.qualname.split(".", 1)
            path = ws.apex_dir / "intent" / module / name / "intent.json"
            return IntentModel.model_validate_json(path.read_text()) if path.is_file() else None

        return advise(
            report,
            ws.root,
            kind,
            only=target,
            properties=properties,
            intent_for=intent_for,
        )

    @server.tool()
    def apex_explain(target: str | None = None, properties: list[str] | None = None) -> AdviceList:
        """Plain-English meaning of each verdict in the last report."""
        return _run(lambda: _advice("explain", target, properties))

    @server.tool()
    def apex_suggest(target: str | None = None, properties: list[str] | None = None) -> AdviceList:
        """Next steps for everything not proven in the last report, with a regression test that
        replays each counterexample."""
        return _run(lambda: _advice("suggest", target, properties))

    @server.tool()
    def apex_review_status(target: str) -> ReviewStatus:
        """Which theorem statements are approved, pending or lapsed. Read-only: approval needs
        the user to run `apex review <target> --approve <ids>` in their own terminal."""
        from apex import pipeline

        return _run(lambda: pipeline.review_status(target, _ws()))

    return server


def main() -> None:
    build_server().run("stdio")
