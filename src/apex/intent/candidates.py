"""What every intent source produces before fusion: candidate examples, properties and
preconditions, each with the place it came from."""

import ast
from dataclasses import dataclass
from typing import Any, Literal

from apex.core.intent import Source


@dataclass
class Example:
    args: tuple[Any, ...]
    expected: Any
    source: Source
    text: str


@dataclass
class Candidate:
    kind: Literal["property", "precondition"]
    text: str
    source: Source
    hint: str | None = None
    confidence: float = 1.0
    prop_kind: Literal["postcondition", "invariant", "equivalence", "termination", "other"] = (
        "postcondition"
    )
    proposed_by: str | None = None
    item: str | None = None
    """For a model's formalization of an English spec item: that item's ID (S1, S2, ...)."""


@dataclass
class Mined:
    examples: list[Example]
    candidates: list[Candidate]

    def __add__(self, other: "Mined") -> "Mined":
        return Mined(self.examples + other.examples, self.candidates + other.candidates)


EMPTY = Mined([], [])


def literal_call(expr: ast.expr, fn_name: str) -> tuple[Any, ...] | None:
    """Arguments of `fn_name(<literals>)`, or None if `expr` is not such a call."""
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


def as_example(test: ast.expr, fn_name: str) -> tuple[tuple[Any, ...], Any] | None:
    """`f(<literals>) == <literal>` (either way round) as (args, expected)."""
    if not (
        isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
    ):
        return None
    left, right = test.left, test.comparators[0]
    for call, value in ((left, right), (right, left)):
        args = literal_call(call, fn_name)
        if args is None:
            continue
        try:
            return args, ast.literal_eval(value)
        except ValueError:
            return None
    return None
