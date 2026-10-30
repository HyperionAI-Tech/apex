"""`apex tla draft`: a model proposes a TLA+ bundle for a concurrent function.

The proposal is only a draft: Apex checks that it is well formed (the three files, a module named
Spec, the PlusCal translation, a SANY parse, a harness with setup/run/observe), feeds any error
back for up to two more attempts, and writes it pending review. Nothing it says counts until a
person approves the bundle with `apex review`.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any

from apex.analyze.target import FunctionInfo
from apex.llm.providers import Provider
from apex.tla.bundle import CFG, HARNESS, SPEC, BundleError, load_bundle
from apex.tla.tlc import sany_check, translate_pluscal

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec_tla": {"type": "string"},
        "spec_cfg": {"type": "string"},
        "harness_py": {"type": "string"},
    },
    "required": ["spec_tla", "spec_cfg", "harness_py"],
    "additionalProperties": False,
}

SYSTEM = """You write TLA+ bundles that let Apex check concurrent Python code. Reply with JSON
{spec_tla, spec_cfg, harness_py}. Rules:

Spec.tla
- `---- MODULE Spec ----` ... `====`. Define Init, Next and Spec == Init /\\ [][Next]_vars;
  PlusCal (`(* --algorithm ... *)` with empty `\\* BEGIN TRANSLATION` / `\\* END TRANSLATION`
  markers) is welcome: Apex runs the translator.
- Model the code as it is, not as it should be. Every Python line that reads or writes shared
  state is its own atomic step (its own PlusCal label), unless a lock held across those lines
  makes them one step. Bugs in the code must be possible in the spec.
- Bound everything with CONSTANTS small enough for exhaustive checking (2-3 threads, 2-3
  iterations).
- Write each intended property as a state invariant (a named definition). For "at the end"
  properties, guard with termination (e.g. AllDone => ...).

Spec.cfg
- SPECIFICATION Spec, CONSTANTS with the bounds, INVARIANT(S) naming the properties.

harness.py
- Imports the code under test from its module by name (the module's directory is on sys.path).
- def setup(): build the shared objects the code works on, return them.
- def run(state): run the real code on them with the same bounds as Spec.cfg (start the
  threads, join them). Do not reimplement the code: call it.
- def observe(state): return {spec variable name: Python value} for the shared variables the
  spec declares that the code makes visible (ints, bools, strings, lists, sets, dicts).
- def check_<Invariant>(obs, done) -> bool for each invariant you can evaluate on an observation;
  `done` is True for the last observation of a finished run.
"""


class DraftError(RuntimeError):
    pass


def _prompt(info: FunctionInfo, intent: list[str], feedback: str | None) -> str:
    source = Path(info.path).read_text()
    parts = [
        f"Module `{info.module}` ({Path(info.path).name}); the function to check is "
        f"`{info.qualname}`.",
        "Its source file:\n```python\n" + source + "\n```",
    ]
    if intent:
        parts.append("What it should do (from its docstring and specs):\n" + "\n".join(intent))
    if feedback:
        parts.append("Your previous bundle was rejected:\n" + feedback + "\nFix it.")
    return "\n\n".join(parts)


def _validate(data: dict[str, Any]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="apex-tla-draft-") as tmp:
        d = Path(tmp)
        (d / SPEC).write_text(data["spec_tla"])
        (d / CFG).write_text(data["spec_cfg"])
        (d / HARNESS).write_text(data["harness_py"])
        try:
            load_bundle(d)
        except BundleError as e:
            return str(e)
        if err := translate_pluscal(d / SPEC):
            return err
        if err := sany_check(d / SPEC):
            return err
    return None


def draft_bundle(
    provider: Provider,
    info: FunctionInfo,
    intent: list[str],
    out_dir: Path,
    *,
    attempts: int = 3,
    force: bool = False,
) -> Path:
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise DraftError(f"{out_dir} already has a bundle; pass --force to replace it")
    feedback = None
    for _ in range(attempts):
        data = provider.complete_json(SYSTEM, _prompt(info, intent, feedback), SCHEMA)
        feedback = _validate(data)
        if feedback is None:
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True)
            (out_dir / SPEC).write_text(data["spec_tla"])
            (out_dir / CFG).write_text(data["spec_cfg"])
            (out_dir / HARNESS).write_text(data["harness_py"])
            return out_dir
    raise DraftError(f"no well-formed bundle after {attempts} attempts; last error: {feedback}")
