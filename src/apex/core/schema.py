"""JSON Schema export for every public contract; surfaces publish these, nobody hand-writes them."""

import json
from pathlib import Path

from pydantic import BaseModel

from apex.cli.doctor import DoctorReport
from apex.core.config import ApexConfig
from apex.core.intent import IntentModel
from apex.core.outputs import InitResult, ReviewStatus, SchemaExport, ToolErrorOutput
from apex.core.report import Report
from apex.fidelity.refute import RefuteResult
from apex.intent.proposals import ProposalCheck, ProposalSet
from apex.lean.compiler import CompileResult
from apex.pipeline import AnalyzeResult, ProveResult, ScaffoldResult
from apex.report.explain import AdviceList
from apex.skill.install import SkillInstallResult

CONTRACTS: dict[str, type[BaseModel]] = {
    "intent_model": IntentModel,
    "report": Report,
    "config": ApexConfig,
    "lean_compile_result": CompileResult,
    "proposal_set": ProposalSet,
    "proposal_check": ProposalCheck,
    "refute_result": RefuteResult,
    # The --json output of every CLI command (see CLI_OUTPUTS).
    "analyze_result": AnalyzeResult,
    "scaffold_result": ScaffoldResult,
    "prove_result": ProveResult,
    "doctor_report": DoctorReport,
    "init_result": InitResult,
    "review_status": ReviewStatus,
    "advice": AdviceList,
    "schema_export": SchemaExport,
    "tool_error": ToolErrorOutput,
    "install_result": SkillInstallResult,
}

# Which schema each command's `--json` output follows (on exit 3, always `tool_error`).
CLI_OUTPUTS: dict[str, str] = {
    "doctor": "doctor_report",
    "init": "init_result",
    "schema": "schema_export",
    "analyze": "analyze_result",
    "scaffold": "scaffold_result",
    "verify": "report",
    "prove": "report",
    "check": "report",
    "report": "report",
    "refute": "refute_result",
    "review": "review_status",
    "explain": "advice",
    "suggest": "advice",
    "intent propose": "proposal_check",
    "intent submit": "proposal_check",
    "dev compile": "lean_compile_result",
    "mcp install": "install_result",
    "skill install": "install_result",
}


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in CONTRACTS.items():
        path = out_dir / f"{name}.schema.json"
        schema = model.model_json_schema(by_alias=True)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written
