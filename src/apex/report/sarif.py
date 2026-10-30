"""SARIF 2.1.0 renderer, for code-scanning UIs.

One result per property (and one per function judged as a whole). The verdict enum is the rule
ID, so a code-scanning UI groups by exactly the verdicts Apex reports; it never sees a softer
word. Levels: failures are errors, `unproven` is a warning, and the rest carry SARIF's `kind`
(`pass`, `review`, `notApplicable`) with level `none`, as the SARIF spec requires.
"""

import hashlib
from pathlib import Path
from typing import Any

from apex.core.report import FunctionResult, PropertyResult, Report, function_verdict
from apex.core.verdict import Verdict
from apex.report.explain import MEANING, target_of
from apex.report.render import detail

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

_KIND_LEVEL: dict[Verdict, tuple[str, str]] = {
    Verdict.PROVEN: ("pass", "none"),
    Verdict.PROVEN_UNREVIEWED: ("review", "none"),
    Verdict.PROVEN_CONDITIONAL: ("review", "none"),
    Verdict.REFUTED: ("fail", "error"),
    Verdict.FIDELITY_FAILED: ("fail", "error"),
    Verdict.CONTRADICTION: ("fail", "error"),
    Verdict.UNPROVEN: ("fail", "warning"),
    Verdict.OUT_OF_SCOPE: ("notApplicable", "none"),
    Verdict.FUZZED: ("review", "note"),
    Verdict.MODEL_CHECKED: ("review", "note"),
}


def _rule(v: Verdict) -> dict[str, Any]:
    level = _KIND_LEVEL[v][1]
    return {
        "id": v.value,
        "name": "".join(w.capitalize() for w in v.value.split("_")),
        "shortDescription": {"text": v.value.replace("_", " ")},
        "fullDescription": {"text": MEANING[v]},
        "defaultConfiguration": {"level": level if level != "none" else "note"},
    }


def _location(f: FunctionResult, root: Path) -> dict[str, Any]:
    uri = target_of(f, root).split("::", 1)[0]
    loc: dict[str, Any] = {"artifactLocation": {"uri": uri, "uriBaseId": "SRCROOT"}}
    if f.line:
        loc["region"] = {"startLine": f.line}
    return {"physicalLocation": loc}


def _fingerprint(f: FunctionResult, pid: str) -> str:
    return hashlib.sha256(f"{f.qualname}:{pid}".encode()).hexdigest()[:32]


def _result(
    f: FunctionResult, v: Verdict, pid: str, text: str, props: dict[str, Any], root: Path
) -> dict[str, Any]:
    kind, level = _KIND_LEVEL[v]
    return {
        "ruleId": v.value,
        "kind": kind,
        "level": level,
        "message": {"text": text},
        "locations": [_location(f, root)],
        "partialFingerprints": {"apexProperty/v1": _fingerprint(f, pid)},
        "properties": props,
    }


def _props(p: PropertyResult) -> dict[str, Any]:
    out: dict[str, Any] = {"verdict": p.verdict.value}
    if p.gap:
        out["reasonCode"] = p.gap.reason_code.value
    if p.lean_statement:
        out["leanStatement"] = p.lean_statement
    if p.review:
        out["review"] = p.review.status
    if p.counterexample:
        out["counterexample"] = p.counterexample.model_dump(exclude_none=True)
    return out


def to_sarif(report: Report, root: Path) -> dict[str, Any]:
    results = []
    seen: set[Verdict] = set()
    for f in report.functions:
        whole = function_verdict(f)
        if whole is not None and f.gap is not None:
            seen.add(whole)
            results.append(
                _result(
                    f,
                    whole,
                    "-",
                    f"{f.qualname}: {whole.value}: {f.gap.reason_code}: {f.gap.explanation}",
                    {"verdict": whole.value, "reasonCode": f.gap.reason_code.value},
                    root,
                )
            )
        for p in f.properties:
            seen.add(p.verdict)
            text = f"{f.qualname} {p.id}: {p.verdict.value}"
            if d := detail(p, target_of(f, root)):
                text += f": {d}"
            results.append(_result(f, p.verdict, p.id, text, _props(p), root))
    rules = [_rule(v) for v in Verdict if v in seen]
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Apex",
                        "version": report.apex_version,
                        "semanticVersion": report.apex_version.split(".dev")[0],
                        "rules": rules,
                    }
                },
                "originalUriBaseIds": {"SRCROOT": {"uri": root.resolve().as_uri() + "/"}},
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "exitCode": report.exit_code,
                        "endTimeUtc": report.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                ],
                "results": results,
                "properties": {
                    "mode": report.mode,
                    "provider": report.provider,
                    "leanToolchain": report.lean_toolchain,
                    "mathlibRev": report.mathlib_rev,
                },
            }
        ],
    }
