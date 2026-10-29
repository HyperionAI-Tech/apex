"""Engine-mode proposals: ask a model to formalize English spec items and docstring prose.

The model sees the signature, the docstring and the spec items, but not the function body: it is
asked what the *intent* says, and showing it the code would invite it to describe the code instead,
which turns a bug into a "property". Only `--from-source` shows the body, and everything proposed
from it is marked `basis: source` and sits at the confidence floor.
"""

import ast
from typing import Any

from apex.analyze.target import FunctionInfo
from apex.core.intent import IntentModel
from apex.intent.english import SpecItem
from apex.intent.proposals import ProposalSet, ProposedPrecondition, ProposedProperty
from apex.llm.providers import Provider, ProviderError

_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item", "text", "formal_hint", "basis"],
    "properties": {
        "item": {
            "type": "string",
            "description": "ID of the English spec item this formalizes (S1, ...), or empty.",
        },
        "text": {"type": "string", "description": "The claim in one plain-English sentence."},
        "formal_hint": {"type": "string", "description": "The claim in the hint language."},
        "basis": {"type": "string", "enum": ["spec", "docstring", "source"]},
    },
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["properties", "preconditions"],
    "properties": {
        "properties": {"type": "array", "items": _ITEM},
        "preconditions": {"type": "array", "items": _ITEM},
    },
}

SYSTEM = """\
You turn the stated intent of one Python function into formal hints for Apex, a verifier that \
proves them in Lean or finds counterexamples. Your output is only a proposal: Apex parses every \
hint, and a person approves each theorem drafted from it.

The hint language is a Python boolean expression over the function's parameters and `r`, its \
return value. `A -> B` means "if A then B" and may be chained. Allowed: integer and boolean \
literals and None; + - * // % and ** with a literal exponent; comparisons, including chains; \
and, or, not; `is None` / `is not None`; `in`; list and tuple literals, indexing and slices; \
conditional expressions; comprehensions and generator expressions; the functions all, any, len, \
abs, min, max, sum, range; and functions defined in the same module (listed in the request). \
Nothing else: no attribute access, no strings, no floats, no other calls.

How to formalize:
- State what the intent says, not what you expect the code to do. If the intent is silent on \
something, leave it out.
- One claim per hint. Split a sentence that makes two claims into two entries.
- A requirement on the inputs (for example "lo must not exceed hi") is a precondition: a hint \
over the parameters only, without `r`.
- When the result may be None, a claim about the value needs `r is not None -> ...`.
- For an English spec item, set `item` to its ID and `basis` to "spec". For docstring prose, \
leave `item` empty and set `basis` to "docstring". Only when the request includes the function \
body may you use `basis` "source", for intent the code makes evident but no document states.
- If an item cannot be expressed in the hint language, omit it rather than approximate it.
- Do not repeat properties the request lists as already formal."""


def build_prompt(
    info: FunctionInfo, intent: IntentModel, items: list[SpecItem], *, from_source: bool
) -> str:
    helpers = [
        _signature_of(n)
        for n in info.module_tree.body
        if isinstance(n, ast.FunctionDef) and n.name != info.qualname
    ]
    parts = [f"Function:\n    {_signature_of(info.node)}"]
    parts.append("Docstring:\n" + (_indent(info.docstring) if info.docstring else "    (none)"))
    if helpers:
        parts.append(
            "Other functions in the same module:\n" + "\n".join(f"    {h}" for h in helpers)
        )
    open_items = [it for it in items if not it.hint]
    if open_items:
        parts.append(
            "English spec items to formalize:\n"
            + "\n".join(
                f"    {it.id}: {'Requires: ' if it.is_precondition else ''}{it.text}"
                for it in open_items
            )
        )
    formal = [p for p in intent.properties if p.formal_hint and p.kind != "example"]
    formal_pres = [c for c in intent.preconditions if c.formal_hint]
    if formal or formal_pres:
        parts.append(
            "Already formal (do not repeat):\n"
            + "\n".join(f"    precondition {c.formal_hint}" for c in formal_pres)
            + ("\n" if formal_pres and formal else "")
            + "\n".join(f"    {p.formal_hint}" for p in formal)
        )
    if from_source:
        parts.append(
            'Function body (you may propose basis "source" properties for intent it makes '
            "evident):\n" + _indent(info.source)
        )
    if not open_items and not info.docstring and not from_source:
        parts.append("There is nothing to formalize; return empty lists.")
    return "\n\n".join(parts)


def _signature_of(node: ast.FunctionDef) -> str:
    args = ", ".join(
        f"{a.arg}: {ast.unparse(a.annotation)}" if a.annotation else a.arg for a in node.args.args
    )
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"def {node.name}({args}){ret}"


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def propose(
    provider: Provider,
    info: FunctionInfo,
    intent: IntentModel,
    items: list[SpecItem],
    *,
    from_source: bool = False,
) -> ProposalSet:
    prompt = build_prompt(info, intent, items, from_source=from_source)
    data = provider.complete_json(SYSTEM, prompt, RESPONSE_SCHEMA)
    try:
        props = [
            ProposedProperty(
                item=d.get("item") or None,
                text=d["text"],
                formal_hint=d["formal_hint"],
                basis=d["basis"],
            )
            for d in data.get("properties", [])
            if from_source or d.get("basis") != "source"
        ]
        pres = [
            ProposedPrecondition(
                item=d.get("item") or None,
                text=d["text"],
                formal_hint=d["formal_hint"],
                basis=d["basis"],
            )
            for d in data.get("preconditions", [])
            if from_source or d.get("basis") != "source"
        ]
    except (KeyError, TypeError, ValueError) as e:
        raise ProviderError(f"{provider.name} returned malformed proposals: {e}") from e
    return ProposalSet(proposed_by=provider.name, properties=props, preconditions=pres)
