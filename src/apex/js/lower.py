"""Lowering JS / TS functions to the pure-Python subset AST the rest of Apex understands.

The lowering is a *translation of meaning*, not of syntax, and it is conservative: wherever
JavaScript and the Python subset disagree, the JS meaning is made explicit or the construct is
refused with a reason.

- `%` truncates toward zero in JS: it lowers to `__apex_tmod__(a, b)` (Lean `Int.tmod`).
- `/` is real division: only `Math.floor(a / b)` (floor division) and `Math.trunc(a / b)`
  (`__apex_tdiv__`, Lean `Int.tdiv`) are integer operations; a bare `/` is a float.
- `===` / `!==` compare values; the translator refuses them on arrays (identity in JS). Loose
  `==` is refused (coercion). Bitwise operators work on 32-bit integers in JS: refused.
- `xs.length`, `Math.abs/min/max`, `xs.includes`, `xs.map/filter/some/every/reduce`, spread, and
  `slice` with literal bounds lower to their Python-subset equivalents.

Impure JavaScript lowers onto the constructs the purity classifier already rejects, so purity has
one implementation: `console.*` becomes `print`, `Math.random` uses `random`, `Date` uses `time`,
`fs` / `process` / `fetch` / imported modules use `os`, timers and `async` are concurrency,
mutating a parameter stays a mutation, and assigning an undeclared name declares a global.

Anything else lowers to `__apex_unsupported__("<what>", "<REASON>")`, which the translator turns
into a gap naming the JS construct and its line. Leading guards (`if (c) throw ...`, and
`assert(c)` from node:assert) become preconditions, like a Python function's leading asserts.
"""

import ast
from dataclasses import dataclass, field

from tree_sitter import Node

from apex.js.parse import JsFunction, JsModule, parse_type, text, walk
from apex.translate.lean import REDUCE_HOOK, TDIV_HOOK, TMOD_HOOK, UNSUPPORTED_HOOK

UNSUPPORTED = UNSUPPORTED_HOOK
TMOD = TMOD_HOOK
TDIV = TDIV_HOOK
REDUCE = REDUCE_HOOK
JS_HELPERS = frozenset({TMOD, TDIV})
"""Builtins the lowering introduces; the hint evaluator and the translator both know them."""

_IMPURE_IMPORTS = ("random", "time", "os", "asyncio")
_IO_OBJECTS = {"fs", "process", "fetch", "XMLHttpRequest", "localStorage", "document", "window"}
_TIMERS = {"setTimeout", "setInterval", "setImmediate", "queueMicrotask", "Promise", "Worker"}
_MUTATORS = {
    "pop": "pop",
    "shift": "pop",
    "unshift": "insert",
    "splice": "pop",
    "sort": "sort",
    "reverse": "reverse",
    "fill": "clear",
}
"""Array methods that mutate in place, mapped to a Python method the purity classifier knows."""


@dataclass
class Lowered:
    node: ast.FunctionDef
    numeric: set[str] = field(default_factory=set)
    """The numeric kinds the signature uses: "number" (float-backed) and/or "bigint"."""
    typed: bool = True
    """Every parameter and the result have a type (TypeScript or JSDoc)."""


def _at[T: ast.AST](py: T, ts: Node | None) -> T:
    if ts is not None:
        for n in ast.walk(py):
            # (new nodes already carry end_lineno = None, but no lineno until it is set)
            if isinstance(n, ast.stmt | ast.expr | ast.arg) and getattr(n, "lineno", None) is None:
                n.lineno = ts.start_point.row + 1
                n.col_offset = ts.start_point.column
                n.end_lineno = ts.end_point.row + 1
                n.end_col_offset = ts.end_point.column
    return py


def unsupported(
    what: str,
    ts: Node | None,
    reason: str = "UNSUPPORTED_CONSTRUCT",
    keep: list[ast.expr] | None = None,
) -> ast.expr:
    """A refusal marker. `keep`: the construct's lowered operands, carried along so the purity
    classifier still sees them (`Math.floor(Math.random() * n)` must stay impure, not just
    unsupported, so refutation never runs it)."""
    call = ast.Call(
        func=ast.Name(UNSUPPORTED, ast.Load()),
        args=[ast.Constant(what), ast.Constant(reason), *(keep or [])],
        keywords=[],
    )
    return _at(call, ts)


def _stmt(what: str, ts: Node | None, reason: str = "UNSUPPORTED_CONSTRUCT") -> ast.stmt:
    return _at(ast.Expr(unsupported(what, ts, reason)), ts)


def _name(name: str) -> ast.Name:
    return ast.Name(name, ast.Load())


def _call(name: str, args: list[ast.expr]) -> ast.expr:
    return ast.Call(_name(name), args, [])


def _method(obj: ast.expr, name: str, args: list[ast.expr]) -> ast.expr:
    return ast.Call(ast.Attribute(obj, name, ast.Load()), args, [])


def _arguments(names: list[ast.arg]) -> ast.arguments:
    return ast.arguments(
        posonlyargs=[],
        args=names,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )


# ---- types ------------------------------------------------------------------------------------


def lower_type(node: Node | None, numeric: set[str]) -> ast.expr | None:
    """A TS type as a Python annotation, or None when it has no counterpart in the subset."""
    if node is None:
        return None
    t = node.type
    if t == "parenthesized_type" and node.named_children:
        return lower_type(node.named_children[0], numeric)
    if t in ("predefined_type", "type_identifier"):
        name = text(node)
        if name in ("number", "bigint"):
            numeric.add(name)
            return _name("int")
        simple = {"boolean": "bool", "string": "str"}
        if name in simple:
            return _name(simple[name])
        if name in ("void", "undefined", "null"):
            return ast.Constant(None)
        return _name(name if name.isidentifier() else "object")
    if t == "literal_type":
        return ast.Constant(None) if text(node) in ("null", "undefined") else None
    if t == "array_type" and node.named_children:
        inner = lower_type(node.named_children[0], numeric)
        return _generic("list", [inner]) if inner is not None else None
    if t == "readonly_type" and node.named_children:
        return lower_type(node.named_children[-1], numeric)
    if t == "generic_type":
        name = text(node.child_by_field_name("name"))
        args = node.child_by_field_name("type_arguments")
        if name in ("Array", "ReadonlyArray") and args is not None and args.named_children:
            inner = lower_type(args.named_children[0], numeric)
            return _generic("list", [inner]) if inner is not None else None
        return None
    if t == "tuple_type":
        parts = [lower_type(c, numeric) for c in node.named_children]
        if any(p is None for p in parts):
            return None
        return _generic("tuple", [p for p in parts if p is not None])
    if t == "union_type":
        members = _union_members(node)
        rest = [m for m in members if text(m) not in ("null", "undefined")]
        if len(rest) == 1 and len(members) > 1:
            inner = lower_type(rest[0], numeric)
            return ast.BinOp(inner, ast.BitOr(), ast.Constant(None)) if inner is not None else None
        return None
    return None


def _union_members(node: Node) -> list[Node]:
    out = []
    for c in node.named_children:
        out += _union_members(c) if c.type == "union_type" else [c]
    return out


def _generic(name: str, args: list[ast.expr | None]) -> ast.expr:
    elts = [a for a in args if a is not None]
    sl: ast.expr = elts[0] if len(elts) == 1 else ast.Tuple(elts, ast.Load())
    return ast.Subscript(_name(name), sl, ast.Load())


# ---- functions --------------------------------------------------------------------------------


def lower_function(fn: JsFunction, module: JsModule) -> Lowered:
    return _Lowerer(fn, module).run()


class _Lowerer:
    def __init__(self, fn: JsFunction, module: JsModule) -> None:
        self.fn = fn
        self.module = module
        self.numeric: set[str] = set()
        self.locals: set[str] = {p.name for p in fn.params}
        self.globals: set[str] = set()

    def run(self) -> Lowered:
        fn = self.fn
        typed = True
        args: list[ast.arg] = []
        for p in fn.params:
            type_node = p.type
            if type_node is None and p.jsdoc_type:
                type_node = parse_type(p.jsdoc_type)
            ann = lower_type(type_node, self.numeric)
            typed = typed and ann is not None
            args.append(_at(ast.arg(p.name if p.name.isidentifier() else "_", ann), p.node))
        ret_node = fn.returns
        if ret_node is None and fn.jsdoc_returns:
            ret_node = parse_type(fn.jsdoc_returns)
        returns = lower_type(ret_node, self.numeric)
        typed = typed and returns is not None
        self._collect_locals(fn.body)
        head: list[ast.stmt] = []
        if fn.is_async:
            head.append(_at(ast.Expr(ast.Await(ast.Constant(None))), fn.node))  # concurrency
        if fn.is_generator:
            head.append(_at(ast.Expr(ast.Yield(None)), fn.node))
        if fn.rest or any(p.optional for p in fn.params):
            head.append(_stmt("optional, default or rest parameters", fn.node))
        if fn.body.type == "statement_block":
            body = self._block(fn.body, leading=True)
        else:  # concise arrow body
            body = [_at(ast.Return(self.expr(fn.body)), fn.body)]
        if self.globals:
            head.insert(0, _at(ast.Global(sorted(self.globals)), fn.node))
        node = ast.FunctionDef(
            name=fn.name,
            args=_arguments(args),
            body=head + (body or [ast.Pass()]),
            decorator_list=[],
            returns=returns,
            type_params=[],
        )
        return Lowered(node=_at(node, fn.node), numeric=self.numeric, typed=typed)

    def _collect_locals(self, node: Node) -> None:
        for n in walk(node):
            target = None
            if n.type == "variable_declarator":
                target = n.child_by_field_name("name")
            elif n.type == "for_in_statement":
                target = n.child_by_field_name("left")
            elif n.type in ("arrow_function", "function_expression"):
                target = n.child_by_field_name("parameters") or n.child_by_field_name("parameter")
            if target is not None:
                self.locals |= {
                    text(i)
                    for i in walk(target)
                    if i.type in ("identifier", "shorthand_property_identifier_pattern")
                }

    # ---- statements ---------------------------------------------------------------------------

    def _block(self, node: Node, leading: bool = False) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        stmts = [s for s in node.named_children if s.type != "comment"]
        guarding = leading
        for s in stmts:
            guarding = guarding and _is_guard(s)
            if guarding:  # leading guards are preconditions
                out.append(_at(ast.Assert(self._guard_condition(s), None), s))
            else:
                out += self.stmt(s)
        return out

    def _guard_condition(self, s: Node) -> ast.expr:
        if s.type == "if_statement":
            return _at(ast.UnaryOp(ast.Not(), self.expr(s.child_by_field_name("condition"))), s)
        call = s.named_children[0]
        args = call.child_by_field_name("arguments")
        return self.expr(args.named_children[0]) if args is not None else ast.Constant(True)

    def stmt(self, s: Node | None) -> list[ast.stmt]:
        if s is None:
            return []
        t = s.type
        if t == "statement_block":
            return self._block(s)
        if t == "empty_statement":
            return []
        if t in ("lexical_declaration", "variable_declaration"):
            return self._declaration(s)
        if t == "expression_statement":
            return self._expr_stmt(s.named_children[0]) if s.named_children else []
        if t == "return_statement":
            value = s.named_children[0] if s.named_children else None
            result = self.expr(value) if value is not None else ast.Constant(None)
            return [_at(ast.Return(result), s)]
        if t == "if_statement":
            alt = s.child_by_field_name("alternative")
            orelse = self.stmt(alt.named_children[0]) if alt and alt.named_children else []
            then = self.stmt(s.child_by_field_name("consequence")) or [ast.Pass()]
            return [_at(ast.If(self.expr(s.child_by_field_name("condition")), then, orelse), s)]
        if t == "for_in_statement":
            return [self._for_of(s)]
        if t == "for_statement":
            return [self._for_range(s)]
        if t in ("while_statement", "do_statement"):
            loop = self.stmt(s.child_by_field_name("body")) or [ast.Pass()]
            cond = self.expr(s.child_by_field_name("condition"))
            return [_at(ast.While(cond, loop, []), s)]
        if t == "break_statement":
            return [_at(ast.Break(), s)]
        if t == "continue_statement":
            return [_at(ast.Continue(), s)]
        if t == "throw_statement":
            return [_at(ast.Raise(_call("Exception", []), None), s)]
        if t == "try_statement":
            return [_stmt("try / catch", s)]
        if t == "switch_statement":
            return [_stmt("switch", s)]
        if t in ("function_declaration", "class_declaration"):
            return [_stmt(f"a nested {t.split('_')[0]}", s)]
        return [_stmt(f"the statement `{text(s)[:40]}`", s)]

    def _declaration(self, s: Node) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for d in s.named_children:
            if d.type != "variable_declarator":
                continue
            value = d.child_by_field_name("value")
            if value is None:
                out.append(_stmt("a declaration without a value", d))
                continue
            target = self._target(d.child_by_field_name("name"))
            ann_node = d.child_by_field_name("type")
            ann = None
            if isinstance(target, ast.Name) and ann_node is not None and ann_node.named_children:
                ann = lower_type(ann_node.named_children[0], self.numeric)
            if isinstance(target, ast.Name) and ann is not None:
                out.append(_at(ast.AnnAssign(target, ann, self.expr(value), 1), d))
            else:
                out.append(_at(ast.Assign([target], self.expr(value)), d))
        return out

    def _target(self, node: Node | None) -> ast.expr:
        if node is None:
            return ast.Name("_", ast.Store())
        if node.type == "identifier":
            name = text(node)
            if name not in self.locals:
                self.globals.add(name)
            return _at(ast.Name(name, ast.Store()), node)
        if node.type == "array_pattern":
            elts = [self._target(c) for c in node.named_children]
            return _at(ast.Tuple(elts, ast.Store()), node)
        if node.type == "member_expression":
            obj = self.expr(node.child_by_field_name("object"))
            prop = text(node.child_by_field_name("property"))
            return _at(ast.Attribute(obj, prop, ast.Store()), node)
        if node.type == "subscript_expression":
            obj = self.expr(node.child_by_field_name("object"))
            idx = self.expr(node.child_by_field_name("index"))
            return _at(ast.Subscript(obj, idx, ast.Store()), node)
        return unsupported(f"assigning to `{text(node)}`", node)

    def _expr_stmt(self, e: Node) -> list[ast.stmt]:
        e = _unparen(e)
        t = e.type
        if t == "assignment_expression":
            target = self._target(e.child_by_field_name("left"))
            return [_at(ast.Assign([target], self.expr(e.child_by_field_name("right"))), e)]
        if t == "augmented_assignment_expression":
            target = self._target(e.child_by_field_name("left"))
            right = self.expr(e.child_by_field_name("right"))
            op = text(e.child_by_field_name("operator"))
            ops: dict[str, ast.operator] = {
                "+=": ast.Add(),
                "-=": ast.Sub(),
                "*=": ast.Mult(),
                "**=": ast.Pow(),
            }
            if op in ops and isinstance(target, ast.Name | ast.Attribute | ast.Subscript):
                return [_at(ast.AugAssign(target, ops[op], right), e)]
            if op == "%=" and isinstance(target, ast.Name):
                value = _call(TMOD, [_name(target.id), right])
                return [_at(ast.Assign([target], value), e)]
            return [_stmt(f"the operator `{op}`", e)]
        if t == "update_expression":
            target = self._target(e.child_by_field_name("argument"))
            op = ast.Add() if "++" in text(e) else ast.Sub()
            if isinstance(target, ast.Name | ast.Attribute | ast.Subscript):
                return [_at(ast.AugAssign(target, op, ast.Constant(1)), e)]
            return [_stmt(f"`{text(e)}`", e)]
        return [_at(ast.Expr(self.expr(e)), e)]

    def _for_of(self, s: Node) -> ast.stmt:
        if text(s.child_by_field_name("operator")) != "of":
            return _stmt("`for ... in` (iterates keys)", s)
        left = s.child_by_field_name("left")
        if left is not None and left.type in ("lexical_declaration", "variable_declaration"):
            decl = left.named_children[0] if left.named_children else None
            left = decl.child_by_field_name("name") if decl is not None else None
        loop = self.stmt(s.child_by_field_name("body")) or [ast.Pass()]
        it = self.expr(s.child_by_field_name("right"))
        return _at(ast.For(self._target(left), it, loop, []), s)

    def _for_range(self, s: Node) -> ast.stmt:
        """Only the canonical `for (let i = a; i < b; i++)` (or `i <= b`) is a range loop."""
        init = s.child_by_field_name("initializer")
        cond = s.child_by_field_name("condition")
        inc = s.child_by_field_name("increment")
        body = s.child_by_field_name("body")
        decl = init.named_children[0] if init is not None and init.named_children else None
        var = text(decl.child_by_field_name("name")) if decl is not None else ""
        start = decl.child_by_field_name("value") if decl is not None else None
        cond_e = cond
        if cond is not None and cond.type == "expression_statement" and cond.named_children:
            cond_e = cond.named_children[0]
        cond_e = _unparen(cond_e) if cond_e is not None else None
        ok = (
            var.isidentifier()
            and start is not None
            and cond_e is not None
            and cond_e.type == "binary_expression"
            and text(cond_e.child_by_field_name("left")) == var
            and text(cond_e.child_by_field_name("operator")) in ("<", "<=")
            and text(inc).replace(" ", "") in (f"{var}++", f"++{var}", f"{var}+=1")
            and body is not None
            and not _assigns(body, var)
        )
        if not ok or cond_e is None or start is None:
            # Not a counted loop: model it as the while loop it is.
            return _at(ast.While(ast.Constant(True), [_stmt("a general `for(;;)` loop", s)], []), s)
        stop = self.expr(cond_e.child_by_field_name("right"))
        if text(cond_e.child_by_field_name("operator")) == "<=":
            stop = ast.BinOp(stop, ast.Add(), ast.Constant(1))
        rng = _call("range", [self.expr(start), stop])
        loop = self.stmt(body) or [ast.Pass()]
        return _at(ast.For(ast.Name(var, ast.Store()), rng, loop, []), s)

    # ---- expressions --------------------------------------------------------------------------

    def expr(self, e: Node | None) -> ast.expr:
        if e is None:
            return ast.Constant(None)
        e = _unparen(e)
        return _at(self._expr(e, e.type), e)

    def _expr(self, e: Node, t: str) -> ast.expr:
        if t == "number":
            return _number(text(e))
        if t in ("true", "false"):
            return ast.Constant(t == "true")
        if t in ("null", "undefined"):
            return ast.Constant(None)
        if t == "identifier":
            name = text(e)
            if name == "undefined":
                return ast.Constant(None)
            if name in ("Infinity", "NaN"):
                return ast.Constant(float("inf") if name == "Infinity" else float("nan"))
            return _name(name)
        if t in ("string", "template_string"):
            return ast.Constant(text(e)[1:-1])
        if t == "this":
            return unsupported("`this`", e)
        if t == "array":
            return self._array(e)
        if t == "unary_expression":
            op = text(e.child_by_field_name("operator"))
            arg = self.expr(e.child_by_field_name("argument"))
            if op == "-":
                return ast.UnaryOp(ast.USub(), arg)
            if op == "!":
                return ast.UnaryOp(ast.Not(), arg)
            return unsupported(f"the operator `{op}`", e, keep=[arg])
        if t == "binary_expression":
            return self._binary(e)
        if t == "ternary_expression":
            return ast.IfExp(
                self.expr(e.child_by_field_name("condition")),
                self.expr(e.child_by_field_name("consequence")),
                self.expr(e.child_by_field_name("alternative")),
            )
        if t == "member_expression":
            obj = e.child_by_field_name("object")
            prop = text(e.child_by_field_name("property"))
            if prop == "length":
                return _call("len", [self.expr(obj)])
            if text(obj) in ("Number", "Math"):
                return unsupported(f"`{text(e)}`", e)
            return ast.Attribute(self.expr(obj), prop, ast.Load())
        if t == "subscript_expression":
            obj = self.expr(e.child_by_field_name("object"))
            return ast.Subscript(obj, self.expr(e.child_by_field_name("index")), ast.Load())
        if t == "call_expression":
            return self._call_expr(e)
        if t == "new_expression":
            ctor = text(e.child_by_field_name("constructor"))
            if ctor == "Date":
                return _method(_name("time"), "time", [])
            if ctor in _TIMERS:
                return _method(_name("asyncio"), "sleep", [])
            return unsupported(f"`new {ctor}`", e)
        if t == "await_expression":
            return ast.Await(self.expr(e.named_children[0]))
        if t in ("as_expression", "non_null_expression", "satisfies_expression"):
            return self.expr(e.named_children[0])
        if t in ("arrow_function", "function_expression"):
            return unsupported("a function value outside map/filter/reduce/some/every", e)
        return unsupported(f"`{text(e)[:40]}`", e)

    def _array(self, e: Node) -> ast.expr:
        parts: list[ast.expr] = []
        current: list[ast.expr] = []
        for c in e.named_children:
            if c.type == "spread_element":
                if current:
                    parts.append(ast.List(current, ast.Load()))
                    current = []
                parts.append(self.expr(c.named_children[0]))
            elif c.type != "comment":
                current.append(self.expr(c))
        if current or not parts:
            parts.append(ast.List(current, ast.Load()))
        out = parts[0]
        for p in parts[1:]:
            out = ast.BinOp(out, ast.Add(), p)
        return out

    def _binary(self, e: Node) -> ast.expr:
        op = text(e.child_by_field_name("operator"))
        left = self.expr(e.child_by_field_name("left"))
        right = self.expr(e.child_by_field_name("right"))
        arith: dict[str, ast.operator] = {
            "+": ast.Add(),
            "-": ast.Sub(),
            "*": ast.Mult(),
            "**": ast.Pow(),
            "/": ast.Div(),
        }
        if op in arith:
            return ast.BinOp(left, arith[op], right)
        if op == "%":
            return _call(TMOD, [left, right])
        cmp: dict[str, ast.cmpop] = {"<": ast.Lt(), "<=": ast.LtE(), ">": ast.Gt(), ">=": ast.GtE()}
        if op in cmp:
            return ast.Compare(left, [cmp[op]], [right])
        if op in ("===", "!=="):
            for a, b in ((left, right), (right, left)):
                if isinstance(b, ast.Constant) and b.value is None:
                    return ast.Compare(a, [ast.Is() if op == "===" else ast.IsNot()], [b])
            return ast.Compare(left, [ast.Eq() if op == "===" else ast.NotEq()], [right])
        if op in ("&&", "||"):
            return ast.BoolOp(ast.And() if op == "&&" else ast.Or(), [left, right])
        both = [left, right]
        if op in ("==", "!="):
            what = f"loose equality `{op}` (it coerces types; use `{op}=`)"
            return unsupported(what, e, keep=both)
        if op in ("&", "|", "^", "<<", ">>", ">>>"):
            return unsupported(f"the bitwise operator `{op}` (32-bit in JavaScript)", e, keep=both)
        return unsupported(f"the operator `{op}`", e, keep=both)

    def _lambda(self, fn: Node, arity: int) -> tuple[list[str], Node] | None:
        """(parameter names, body expression) of a concise arrow function, or None."""
        if fn.type != "arrow_function":
            return None
        params = _arrow_params(fn)
        body = fn.child_by_field_name("body")
        if body is not None and body.type == "statement_block":
            stmts = [s for s in body.named_children if s.type != "comment"]
            ok = len(stmts) == 1 and stmts[0].type == "return_statement"
            body = stmts[0].named_children[0] if ok and stmts[0].named_children else None
        if body is None or len(params) > arity or not all(p.isidentifier() for p in params):
            return None
        return params, body

    def _call_expr(self, e: Node) -> ast.expr:
        func = e.child_by_field_name("function")
        args_node = e.child_by_field_name("arguments")
        children = args_node.named_children if args_node is not None else []
        argn = [a for a in children if a.type != "comment"]
        args = [self.expr(a) for a in argn]
        if func is None:
            return unsupported(f"`{text(e)[:40]}`", e)
        if func.type == "identifier":
            name = text(func)
            if name in _TIMERS:
                return _method(_name("asyncio"), "sleep", [])
            if name in _IO_OBJECTS or name == "require" or name in self.module.imports:
                return _method(_name("os"), "system", [])
            if name in ("Number", "String", "Boolean", "parseInt", "parseFloat"):
                return unsupported(f"`{name}(...)` (type conversion)", e, keep=args)
            return _call(name, args)
        if func.type != "member_expression":
            return unsupported(f"calling `{text(func)[:30]}`", e, keep=args)
        obj = func.child_by_field_name("object")
        prop = text(func.child_by_field_name("property"))
        objname = text(obj)
        if objname == "Math":
            return self._math(prop, argn, args, e)
        if objname == "console":
            return _call("print", args)
        if objname in ("Date", "performance"):
            return _method(_name("time"), "time", [])
        if objname in _IO_OBJECTS or objname in self.module.imports:
            return _method(_name("os"), "system", [])
        if objname in ("Promise", "Atomics"):
            return _method(_name("asyncio"), "sleep", [])
        target = self.expr(obj)
        if prop == "push" and len(args) == 1:
            return _method(target, "append", args)
        if prop in _MUTATORS:
            return _method(target, _MUTATORS[prop], args)
        if prop == "includes" and len(args) == 1:
            return ast.Compare(args[0], [ast.In()], [target])
        if prop == "concat" and len(args) == 1:
            return ast.BinOp(target, ast.Add(), args[0])
        if prop == "slice" and len(argn) <= 2:
            lo = args[0] if args else None
            hi = args[1] if len(args) > 1 else None
            return ast.Subscript(target, ast.Slice(lo, hi, None), ast.Load())
        if prop in ("map", "filter", "some", "every") and len(argn) == 1:
            return self._comprehension(prop, target, argn[0], e)
        if prop == "reduce" and len(argn) == 2:
            return self._reduce(target, argn[0], args[1], e)
        return _method(target, prop, args)

    def _math(self, prop: str, argn: list[Node], args: list[ast.expr], e: Node) -> ast.expr:
        if prop in ("max", "min") and len(args) == 2:
            return _call(prop, args)
        if prop == "abs" and len(args) == 1:
            return _call("abs", args)
        if prop in ("floor", "trunc") and len(argn) == 1:
            inner = _unparen(argn[0])
            if (
                inner.type == "binary_expression"
                and text(inner.child_by_field_name("operator")) == "/"
            ):
                a = self.expr(inner.child_by_field_name("left"))
                b = self.expr(inner.child_by_field_name("right"))
                return ast.BinOp(a, ast.FloorDiv(), b) if prop == "floor" else _call(TDIV, [a, b])
            return unsupported(f"`Math.{prop}` of a non-quotient", e, "FLOAT_SEMANTICS", args)
        if prop == "random":
            return _method(_name("random"), "random", [])
        return unsupported(f"`Math.{prop}`", e, "FLOAT_SEMANTICS", args)

    def _comprehension(self, prop: str, target: ast.expr, fn: Node, e: Node) -> ast.expr:
        lam = self._lambda(fn, 1)
        if lam is None:
            return unsupported(f"`{prop}` with a callback other than `x => expression`", e)
        params, body = lam
        var = params[0] if params else "_"
        gen = ast.comprehension(ast.Name(var, ast.Store()), target, [], 0)
        value = self.expr(body)
        if prop == "map":
            return ast.ListComp(value, [gen])
        if prop == "filter":
            gen.ifs = [value]
            return ast.ListComp(_name(var), [gen])
        return _call("any" if prop == "some" else "all", [ast.GeneratorExp(value, [gen])])

    def _reduce(self, target: ast.expr, fn: Node, init: ast.expr, e: Node) -> ast.expr:
        lam = self._lambda(fn, 2)
        if lam is None or len(lam[0]) != 2:
            return unsupported("`reduce` with a callback other than `(acc, x) => expression`", e)
        (acc, var), body = lam
        summing = text(_unparen(body)).replace(" ", "") in (f"{acc}+{var}", f"{var}+{acc}")
        if summing and isinstance(init, ast.Constant) and init.value == 0:
            return _call("sum", [target])
        lam_ast = ast.Lambda(_arguments([ast.arg(acc), ast.arg(var)]), self.expr(body))
        return _call(REDUCE, [lam_ast, target, init])


def _number(raw: str) -> ast.expr:
    raw = raw.replace("_", "")
    if raw.endswith("n"):
        return ast.Constant(int(raw[:-1], 0))
    try:
        return ast.Constant(int(raw, 0))
    except ValueError:
        return ast.Constant(float(raw))


def _arrow_params(fn: Node) -> list[str]:
    single = fn.child_by_field_name("parameter")
    if single is not None:
        return [text(single)]
    plist = fn.child_by_field_name("parameters")
    names = []
    for p in plist.named_children if plist is not None else []:
        pattern = p.child_by_field_name("pattern") if p.type.endswith("_parameter") else p
        names.append(text(pattern))
    return names


def _unparen(node: Node) -> Node:
    while node.type == "parenthesized_expression" and node.named_children:
        node = node.named_children[0]
    return node


def _is_guard(s: Node) -> bool:
    """`if (cond) throw ...;` or `assert(cond)` / `assert.ok(cond)` from node:assert.
    (`console.assert` only logs and does not stop the function, so it is not a guard.)"""
    if s.type == "if_statement" and s.child_by_field_name("alternative") is None:
        cons = s.child_by_field_name("consequence")
        if cons is not None and cons.type == "statement_block":
            inner = [c for c in cons.named_children if c.type != "comment"]
            cons = inner[0] if len(inner) == 1 else None
        return cons is not None and cons.type == "throw_statement"
    if s.type == "expression_statement" and s.named_children:
        call = s.named_children[0]
        if call.type == "call_expression":
            return text(call.child_by_field_name("function")) in ("assert", "assert.ok")
    return False


def _assigns(body: Node, var: str) -> bool:
    for n in walk(body):
        assigns = n.type in ("assignment_expression", "augmented_assignment_expression")
        if assigns and text(n.child_by_field_name("left")) == var:
            return True
        if n.type == "update_expression" and text(n.child_by_field_name("argument")) == var:
            return True
    return False


def lower_module(module: JsModule) -> tuple[ast.Module, dict[str, Lowered]]:
    """Every top-level function lowered, in a synthetic module whose imports let the purity
    classifier recognize JS's impure APIs (see the module docstring)."""
    lowered = {name: lower_function(fn, module) for name, fn in module.functions.items()}
    body: list[ast.stmt] = [ast.Import([ast.alias(m)]) for m in _IMPURE_IMPORTS]
    body += [lw.node for lw in lowered.values()]
    tree = ast.Module(body=body, type_ignores=[])
    for n in ast.walk(tree):
        if isinstance(n, ast.stmt | ast.expr) and getattr(n, "lineno", None) is None:
            n.lineno, n.col_offset = 1, 0
            n.end_lineno, n.end_col_offset = 1, 0
    return tree, lowered
