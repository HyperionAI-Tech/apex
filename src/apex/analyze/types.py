"""Python type annotations in the supported subset, and their Lean counterparts."""

import ast
from dataclasses import dataclass


class UnsupportedType(ValueError):
    pass


@dataclass(frozen=True)
class PyType:
    """A type in the MVP subset: int, bool, list[T], tuple[...], T | None."""

    kind: str  # "int" | "bool" | "list" | "tuple" | "option"
    args: tuple["PyType", ...] = ()

    def lean(self) -> str:
        match self.kind:
            case "int":
                return "Int"
            case "bool":
                return "Bool"
            case "list":
                return f"(List {self.args[0].lean()})"
            case "option":
                return f"(Option {self.args[0].lean()})"
            case "tuple":
                # Lean products are right-nested: (A × B × C) = A × (B × C).
                return "(" + " × ".join(a.lean() for a in self.args) + ")"
        raise AssertionError(self.kind)

    def python(self) -> str:
        match self.kind:
            case "int" | "bool":
                return self.kind
            case "list":
                return f"list[{self.args[0].python()}]"
            case "option":
                return f"{self.args[0].python()} | None"
            case "tuple":
                return "tuple[" + ", ".join(a.python() for a in self.args) + "]"
        raise AssertionError(self.kind)


INT = PyType("int")
BOOL = PyType("bool")


def list_of(t: PyType) -> PyType:
    return PyType("list", (t,))


def option_of(t: PyType) -> PyType:
    return PyType("option", (t,))


def parse_annotation(node: ast.expr | None) -> PyType:
    if node is None:
        raise UnsupportedType("missing type annotation")
    match node:
        case ast.Name(id="int"):
            return INT
        case ast.Name(id="bool"):
            return BOOL
        case ast.Name(id="float"):
            raise UnsupportedType("float")
        case ast.Name(id="str"):
            raise UnsupportedType("str is not in the MVP subset")
        case ast.Subscript(value=ast.Name(id="list" | "List"), slice=inner):
            return list_of(parse_annotation(inner))
        case ast.Subscript(value=ast.Name(id="tuple" | "Tuple"), slice=ast.Tuple(elts=elts)):
            if len(elts) < 2 or any(
                isinstance(e, ast.Constant) and e.value is Ellipsis for e in elts
            ):
                raise UnsupportedType("only fixed-size tuples of 2+ elements are supported")
            return PyType("tuple", tuple(parse_annotation(e) for e in elts))
        case ast.Subscript(value=ast.Name(id="Optional"), slice=inner):
            return option_of(parse_annotation(inner))
        case ast.BinOp(op=ast.BitOr(), left=left, right=ast.Constant(value=None)):
            return option_of(parse_annotation(left))
        case ast.BinOp(op=ast.BitOr(), left=ast.Constant(value=None), right=right):
            return option_of(parse_annotation(right))
    raise UnsupportedType(f"type `{ast.unparse(node)}` is not in the MVP subset")
