"""The deterministic verbs. Every surface (CLI, MCP, skill) calls these and nothing else.

Each verb accepts candidates from outside (a spec file written by a human, an agent in host mode,
or Apex's own LLM client in engine mode) and checks them. None of them calls a model.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from apex import __version__
from apex.analyze.purity import PurityResult, classify
from apex.analyze.target import FunctionInfo, load_function
from apex.core.config import ApexConfig
from apex.core.intent import IntentModel
from apex.core.report import (
    FunctionResult,
    Gap,
    ProofArtifact,
    PropertyResult,
    Report,
)
from apex.core.verdict import ReasonCode, Verdict, exit_code_for
from apex.fidelity.bridge import FidelityError, check_fidelity
from apex.intent.skeleton import Example, build_intent
from apex.lean.compiler import TheoremOutcome, compile_and_audit, run_lean
from apex.lean.project import LeanProject
from apex.prove.tactics import probe
from apex.theorems.review import ReviewStore
from apex.theorems.spec import Spec, SpecDecl, SpecError, parse_spec, render_decl
from apex.translate.lean import LeanModel, TranslationError, translate
from apex.translate.literals import LiteralError, to_lean

FILE_HEADER = "import Apex.Prelude\n"
OPTIONS = "set_option autoImplicit false\nset_option relaxedAutoImplicit false\n"


FIDELITY_GAP = Gap(
    reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
    explanation="The Lean model disagrees with the Python, so the proof is about the wrong "
    "function and is discarded.",
    next_steps=[
        "If Python raises here, guard the input and return None, or add a precondition.",
        "Otherwise report it as an Apex translator bug.",
    ],
)


class ApexToolError(RuntimeError):
    """Bad input or a broken environment. Maps to exit code 3, never to a verdict."""


# ---- workspace -------------------------------------------------------------------------------


@dataclass
class Workspace:
    root: Path
    config: ApexConfig

    @classmethod
    def discover(cls, cwd: Path | None = None) -> "Workspace":
        cwd = (cwd or Path.cwd()).resolve()
        config, path = ApexConfig.discover(cwd)
        return cls(root=path.parent if path else cwd, config=config)

    @property
    def apex_dir(self) -> Path:
        return self.root / ".apex"

    def _fn_dir(self, base: str, info: FunctionInfo) -> Path:
        return self.apex_dir / base / info.module / info.qualname

    def model_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("lean", info) / "Model.lean"

    def spec_path(self, info: FunctionInfo) -> Path:
        return self._fn_dir("lean", info) / "Spec.lean"

    def proofs_dir(self, info: FunctionInfo) -> Path:
        return self._fn_dir("proofs", info)

    @property
    def review_store(self) -> ReviewStore:
        return ReviewStore(self.root / self.config.review_store)

    @property
    def last_report(self) -> Path:
        return self.apex_dir / "last_report.json"


def function_id(info: FunctionInfo) -> str:
    return f"{info.module}.{info.qualname}"


# ---- analyze ---------------------------------------------------------------------------------


class AnalyzeResult(BaseModel):
    intent: IntentModel
    in_scope: bool
    gap: Gap | None = None
    pure_core: str | None = None
    lean_model: str | None = None


@dataclass
class _Analysis:
    info: FunctionInfo
    purity: PurityResult
    model: LeanModel | None
    gap: Gap | None
    intent: IntentModel
    examples: list[Example]


def _purity_gap(purity: PurityResult) -> Gap:
    first = purity.findings[0]
    steps = []
    if purity.pure_core:
        steps.append("Verify the extracted pure core instead (the side effects are separable).")
    if first.reason_code is ReasonCode.FLOAT_SEMANTICS:
        steps.append("Use integers (e.g. fixed-point cents) or a float-aware tool such as Gappa.")
    elif first.reason_code is ReasonCode.NEEDS_CONCURRENCY_TOOL:
        steps.append("Model the concurrency protocol in TLA+; verify the pure step function here.")
    else:
        steps.append(
            "Move the computation into a pure function that takes its inputs as "
            "arguments and returns its result, and verify that."
        )
    return Gap(
        reason_code=first.reason_code,
        explanation="The function is not pure: " + "; ".join(purity.reasons) + ".",
        next_steps=steps,
    )


def _translation_gap(e: TranslationError) -> Gap:
    steps = ["Rewrite the construct using the supported subset (see LEAN_SUBSET.md)."]
    if e.reason_code is ReasonCode.NONTERMINATION:
        steps = ["Use a for loop over a list or range(), or recursion on a decreasing argument."]
    elif e.reason_code is ReasonCode.FLOAT_SEMANTICS:
        steps = ["Use integers (e.g. fixed-point) for exact arithmetic, or a float-aware tool."]
    return Gap(
        reason_code=e.reason_code, explanation=f"Cannot model faithfully: {e}.", next_steps=steps
    )


def _analyze(target: str) -> _Analysis:
    info = load_function(target)
    purity = classify(info.node, info.module_tree)
    model, gap = None, None
    if purity.class_ != "pure":
        gap = _purity_gap(purity)
    else:
        try:
            model = translate(info)
        except TranslationError as e:
            gap = _translation_gap(e)
    sig = model.target_sig if model else None
    intent, examples = build_intent(
        info, purity, sig.params if sig else None, sig.returns if sig else None
    )
    return _Analysis(info, purity, model, gap, intent, examples)


def analyze(target: str) -> AnalyzeResult:
    a = _analyze(target)
    return AnalyzeResult(
        intent=a.intent,
        in_scope=a.gap is None,
        gap=a.gap,
        pure_core=a.purity.pure_core,
        lean_model=a.model.text if a.model else None,
    )


# ---- scaffold --------------------------------------------------------------------------------


class ScaffoldResult(BaseModel):
    model_path: str | None = None
    spec_path: str | None = None
    spec_written: bool = False
    """False when a spec already existed; Apex never overwrites one."""
    model_compiles: bool = False
    gap: Gap | None = None


def model_file(model: LeanModel) -> str:
    return (
        f"{FILE_HEADER}{OPTIONS}\n-- Generated by Apex {__version__} from the Python source. "
        f"Do not edit: it is regenerated on every run.\n\nnamespace {model.namespace}\n\n"
        f"{model.text}\nend {model.namespace}\n"
    )


def _check_model(model: LeanModel, project: LeanProject) -> Gap | None:
    run = run_lean(model_file(model), project, timeout_s=300)
    errors = [m for m, _ in run.messages if m.severity == "error"]
    if run.code is None:
        return Gap(
            reason_code=ReasonCode.BUDGET_EXHAUSTED,
            explanation="The Lean model did not compile within 300s.",
            next_steps=["Simplify the function or split it into smaller ones."],
        )
    if not errors:
        return None
    text = errors[0].message
    if re.search(r"termination|decreasing|structural recursion", text, re.I):
        return Gap(
            reason_code=ReasonCode.NONTERMINATION,
            explanation=f"Lean could not show the recursion terminates: {text.splitlines()[0]}",
            next_steps=[
                "Recurse on a strictly smaller argument (e.g. n - 1 with a "
                "base case for n <= 0), or use a for loop."
            ],
        )
    return Gap(
        reason_code=ReasonCode.UNSUPPORTED_CONSTRUCT,
        explanation=f"The generated Lean model does not compile: {text.splitlines()[0]}",
        next_steps=["Report this as an Apex translator bug with the function source."],
    )


def _example_theorem(ex: Example, pid: str, model: LeanModel) -> str | None:
    sig = model.target_sig
    try:
        args = " ".join(to_lean(a, t) for a, (_, t) in zip(ex.args, sig.params, strict=True))
        expected = to_lean(ex.expected, sig.returns)
    except LiteralError:
        return None
    return (
        f"/-- {pid}: {ex.text}  [{ex.source.type} {ex.source.loc}] -/\n"
        f"theorem {pid} : {model.def_names[-1]} {args} = {expected} := by\n  sorry\n"
    )


def spec_skeleton(a: _Analysis) -> str:
    assert a.model is not None
    info = a.info
    out = [
        f"/-\nApex spec for {info.path}::{info.qualname}",
        "Model: Model.lean in this folder (generated; read it, do not edit it).",
        "",
        "Write each property as a theorem named after its ID (P1, P2, ...; E1... are examples).",
        "Statements need human approval in a terminal (`apex review`) before they count as",
        "`proven`. Proofs may be anything Lean accepts except sorry/admit/native_decide/axioms.",
        "Only imports and theorem/lemma declarations are allowed in this file.",
        "-/\n",
    ]
    for prop in a.intent.properties:
        ex = next((e for k, e in enumerate(a.examples, start=1) if f"E{k}" == prop.id), None)
        thm = _example_theorem(ex, prop.id, a.model) if ex else None
        if thm:
            out.append(thm)
        else:
            out.append(
                f"-- TODO {prop.id}: {prop.text}"
                + (f"\n--   hint: {prop.formal_hint}" if prop.formal_hint else "")
                + "\n"
            )
    if not a.intent.properties:
        out.append("-- TODO P1: state the first property of the function here.\n")
    return "\n".join(out)


def scaffold(target: str, ws: Workspace, project: LeanProject | None = None) -> ScaffoldResult:
    a = _analyze(target)
    if a.model is None:
        return ScaffoldResult(gap=a.gap)
    project = project or LeanProject.locate()
    gap = _check_model(a.model, project)
    model_path, spec_path = ws.model_path(a.info), ws.spec_path(a.info)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(model_file(a.model))
    written = False
    if not spec_path.exists():
        spec_path.write_text(spec_skeleton(a))
        written = True
    return ScaffoldResult(
        model_path=str(model_path),
        spec_path=str(spec_path),
        spec_written=written,
        model_compiles=gap is None,
        gap=gap,
    )


# ---- verify ----------------------------------------------------------------------------------


def verdict_file(model: LeanModel, spec: Spec, decl: SpecDecl | None) -> str:
    imports = "".join(f"import {m}\n" for m in spec.imports)
    helpers = "".join(h.text + "\n" for h in spec.helpers)
    target = decl.text if decl else ""
    return (
        f"{FILE_HEADER}{imports}{OPTIONS}\nnamespace {model.namespace}\n\n{model.text}\n"
        f"{helpers}{target}\nend {model.namespace}\n"
    )


def load_spec(path: Path) -> Spec:
    if not path.is_file():
        raise ApexToolError(f"no spec file at {path}; run `apex scaffold` first")
    try:
        return parse_spec(path.read_text())
    except SpecError as e:
        raise ApexToolError(f"invalid spec {path}: " + "; ".join(e.problems)) from e


def _verify_function(
    a: _Analysis, ws: Workspace, spec_path: Path, project: LeanProject, fidelity_n: int
) -> FunctionResult:
    info = a.info
    fid = function_id(info)
    result = FunctionResult(
        qualname=fid,
        file=str(info.path),
        sha256=info.sha256,
        purity=a.purity.class_,
        pure_core=a.purity.pure_core,
    )
    spec = load_spec(spec_path) if spec_path.is_file() else Spec()
    declared = {d.name for d in spec.properties}
    wanted = [d.name for d in spec.properties] + [
        p.id for p in a.intent.properties if p.id not in declared
    ]

    if a.gap is None and a.model is not None:
        a.gap = _check_model(a.model, project)
    if a.gap is not None or a.model is None:
        result.gap = a.gap
        result.properties = [
            PropertyResult(id=pid, verdict=Verdict.OUT_OF_SCOPE, gap=a.gap) for pid in wanted
        ]
        return result

    model = a.model
    store = ws.review_store
    conflicted = {pid: c for c in a.intent.conflicts for pid in c.property_ids}
    compiled: dict[str, PropertyResult] = {}
    for decl in spec.properties:
        review = store.status(fid, info.sha256, decl.name, decl.statement_sha256)
        base = {"id": decl.name, "lean_statement": decl.full_statement, "review": review}
        if decl.name in conflicted:
            c = conflicted[decl.name]
            compiled[decl.name] = PropertyResult(
                **base,
                verdict=Verdict.CONTRADICTION,
                gap=Gap(
                    reason_code=ReasonCode.SPEC_CONFLICT,
                    explanation=c.description,
                    next_steps=["Decide which source is right and fix the other."],
                ),
            )
            continue
        if not decl.has_proof:
            compiled[decl.name] = PropertyResult(
                **base,
                verdict=Verdict.UNPROVEN,
                gap=Gap(
                    reason_code=ReasonCode.BUDGET_EXHAUSTED,
                    explanation="No proof has been found or submitted yet.",
                    next_steps=[
                        "Run `apex prove --tactics-only`, or write a "
                        "proof in the spec and run `apex verify`."
                    ],
                ),
            )
            continue
        res = compile_and_audit(
            verdict_file(model, spec, decl),
            [f"{model.namespace}.{decl.name}"],
            project=project,
            timeout_s=ws.config.proof_budget_s,
            keep_dir=ws.proofs_dir(info) / decl.name,
        )
        if res.status == "tool_error":
            raise ApexToolError(res.tool_error or "Lean failed")
        check = res.theorems[0]
        if check.outcome is TheoremOutcome.PROVED:
            assert check.audit is not None and res.lean_file is not None
            proof = ProofArtifact(
                path=str(Path(res.lean_file).relative_to(ws.root)),
                sha256=res.source_sha256,
                axioms=check.audit.axioms,
            )
            verdict = Verdict.PROVEN if review.status == "approved" else Verdict.PROVEN_UNREVIEWED
            compiled[decl.name] = PropertyResult(**base, verdict=verdict, proof=proof)
        else:
            timeout = check.outcome is TheoremOutcome.TIMEOUT
            first = next((m for m in res.errors), None)
            detail = check.reasons[0] if check.reasons else "rejected"
            steps = ["Read the Lean error and goal state below, fix the proof, re-run verify."]
            if first and first.goal:
                steps.append(f"Goal: {first.goal}")
            compiled[decl.name] = PropertyResult(
                **base,
                verdict=Verdict.UNPROVEN,
                gap=Gap(
                    reason_code=(
                        ReasonCode.BUDGET_EXHAUSTED if timeout else ReasonCode.PROOF_REJECTED
                    ),
                    explanation=detail,
                    next_steps=steps,
                ),
            )

    # Stage 6: a proof only stands if the model it is about matches the real Python.
    if any(p.proof for p in compiled.values()):
        try:
            result.fidelity = check_fidelity(info, model, project=project, n=fidelity_n)
        except FidelityError as e:
            raise ApexToolError(f"fidelity check could not run: {e}") from e
        if not result.fidelity.agree:
            m = result.fidelity.mismatch
            for pid, p in compiled.items():
                if p.proof is not None:
                    compiled[pid] = p.model_copy(
                        update={
                            "verdict": Verdict.FIDELITY_FAILED,
                            "proof": None,
                            "counterexample": m,
                            "gap": FIDELITY_GAP,
                        }
                    )
    else:
        result.fidelity = None

    for pid in wanted:
        if pid in compiled:
            result.properties.append(compiled[pid])
        else:
            prop = next(p for p in a.intent.properties if p.id == pid)
            result.properties.append(
                PropertyResult(
                    id=pid,
                    verdict=Verdict.UNPROVEN,
                    gap=Gap(
                        reason_code=ReasonCode.NO_STATEMENT,
                        explanation=f"No Lean statement for: {prop.text}",
                        next_steps=[f"Add `theorem {pid} ...` to {spec_path.name}."],
                    ),
                )
            )
    return result


def verify(
    targets: list[str],
    ws: Workspace,
    *,
    spec_paths: dict[str, Path] | None = None,
    project: LeanProject | None = None,
    fidelity_n: int = 200,
    write_report: bool = True,
) -> Report:
    project = project or LeanProject.locate()
    functions = []
    for target in targets:
        a = _analyze(target)
        spec_path = (spec_paths or {}).get(target) or ws.spec_path(a.info)
        functions.append(_verify_function(a, ws, spec_path, project, fidelity_n))
    verdicts = [p.verdict for f in functions for p in f.properties]
    report = Report(
        apex_version=__version__,
        mode="host",
        lean_toolchain=project.toolchain,
        mathlib_rev=project.mathlib_rev,
        functions=functions,
        exit_code=int(exit_code_for(verdicts, ws.config.fail_on)),
    )
    if write_report:
        ws.last_report.parent.mkdir(parents=True, exist_ok=True)
        ws.last_report.write_text(report.model_dump_json(indent=2))
    return report


# ---- prove --tactics-only --------------------------------------------------------------------


class ProveResult(BaseModel):
    spec_path: str
    closed: dict[str, str]
    """Property -> tactic proof written into the spec."""
    open: list[str]
    duration_s: float


def prove_tactics(
    target: str, ws: Workspace, *, spec_path: Path | None = None, project: LeanProject | None = None
) -> ProveResult:
    a = _analyze(target)
    if a.model is None:
        raise ApexToolError(f"{target} is out of scope: {a.gap.explanation if a.gap else ''}")
    project = project or LeanProject.locate()
    spec_path = spec_path or ws.spec_path(a.info)
    spec = load_spec(spec_path)
    targets = [d for d in spec.properties if not d.has_proof]
    if not targets:
        return ProveResult(spec_path=str(spec_path), closed={}, open=[], duration_s=0.0)
    header = FILE_HEADER + "".join(f"import {m}\n" for m in spec.imports) + OPTIONS
    res = probe(
        header,
        a.model.text,
        a.model.namespace,
        spec,
        a.model.def_names,
        targets,
        project,
        timeout_s=max(ws.config.proof_budget_s * len(targets), 120),
    )
    text = spec_path.read_text()
    for decl in targets:
        if proof := res.closed.get(decl.name):
            text = text.replace(decl.text.rstrip("\n"), render_decl(decl, proof).rstrip("\n"), 1)
    spec_path.write_text(text)
    return ProveResult(
        spec_path=str(spec_path),
        closed=res.closed,
        open=[d.name for d in targets if d.name not in res.closed],
        duration_s=round(res.duration_s, 2),
    )
