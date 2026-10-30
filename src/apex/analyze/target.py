"""Resolving `path.py::qualname` targets to function source and AST."""

import ast
from dataclasses import dataclass, field
from pathlib import Path

from apex.core.hashing import sha256_text


class TargetError(ValueError):
    """The target string or file is invalid. Maps to exit code 3."""


@dataclass
class FunctionInfo:
    path: Path
    module: str
    """Module name derived from the file stem; used as the Lean namespace."""
    qualname: str
    node: ast.FunctionDef
    source: str
    """The function's own source text."""
    module_tree: ast.Module
    module_source: str
    callees: list["FunctionInfo"] = field(default_factory=list)
    """Same-module functions this one calls, transitively, in dependency order."""
    language: str = "python"
    """`python`, or `js` (JavaScript / TypeScript): then `node` and `module_tree` are the
    function lowered to the Python subset (apex.js.lower), and `source` is the real JS."""
    exported: bool = True
    """JS only: whether the module exports the function (Apex can only run exported ones)."""
    numeric: frozenset[str] = frozenset()
    """JS only: the numeric kinds in the signature, `number` (float-backed) and/or `bigint`."""
    typed: bool = True
    """JS only: every parameter and the result carry a TypeScript or JSDoc type."""
    jsdoc: str | None = None

    @property
    def lineno(self) -> int:
        return self.node.lineno

    @property
    def sha256(self) -> str:
        """Identity for review approvals: the function plus every same-module function it calls.

        Changing any of them changes what a theorem is about, so approvals must lapse.
        """
        parts = [c.source for c in self.callees] + [self.source]
        return sha256_text("\n\n".join(parts))

    @property
    def docstring(self) -> str | None:
        return ast.get_docstring(self.node)


def parse_target(target: str) -> tuple[Path, str]:
    if "::" not in target:
        raise TargetError(f"target must look like path.py::qualname, got `{target}`")
    path_s, qualname = target.split("::", 1)
    path = Path(path_s)
    if not path.is_file():
        raise TargetError(f"no such file: {path}")
    if not qualname.isidentifier() or not qualname.isascii():
        raise TargetError(f"only top-level functions are supported in the MVP, got `{qualname}`")
    return path, qualname


def _top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def top_level_functions(path: Path) -> list[str]:
    """Names of the top-level functions defined in a module, in source order."""
    from apex.js import is_js

    if is_js(path):
        from apex.js.parse import load_module

        return list(load_module(path).functions)
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as e:
        raise TargetError(f"{path}: {e}") from e
    return list(_top_level_functions(tree))


def load_function(target: str) -> FunctionInfo:
    path, qualname = parse_target(target)
    from apex.js import is_js

    if is_js(path):
        from apex.js.target import load_js_function

        return load_js_function(path, qualname)
    module_source = path.read_text()
    try:
        tree = ast.parse(module_source, filename=str(path))
    except SyntaxError as e:
        raise TargetError(f"{path}: {e}") from e
    functions = _top_level_functions(tree)
    if qualname not in functions:
        raise TargetError(f"{path} has no top-level function `{qualname}`")

    def info(name: str) -> FunctionInfo:
        node = functions[name]
        src = ast.get_source_segment(module_source, node) or ast.unparse(node)
        return FunctionInfo(
            path=path,
            module=path.stem,
            qualname=name,
            node=node,
            source=src,
            module_tree=tree,
            module_source=module_source,
        )

    root = info(qualname)
    order: list[str] = []
    seen: set[str] = {qualname}

    def visit(fn: ast.FunctionDef) -> None:
        for call in ast.walk(fn):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in functions
                and call.func.id not in seen
            ):
                seen.add(call.func.id)
                visit(functions[call.func.id])
                order.append(call.func.id)

    visit(root.node)
    root.callees = [info(n) for n in order]
    return root
