"""JSON Schema export for every public contract; surfaces publish these, nobody hand-writes them."""

import json
from pathlib import Path

from pydantic import BaseModel

from apex.core.config import ApexConfig
from apex.core.intent import IntentModel
from apex.core.report import Report
from apex.fidelity.refute import RefuteResult
from apex.intent.proposals import ProposalCheck, ProposalSet
from apex.lean.compiler import CompileResult

CONTRACTS: dict[str, type[BaseModel]] = {
    "intent_model": IntentModel,
    "report": Report,
    "config": ApexConfig,
    "lean_compile_result": CompileResult,
    "proposal_set": ProposalSet,
    "proposal_check": ProposalCheck,
    "refute_result": RefuteResult,
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
