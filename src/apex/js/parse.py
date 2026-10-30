"""Parsing JS / TS modules with tree-sitter: top-level functions, exports, JSDoc, types."""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Node, Parser


@cache
def _parser() -> Parser:
    # The TypeScript grammar also parses plain JavaScript (TS is a superset of it).
    return Parser(Language(ts_typescript.language_typescript()))


def parse(source: str) -> Node:
    return _parser().parse(source.encode()).root_node


def text(node: Node | None) -> str:
    if node is None:
        return ""
    data = node.text
    return data.decode() if data is not None else ""


@dataclass
class JsParam:
    name: str
    type: Node | None
    """The TypeScript type node, if the source annotates one."""
    jsdoc_type: str | None = None
    """`@param {T} name` from JSDoc, for plain JavaScript."""
    optional: bool = False
    node: Node | None = None


@dataclass
class JsFunction:
    name: str
    node: Node
    """The function (function_declaration, or the arrow_function / function_expression value)."""
    decl: Node
    """The whole declaration (with `export`, if any): its text is the function's identity."""
    exported: bool
    jsdoc: str | None
    params: list[JsParam]
    returns: Node | None
    jsdoc_returns: str | None
    body: Node
    """A statement_block, or an expression for a concise arrow function."""
    is_async: bool = False
    is_generator: bool = False
    rest: bool = False


@dataclass
class JsModule:
    path: Path
    source: str
    root: Node
    functions: dict[str, JsFunction] = field(default_factory=dict)
    globals: set[str] = field(default_factory=set)
    """Names declared at the top level that are not functions (module state)."""
    imports: dict[str, str] = field(default_factory=dict)
    """Local name -> module specifier, for `import x from "m"` / `require("m")`."""


_JSDOC_PARAM = re.compile(r"@param\s+\{([^}]*)\}\s+\[?(\w+)")
_JSDOC_RETURNS = re.compile(r"@returns?\s+\{([^}]*)\}")


def _jsdoc_before(node: Node) -> str | None:
    prev = node.prev_named_sibling
    # Only a doc comment that ends right before the declaration documents it.
    if (
        prev is not None
        and prev.type == "comment"
        and text(prev).startswith("/**")
        and node.start_point.row - prev.end_point.row <= 1
    ):
        return text(prev)
    return None


def _params(fn: Node, jsdoc: str | None) -> tuple[list[JsParam], bool]:
    doc_types = dict((n, t) for t, n in _JSDOC_PARAM.findall(jsdoc or ""))
    params: list[JsParam] = []
    rest = False
    plist = fn.child_by_field_name("parameters")
    if plist is None:  # `x => ...`
        single = fn.child_by_field_name("parameter")
        if single is not None:
            name = text(single)
            params.append(JsParam(name, None, doc_types.get(name), node=single))
        return params, rest
    for p in plist.named_children:
        if p.type == "comment":
            continue
        if p.type in ("required_parameter", "optional_parameter"):
            pattern = p.child_by_field_name("pattern")
            ann = p.child_by_field_name("type")
            type_node = ann.named_children[0] if ann is not None and ann.named_children else None
            name = text(pattern)
            if pattern is not None and pattern.type == "rest_pattern":
                rest = True
            params.append(
                JsParam(
                    name,
                    type_node,
                    doc_types.get(name),
                    optional=p.type == "optional_parameter"
                    or p.child_by_field_name("value") is not None,
                    node=p,
                )
            )
        elif p.type == "identifier":
            params.append(JsParam(text(p), None, doc_types.get(text(p)), node=p))
        else:  # destructuring, defaults in JS, ...
            params.append(JsParam(text(p), None, None, optional=True, node=p))
    return params, rest


def _function(name: str, fn: Node, decl: Node, exported: bool) -> JsFunction:
    jsdoc = _jsdoc_before(decl)
    params, rest = _params(fn, jsdoc)
    ret = fn.child_by_field_name("return_type")
    ret_type = ret.named_children[0] if ret is not None and ret.named_children else None
    m = _JSDOC_RETURNS.search(jsdoc or "")
    head = text(fn)[:12]
    return JsFunction(
        name=name,
        node=fn,
        decl=decl,
        exported=exported,
        jsdoc=jsdoc,
        params=params,
        returns=ret_type,
        jsdoc_returns=m.group(1) if m else None,
        body=fn.child_by_field_name("body") or fn,
        is_async=head.startswith("async"),
        is_generator=fn.type in ("generator_function_declaration", "generator_function")
        or "function*" in text(fn)[:20],
        rest=rest,
    )


def load_module(path: Path) -> JsModule:
    source = path.read_text()
    root = parse(source)
    mod = JsModule(path=path, source=source, root=root)
    exported_names: set[str] = set()
    for stmt in root.named_children:
        exported = stmt.type == "export_statement"
        inner = stmt
        if exported:
            decl = stmt.child_by_field_name("declaration")
            if decl is None:  # `export { a, b }`
                for spec in stmt.named_children:
                    if spec.type == "export_clause":
                        for s in spec.named_children:
                            exported_names.add(text(s.child_by_field_name("name") or s))
                continue
            inner = decl
        if inner.type in ("function_declaration", "generator_function_declaration"):
            name = text(inner.child_by_field_name("name"))
            mod.functions[name] = _function(name, inner, stmt, exported)
        elif inner.type in ("lexical_declaration", "variable_declaration"):
            for d in inner.named_children:
                if d.type != "variable_declarator":
                    continue
                name = text(d.child_by_field_name("name"))
                value = d.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression"):
                    mod.functions[name] = _function(name, value, stmt, exported)
                elif value is not None and _require(value):
                    mod.imports[name] = _require(value) or ""
                else:
                    mod.globals.add(name)
        elif inner.type == "import_statement":
            source_node = inner.child_by_field_name("source")
            spec = text(source_node).strip("'\"")
            for ident in _imported_names(inner):
                mod.imports[ident] = spec
        elif inner.type == "expression_statement":
            # CommonJS: module.exports = { f, g } / exports.f = f
            exported_names |= _commonjs_exports(inner)
    for name in exported_names:
        if name in mod.functions:
            mod.functions[name].exported = True
    return mod


def _require(node: Node) -> str | None:
    if node.type == "call_expression" and text(node.child_by_field_name("function")) == "require":
        args = node.child_by_field_name("arguments")
        if args is not None and args.named_children:
            return text(args.named_children[0]).strip("'\"")
    return None


def _imported_names(node: Node) -> list[str]:
    out = []
    for n in _walk(node):
        if n.type == "identifier" and n.parent is not None and n.parent.type != "string":
            out.append(text(n))
    return out


def _commonjs_exports(stmt: Node) -> set[str]:
    out: set[str] = set()
    expr = stmt.named_children[0] if stmt.named_children else None
    if expr is None or expr.type != "assignment_expression":
        return out
    left = text(expr.child_by_field_name("left"))
    right = expr.child_by_field_name("right")
    if left == "module.exports" and right is not None and right.type == "object":
        for prop in right.named_children:
            if prop.type == "shorthand_property_identifier":
                out.add(text(prop))
            elif prop.type == "pair":
                out.add(text(prop.child_by_field_name("value")))
    elif left.startswith(("exports.", "module.exports.")) and right is not None:
        out.add(text(right))
    return out


def _walk(node: Node):
    yield node
    for c in node.named_children:
        yield from _walk(c)


def walk(node: Node):
    return _walk(node)


def parse_type(type_text: str) -> Node | None:
    """A JSDoc type string as a TypeScript type node (JSDoc types are TS-compatible enough)."""
    root = parse(f"let __apex_t: {type_text};")
    for n in _walk(root):
        if n.type == "type_annotation" and n.named_children:
            return n.named_children[0]
    return None
