"""Purity classification and pure-core extraction.

Impure code is classified and its pure core extracted, never verified. Classification is
conservative: anything that might touch the outside world, depend on hidden state, or mutate its
arguments makes the function impure.
"""

import ast
import copy
from dataclasses import dataclass, field
from typing import Literal

from apex.core.verdict import ReasonCode

IO_BUILTINS = frozenset(
    {
        "print",
        "open",
        "input",
        "exec",
        "eval",
        "compile",
        "breakpoint",
        "__import__",
        "exit",
        "quit",
    }
)
HIDDEN_STATE_BUILTINS = frozenset({"id", "hash", "globals", "locals", "vars", "setattr", "delattr"})
CONCURRENCY_MODULES = frozenset({"threading", "asyncio", "multiprocessing", "concurrent", "queue"})
IMPURE_MODULES = frozenset(
    {
        "os",
        "sys",
        "io",
        "pathlib",
        "shutil",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "logging",
        "random",
        "secrets",
        "time",
        "datetime",
        "uuid",
        "tempfile",
        "sqlite3",
        "pickle",
    }
)
FLOAT_MODULES = frozenset({"math", "cmath", "statistics", "decimal", "fractions", "numpy"})
MUTATING_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "clear",
        "sort",
        "reverse",
        "update",
        "add",
        "discard",
        "setdefault",
        "popitem",
    }
)


@dataclass
class PurityFinding:
    reason_code: ReasonCode
    message: str
    line: int
    removable: bool = False
    """A statement-level side effect (e.g. a log line) that can be sliced out for the pure core."""


@dataclass
class PurityResult:
    class_: Literal["pure", "impure"]
    findings: list[PurityFinding] = field(default_factory=list)
    pure_core: str | None = None
    """Source of the function with removable side effects sliced out, if that leaves it pure."""

    @property
    def reasons(self) -> list[str]:
        return [f"{f.reason_code}: {f.message} (line {f.line})" for f in self.findings]


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _Visitor(ast.NodeVisitor):
    def __init__(self, fn: ast.FunctionDef, module_aliases: dict[str, str]) -> None:
        self.params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        self.fresh = _fresh_locals(fn, self.params)
        self.aliases = module_aliases
        self.findings: list[PurityFinding] = []
        self._stmt_level_call: ast.Call | None = None

    def add(self, code: ReasonCode, msg: str, node: ast.AST, removable: bool = False) -> None:
        self.findings.append(PurityFinding(code, msg, getattr(node, "lineno", 0), removable))

    def visit_Expr(self, node: ast.Expr) -> None:
        self._stmt_level_call = node.value if isinstance(node.value, ast.Call) else None
        self.generic_visit(node)
        self._stmt_level_call = None

    def visit_Global(self, node: ast.Global) -> None:
        self.add(ReasonCode.IMPURE_IO, f"declares global {', '.join(node.names)}", node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.add(ReasonCode.IMPURE_IO, f"declares nonlocal {', '.join(node.names)}", node)

    def visit_Await(self, node: ast.Await) -> None:
        self.add(ReasonCode.NEEDS_CONCURRENCY_TOOL, "awaits a coroutine", node)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.add(ReasonCode.UNSUPPORTED_CONSTRUCT, "is a generator", node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.add(ReasonCode.UNSUPPORTED_CONSTRUCT, "is a generator", node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float | complex):
            self.add(ReasonCode.FLOAT_SEMANTICS, f"uses the float literal {node.value!r}", node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div):
            self.add(
                ReasonCode.FLOAT_SEMANTICS, "uses true division `/`, which yields a float", node
            )
        self.generic_visit(node)

    def _check_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Subscript | ast.Attribute):
            root = _root_name(target.value)
            if root in self.params:
                self.add(ReasonCode.IMPURE_IO, f"mutates its argument `{root}` in place", target)
            elif root is not None and root not in self.fresh:
                self.add(ReasonCode.IMPURE_IO, f"mutates `{ast.unparse(target)}`", target)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            self._check_target(t)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        removable = node is self._stmt_level_call
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in IO_BUILTINS:
                self.add(ReasonCode.IMPURE_IO, f"calls `{func.id}`", node, removable)
            elif func.id in HIDDEN_STATE_BUILTINS:
                self.add(ReasonCode.IMPURE_IO, f"calls `{func.id}`, which reads hidden state", node)
            elif func.id == "float":
                self.add(ReasonCode.FLOAT_SEMANTICS, "converts to float", node)
        elif isinstance(func, ast.Attribute):
            root = _root_name(func)
            module = self.aliases.get(root or "", root)
            if module in CONCURRENCY_MODULES:
                self.add(ReasonCode.NEEDS_CONCURRENCY_TOOL, f"calls `{ast.unparse(func)}`", node)
            elif module in IMPURE_MODULES:
                self.add(
                    ReasonCode.IMPURE_IO,
                    f"calls `{ast.unparse(func)}`",
                    node,
                    removable and module == "logging",
                )
            elif module in FLOAT_MODULES:
                self.add(ReasonCode.FLOAT_SEMANTICS, f"calls `{ast.unparse(func)}`", node)
            elif func.attr in MUTATING_METHODS and root in self.params:
                self.add(
                    ReasonCode.IMPURE_IO, f"mutates its argument `{root}` via .{func.attr}()", node
                )
            elif root == "self":
                self.add(ReasonCode.UNSUPPORTED_CONSTRUCT, "uses object state via `self`", node)
        self.generic_visit(node)


_FRESH = (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp)


def _fresh_locals(fn: ast.FunctionDef, params: set[str]) -> set[str]:
    """Locals that only ever hold a container built in this call (`out = []`): writing into one
    is invisible to the caller. A name bound any other way (`out = xs`, a loop target, `global`)
    might alias outside state, so it is not fresh."""
    fresh: set[str] = set()
    other: set[str] = set(params)
    for node in ast.walk(fn):
        if isinstance(node, ast.Global | ast.Nonlocal):
            other.update(node.names)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for name in _bound(t):
                    is_fresh = isinstance(t, ast.Name) and isinstance(node.value, _FRESH)
                    (fresh if is_fresh else other).add(name)
        elif isinstance(node, ast.For | ast.With | ast.NamedExpr | ast.AugAssign):
            targets = (
                [i.optional_vars for i in node.items if i.optional_vars]
                if isinstance(node, ast.With)
                else [node.target]
            )
            for t in targets:
                other.update(_bound(t))
    return fresh - other


def _bound(target: ast.expr) -> list[str]:
    """Names a binding target rebinds (`out[i] = v` and `obj.x = v` rebind nothing)."""
    match target:
        case ast.Name(id=name):
            return [name]
        case ast.Tuple(elts=elts) | ast.List(elts=elts):
            return [n for e in elts for n in _bound(e)]
        case ast.Starred(value=value):
            return _bound(value)
    return []


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to top-level module names (`import numpy as np` -> np: numpy)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = node.module.split(".")[0]
    return aliases


def classify(fn: ast.FunctionDef, module_tree: ast.Module) -> PurityResult:
    if fn.decorator_list:
        names = ", ".join(ast.unparse(d) for d in fn.decorator_list)
        return PurityResult(
            "impure",
            [
                PurityFinding(
                    ReasonCode.UNSUPPORTED_CONSTRUCT,
                    f"is decorated ({names}); decorators can change behaviour arbitrarily",
                    fn.lineno,
                )
            ],
        )
    visitor = _Visitor(fn, _module_aliases(module_tree))
    for stmt in fn.body:
        visitor.visit(stmt)
    findings = visitor.findings
    if not findings:
        return PurityResult("pure")
    pure_core = None
    if all(f.removable for f in findings):
        pure_core = _slice_out_side_effects(fn, {f.line for f in findings})
    return PurityResult("impure", findings, pure_core)


def _slice_out_side_effects(fn: ast.FunctionDef, lines: set[int]) -> str:
    """Drop statement-level side-effect calls (prints, log lines), keeping everything else."""
    clone = copy.deepcopy(fn)

    class Slicer(ast.NodeTransformer):
        def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
            if isinstance(node.value, ast.Call) and node.lineno in lines:
                return None
            return node

    clone = Slicer().visit(clone)
    for node in ast.walk(clone):  # a block emptied by slicing still needs a statement
        body = getattr(node, "body", None)
        if isinstance(body, list) and not body:
            body.append(ast.Pass())
    return ast.unparse(clone)
