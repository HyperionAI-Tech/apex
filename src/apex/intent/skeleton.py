"""Deterministic Intent Model skeleton: purity, signature, and examples mined from doctests and
test assertions. English specs and docstring prose need an LLM and arrive in Phase 2.
"""

import ast
import doctest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.analyze.purity import PurityResult
from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.core.intent import (
    Conflict,
    FunctionRef,
    IntentModel,
    Property,
    Purity,
    Signature,
    Source,
)


@dataclass
class Example:
    args: tuple[Any, ...]
    expected: Any
    source: Source
    text: str


def _literal_call(expr: ast.expr, fn_name: str) -> tuple[Any, ...] | None:
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == fn_name
        and not expr.keywords
    ):
        try:
            return tuple(ast.literal_eval(a) for a in expr.args)
        except ValueError:
            return None
    return None


def doctest_examples(info: FunctionInfo) -> list[Example]:
    doc = info.docstring
    if not doc:
        return []
    out = []
    doc_line = info.node.body[0].lineno if info.node.body else info.lineno
    for ex in doctest.DocTestParser().get_examples(doc):
        try:
            call = ast.parse(ex.source.strip(), mode="eval").body
            expected = ast.literal_eval(ex.want.strip())
        except (SyntaxError, ValueError):
            continue
        args = _literal_call(call, info.qualname)
        if args is None:
            continue
        loc = f"{info.path.name}:{doc_line + ex.lineno}"
        out.append(
            Example(
                args,
                expected,
                Source(type="docstring", loc=loc),
                f"{ex.source.strip()} == {ex.want.strip()}",
            )
        )
    return out


def find_test_files(info: FunctionInfo) -> list[Path]:
    name = f"test_{info.path.stem}.py"
    here = info.path.resolve().parent
    candidates = [here / name, here / "tests" / name]
    candidates += [p / "tests" / name for p in list(here.parents)[:3]]
    return [p for p in dict.fromkeys(candidates) if p.is_file()]


def test_examples(info: FunctionInfo) -> list[Example]:
    out = []
    for path in find_test_files(info):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assert)
                and isinstance(node.test, ast.Compare)
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.Eq)
            ):
                continue
            left, right = node.test.left, node.test.comparators[0]
            for call, value in ((left, right), (right, left)):
                args = _literal_call(call, info.qualname)
                if args is None:
                    continue
                try:
                    expected = ast.literal_eval(value)
                except ValueError:
                    continue
                out.append(
                    Example(
                        args,
                        expected,
                        Source(type="test", loc=f"{path.name}:{node.lineno}"),
                        ast.unparse(node.test),
                    )
                )
                break
    return out


def _fits(value: Any, ty: PyType) -> bool:
    match ty.kind:
        case "int":
            return isinstance(value, int) and not isinstance(value, bool)
        case "bool":
            return isinstance(value, bool)
        case "list":
            return isinstance(value, list) and all(_fits(v, ty.args[0]) for v in value)
        case "tuple":
            return (
                isinstance(value, tuple)
                and len(value) == len(ty.args)
                and all(_fits(v, t) for v, t in zip(value, ty.args, strict=True))
            )
        case "option":
            return value is None or _fits(value, ty.args[0])
    return False


def build_intent(
    info: FunctionInfo,
    purity: PurityResult,
    params: list[tuple[str, PyType]] | None,
    returns: PyType | None,
) -> tuple[IntentModel, list[Example]]:
    examples = doctest_examples(info) + test_examples(info)
    if params is not None and returns is not None:
        examples = [
            e
            for e in examples
            if len(e.args) == len(params)
            and all(_fits(a, t) for a, (_, t) in zip(e.args, params, strict=True))
            and _fits(e.expected, returns)
        ]

    props, conflicts, seen = [], [], {}
    for k, ex in enumerate(examples, start=1):
        pid = f"E{k}"
        key = repr(ex.args)
        if key in seen and seen[key][1].expected != ex.expected:
            other_id, other = seen[key]
            conflicts.append(
                Conflict(
                    property_ids=[other_id, pid],
                    description=f"{other.text} contradicts {ex.text}",
                    sources=[other.source, ex.source],
                )
            )
        seen.setdefault(key, (pid, ex))
        props.append(
            Property(
                id=pid,
                kind="example",
                text=ex.text,
                formal_hint=ex.text,
                sources=[ex.source],
                confidence=1.0,
            )
        )

    intent = IntentModel(
        function=FunctionRef(
            qualname=f"{info.module}.{info.qualname}", file=str(info.path), sha256=info.sha256
        ),
        purity=Purity(**{"class": purity.class_, "reasons": purity.reasons}),
        signature=Signature(
            params=[(n, t.python()) for n, t in params]
            if params
            else [
                (a.arg, ast.unparse(a.annotation) if a.annotation else "?")
                for a in info.node.args.args
            ],
            returns=returns.python()
            if returns
            else (ast.unparse(info.node.returns) if info.node.returns else "?"),
        ),
        properties=props,
        conflicts=conflicts,
    )
    return intent, examples
