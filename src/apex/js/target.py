"""`path.ts::name` targets: the real JS source and identity, with the function lowered."""

import ast
from pathlib import Path

from apex.analyze.target import FunctionInfo, TargetError
from apex.js.lower import lower_module
from apex.js.parse import load_module, text


def load_js_function(path: Path, qualname: str) -> FunctionInfo:
    try:
        module = load_module(path)
    except (OSError, UnicodeDecodeError) as e:
        raise TargetError(f"{path}: {e}") from e
    if module.root.has_error:
        raise TargetError(f"{path}: the file does not parse as JavaScript / TypeScript")
    if qualname not in module.functions:
        raise TargetError(f"{path} has no top-level function `{qualname}`")
    tree, lowered = lower_module(module)

    def info(name: str) -> FunctionInfo:
        fn = module.functions[name]
        lw = lowered[name]
        source = text(fn.decl)
        if fn.jsdoc:
            source = fn.jsdoc + "\n" + source  # the JSDoc is intent: editing it voids approvals
        return FunctionInfo(
            path=path,
            module=path.stem,  # as for Python; the Lean namespace sanitizes it
            qualname=name,
            node=lw.node,
            source=source,
            module_tree=tree,
            module_source=module.source,
            language="js",
            exported=fn.exported,
            numeric=frozenset(lw.numeric),
            typed=lw.typed,
            jsdoc=fn.jsdoc,
        )

    root = info(qualname)
    order: list[str] = []
    seen = {qualname}

    def visit(node: ast.FunctionDef) -> None:
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in lowered
                and call.func.id not in seen
            ):
                seen.add(call.func.id)
                visit(lowered[call.func.id].node)
                order.append(call.func.id)

    visit(root.node)
    root.callees = [info(n) for n in order]
    if any(c.numeric for c in root.callees):
        root.numeric = root.numeric | frozenset().union(*(c.numeric for c in root.callees))
    return root
