"""Python values <-> Lean terms, for examples (statements) and the fidelity bridge (inputs)."""

from typing import Any

from apex.analyze.types import PyType


class LiteralError(ValueError):
    pass


def to_lean(value: Any, ty: PyType) -> str:
    """Render a Python value as a Lean term of the Lean counterpart of `ty`."""
    match ty.kind:
        case "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise LiteralError(f"expected int, got {value!r}")
            return f"({value} : Int)"
        case "bool":
            if not isinstance(value, bool):
                raise LiteralError(f"expected bool, got {value!r}")
            return "true" if value else "false"
        case "list":
            if not isinstance(value, list):
                raise LiteralError(f"expected list, got {value!r}")
            items = ", ".join(to_lean(v, ty.args[0]) for v in value)
            return f"([{items}] : {ty.lean()})"
        case "tuple":
            if not isinstance(value, tuple) or len(value) != len(ty.args):
                raise LiteralError(f"expected {len(ty.args)}-tuple, got {value!r}")
            return "(" + ", ".join(to_lean(v, t) for v, t in zip(value, ty.args, strict=True)) + ")"
        case "option":
            return "none" if value is None else f"(some {to_lean(value, ty.args[0])})"
    raise LiteralError(f"unsupported type {ty.kind}")


def normalize(value: Any) -> Any:
    """Canonical form for comparing Python results with Lean `PyRepr` output.

    Tuples become right-nested pairs (Lean's representation) and every leaf is tagged with its
    type, so `True` never equals `1` the way it does in Python.
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if value is None:
        return ("none",)
    if isinstance(value, list):
        return ("list", tuple(normalize(v) for v in value))
    if isinstance(value, tuple):
        if len(value) <= 2:
            return ("tuple", tuple(normalize(v) for v in value))
        return ("tuple", (normalize(value[0]), normalize(value[1:])))
    return ("other", repr(value))
