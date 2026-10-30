"""Python -> Lean model translation for the MVP subset.

Supported: integer arithmetic (with Python's floor `//` and `%`), comparisons, booleans,
`if`/`elif`/`else`, conditional expressions, lists, fixed-size tuples, `Optional` returns,
calls between module functions, simple `for` loops (rewritten as `foldl`), list
comprehensions, constant slices, and `len`/`abs`/`min`/`max`/`sum`. Recursion is translated, but
Lean's automatic termination check rejects it in practice (`n - 1` on an Int, `xs[1:]`), which
surfaces as NONTERMINATION.

Leading `assert` statements are preconditions, not code: the model leaves them out, theorems
drafted from the Intent Model assume them, and the fidelity check only compares inputs that pass
them. That is partial-correctness semantics: where Python raises on a failed precondition there is
no result for a theorem to be wrong about.

Everything else raises `TranslationError` with `UNSUPPORTED_CONSTRUCT` (or a more specific reason
code). The translator never guesses: a construct it cannot model faithfully is a gap, not a
best-effort approximation, because a proof about the wrong model is worse than no proof.
"""

import ast
from collections.abc import Callable
from dataclasses import dataclass

from apex.analyze.target import FunctionInfo
from apex.analyze.types import BOOL, INT, PyType, UnsupportedType, list_of, parse_annotation
from apex.core.verdict import ReasonCode

UNSUPPORTED_HOOK = "__apex_unsupported__"
"""`__apex_unsupported__("what", "REASON")`: a frontend (the JS lowering) marking a construct it
cannot express in the subset; translating it raises a gap with that reason."""
TMOD_HOOK = "__apex_tmod__"
"""Truncated remainder (JavaScript `%`): Lean `Int.tmod`."""
TDIV_HOOK = "__apex_tdiv__"
"""Truncated quotient (JavaScript `Math.trunc(a / b)`): Lean `Int.tdiv`."""
REDUCE_HOOK = "__apex_reduce__"
"""`__apex_reduce__(lambda acc, x: body, xs, init)` (JavaScript `reduce`): `List.foldl`."""

LEAN_KEYWORDS = frozenset(
    {
        "at",
        "by",
        "do",
        "else",
        "end",
        "fun",
        "have",
        "if",
        "in",
        "let",
        "match",
        "then",
        "with",
        "where",
        "from",
        "show",
        "open",
        "namespace",
        "section",
        "theorem",
        "lemma",
        "def",
        "example",
        "structure",
        "class",
        "instance",
        "import",
        "deriving",
        "for",
        "unless",
        "return",
        "mut",
        "try",
        "catch",
        "finally",
        "break",
        "continue",
        "variable",
        "universe",
        "axiom",
        "macro",
        "syntax",
        "sorry",
        "calc",
        "nomatch",
        "nofun",
        "termination_by",
        "decreasing_by",
        "mutual",
        "abbrev",
        "private",
        "protected",
        "noncomputable",
        "partial",
        "unsafe",
        "set_option",
        "attribute",
        "local",
        "scoped",
        "notation",
        "prefix",
        "infix",
        "infixl",
        "infixr",
        "postfix",
        "Type",
        "Prop",
        "Sort",
        "forall",
        "exists",
        "inductive",
        "opaque",
        "extends",
        "this",
        "suffices",
        "obtain",
        "rfl",
        "omega",
        "simp",
        "decide",
        # Found by compiling `def <name>` for every Python keyword, builtin and common name
        # against the pinned toolchain, with and without Mathlib (`repeat` used to break the
        # model of `def repeat(...)`).
        "elab",
        "elab_rules",
        "export",
        "hiding",
        "include",
        "initialize",
        "macro_rules",
        "nonrec",
        "omit",
        "renaming",
        "repeat",
        "says",  # Mathlib's `says` combinator: reserved only once Mathlib is imported
        "show_term",
        "using",
        "while",
    }
)

_CMP_SYMBOLS: dict[type, str] = {
    ast.Eq: "=",
    ast.NotEq: "≠",
    ast.Lt: "<",
    ast.LtE: "≤",
    ast.Gt: ">",
    ast.GtE: "≥",
    ast.In: "∈",
    ast.NotIn: "∉",
}

UNKNOWN = PyType("unknown")
NONE = PyType("none")


class TranslationError(Exception):
    def __init__(
        self,
        message: str,
        node: ast.AST | None = None,
        reason_code: ReasonCode = ReasonCode.UNSUPPORTED_CONSTRUCT,
    ) -> None:
        self.line = getattr(node, "lineno", None)
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{message}" + (f" (line {self.line})" if self.line else ""))


@dataclass
class FunctionSig:
    params: list[tuple[str, PyType]]
    returns: PyType


@dataclass
class LeanModel:
    namespace: str
    text: str
    """All model definitions, dependencies first, without the surrounding namespace."""
    def_names: list[str]
    sigs: dict[str, FunctionSig]

    @property
    def target_sig(self) -> FunctionSig:
        # `sigs` is keyed by Python name in definition order, target last; `def_names` holds Lean
        # identifiers, which differ for escaped names such as «repeat».
        return list(self.sigs.values())[-1]


def lean_ident(name: str) -> str:
    return f"«{name}»" if name in LEAN_KEYWORDS else name


def lean_namespace(module: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in module) or "model"


def _indent(text: str, n: int = 2) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _int_literal(v: int) -> str:
    return f"({v} : Int)"


def signature(fn: ast.FunctionDef) -> FunctionSig:
    a = fn.args
    if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
        raise TranslationError("only plain positional parameters are supported", fn)
    try:
        params = [(p.arg, parse_annotation(p.annotation)) for p in a.args]
        returns = parse_annotation(fn.returns)
    except UnsupportedType as e:
        code = ReasonCode.FLOAT_SEMANTICS if "float" in str(e) else ReasonCode.UNSUPPORTED_CONSTRUCT
        raise TranslationError(f"{fn.name}: {e}", fn, code) from e
    return FunctionSig(params, returns)


def _body_without_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    stmts = list(fn.body)
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    return stmts


def leading_asserts(fn: ast.FunctionDef) -> list[ast.Assert]:
    """`assert` statements before any other code: the function's stated preconditions."""
    out = []
    for s in _body_without_docstring(fn):
        if not isinstance(s, ast.Assert):
            break
        out.append(s)
    return out


def _contains(nodes: list[ast.stmt], *types: type) -> bool:
    return any(isinstance(n, types) for stmt in nodes for n in ast.walk(stmt))


def _assigned_names(nodes: list[ast.stmt]) -> list[str]:
    out: list[str] = []
    for stmt in nodes:
        for n in ast.walk(stmt):
            targets: list[ast.expr] = []
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, ast.AugAssign | ast.AnnAssign):
                targets = [n.target]
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                f = n.value.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    targets = [f.value]  # xs.append(...) rebinds xs
            for t in targets:
                for name in ast.walk(t):
                    if isinstance(name, ast.Name) and name.id not in out:
                        out.append(name.id)
    return out


class _FunctionTranslator:
    def __init__(self, fn: ast.FunctionDef, sigs: dict[str, FunctionSig], js: bool = False) -> None:
        self.fn = fn
        self.sigs = sigs
        self.sig = sigs[fn.name]
        self.js = js
        """Lowered from JavaScript: `==` on arrays would be identity there, so it is refused."""

    # ---- types -------------------------------------------------------------------------------

    @staticmethod
    def _unify(a: PyType, b: PyType, node: ast.AST) -> PyType:
        if a == b or b == UNKNOWN:
            return a
        if a == UNKNOWN:
            return b
        if a.kind == b.kind == "list":
            return list_of(_FunctionTranslator._unify(a.args[0], b.args[0], node))
        raise TranslationError(f"type mismatch: {a.python()} vs {b.python()}", node)

    def _coerce(self, text: str, actual: PyType, expected: PyType | None, node: ast.AST) -> str:
        if expected is None or expected == actual:
            return text
        if expected.kind == "option":
            if actual == NONE:
                return "none"
            if actual.kind != "option":
                self._unify(expected.args[0], actual, node)
                return f"(some {text})"
            return text
        self._unify(expected, actual, node)
        return text

    # ---- expressions -------------------------------------------------------------------------

    def expr(
        self, node: ast.expr, env: dict[str, PyType], expected: PyType | None = None
    ) -> tuple[str, PyType]:
        match node:
            case ast.Constant(value=bool() as b):
                return ("true" if b else "false"), BOOL
            case ast.Constant(value=int() as v):
                return _int_literal(v), INT
            case ast.Constant(value=None):
                if expected is None or expected.kind != "option":
                    raise TranslationError("`None` outside an Optional context", node)
                return "none", expected
            case ast.Constant(value=float()):
                raise TranslationError("float literal", node, ReasonCode.FLOAT_SEMANTICS)
            case ast.Constant():
                raise TranslationError(f"literal {node.value!r} is not in the MVP subset", node)
            case ast.Name(id=name):
                if name not in env:
                    raise TranslationError(
                        f"`{name}` is not a local or parameter (globals are not modelled)", node
                    )
                return lean_ident(name), env[name]
            case ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=int() as v)) if (
                not isinstance(v, bool)
            ):
                return _int_literal(-v), INT
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                t, ty = self.expr(operand, env, INT)
                self._unify(INT, ty, node)
                return f"(-{t})", INT
            case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                return self.expr(operand, env, INT)
            case ast.UnaryOp(op=ast.Not()) | ast.Compare() | ast.BoolOp():
                return f"(decide {self.prop(node, env)})", BOOL
            case ast.Call(func=ast.Name(id="all" | "any"), args=[ast.GeneratorExp()]):
                return f"(decide {self.prop(node, env)})", BOOL
            case ast.BinOp():
                return self._binop(node, env, expected)
            case ast.IfExp(test=test, body=body, orelse=orelse):
                c = self.prop(test, env)
                a, ta = self.expr(body, env, expected)
                b, tb = self.expr(orelse, env, expected or ta)
                ty = self._unify(ta, tb, node) if expected is None else expected
                a = self._coerce(a, ta, ty, body)
                b = self._coerce(b, tb, ty, orelse)
                return f"(if {c} then {a} else {b})", ty
            case ast.List(elts=elts):
                elem_expected = expected.args[0] if expected and expected.kind == "list" else None
                parts, ety = [], elem_expected or UNKNOWN
                for e in elts:
                    t, ty = self.expr(e, env, elem_expected)
                    ety = self._unify(ety, ty, e)
                    parts.append(t)
                return "[" + ", ".join(parts) + "]", list_of(ety)
            case ast.Tuple(elts=elts):
                exp_args = expected.args if expected and expected.kind == "tuple" else None
                parts, tys = [], []
                for k, e in enumerate(elts):
                    t, ty = self.expr(e, env, exp_args[k] if exp_args else None)
                    parts.append(t)
                    tys.append(ty)
                return "(" + ", ".join(parts) + ")", PyType("tuple", tuple(tys))
            case ast.Subscript():
                return self._subscript(node, env)
            case ast.Call():
                return self._call(node, env)
            case ast.ListComp(elt=elt, generators=[gen]):
                return self._listcomp(elt, gen, env)
        raise TranslationError(f"expression `{ast.unparse(node)}` is not in the MVP subset", node)

    def _binop(
        self, node: ast.BinOp, env: dict[str, PyType], expected: PyType | None
    ) -> tuple[str, PyType]:
        op = node.op
        if isinstance(op, ast.Div):
            raise TranslationError(
                "true division `/` yields a float", node, ReasonCode.FLOAT_SEMANTICS
            )
        if isinstance(op, ast.Pow):
            base, tb = self.expr(node.left, env, INT)
            self._unify(INT, tb, node)
            match node.right:
                case ast.Constant(value=int() as k) if not isinstance(k, bool) and k >= 0:
                    return f"({base} ^ ({k} : Nat))", INT
            raise TranslationError(
                "`**` needs a non-negative integer literal exponent "
                "(otherwise Python may return a float)",
                node,
            )
        left, tl = self.expr(node.left, env, expected)
        right, tr = self.expr(node.right, env, tl if tl != UNKNOWN else expected)
        if tl.kind == "list" or tr.kind == "list":
            if isinstance(op, ast.Add):
                return f"({left} ++ {right})", self._unify(tl, tr, node)
            raise TranslationError("only `+` is supported on lists", node)
        self._unify(INT, tl, node.left)
        self._unify(INT, tr, node.right)
        match op:
            case ast.Add():
                return f"({left} + {right})", INT
            case ast.Sub():
                return f"({left} - {right})", INT
            case ast.Mult():
                return f"({left} * {right})", INT
            case ast.FloorDiv():
                return f"(Apex.pyFloorDiv {left} {right})", INT
            case ast.Mod():
                return f"(Apex.pyMod {left} {right})", INT
        raise TranslationError(f"operator `{type(op).__name__}` is not in the MVP subset", node)

    def _subscript(self, node: ast.Subscript, env: dict[str, PyType]) -> tuple[str, PyType]:
        base, ty = self.expr(node.value, env)
        if ty.kind == "tuple":
            match node.slice:
                case ast.Constant(value=int() as k) if 0 <= k < len(ty.args):
                    # Right-nested products: t[0] = t.1, t[1] = t.2.1, last = t.2.2...2
                    path = ".2" * k + (".1" if k < len(ty.args) - 1 else "")
                    return f"({base}{path})", ty.args[k]
            raise TranslationError("tuple index must be a constant within range", node)
        if ty.kind == "list" and isinstance(node.slice, ast.Slice):
            s = node.slice
            if s.step is not None:
                raise TranslationError("slices with a step are not supported", node)
            out = base
            lower = self._const_nonneg(s.lower) if s.lower is not None else None
            upper = self._const_nonneg(s.upper) if s.upper is not None else None
            if upper is not None:
                out = f"({out}.take {upper})"
            if lower is not None:
                out = f"({out}.drop {lower})"
            return out, ty
        raise TranslationError(
            "list indexing can raise IndexError, which the MVP does not model; iterate with a "
            "for loop, or check bounds and return None",
            node,
        )

    @staticmethod
    def _const_nonneg(e: ast.expr) -> int:
        if isinstance(e, ast.Constant) and isinstance(e.value, int) and e.value >= 0:
            return e.value
        raise TranslationError("slice bounds must be non-negative integer literals", e)

    def _call(self, node: ast.Call, env: dict[str, PyType]) -> tuple[str, PyType]:
        if node.keywords:
            raise TranslationError("keyword arguments are not supported", node)
        func, args = node.func, node.args
        if isinstance(func, ast.Name):
            name = func.id
            if name == UNSUPPORTED_HOOK:
                consts = [a.value for a in args if isinstance(a, ast.Constant)]
                what = consts[0] if consts else "?"
                reason = consts[1] if len(consts) > 1 else ""
                code = ReasonCode(reason) if reason in ReasonCode.__members__ else None
                raise TranslationError(
                    f"{what} is not in the subset", node, code or ReasonCode.UNSUPPORTED_CONSTRUCT
                )
            if name in (TMOD_HOOK, TDIV_HOOK) and len(args) == 2:
                a, ta = self.expr(args[0], env, INT)
                b, tb = self.expr(args[1], env, INT)
                self._unify(INT, ta, node)
                self._unify(INT, tb, node)
                return f"(Int.{'tmod' if name == TMOD_HOOK else 'tdiv'} {a} {b})", INT
            if name == REDUCE_HOOK and len(args) == 3 and isinstance(args[0], ast.Lambda):
                return self._reduce(args[0], args[1], args[2], env, node)
            if name in self.sigs:
                sig = self.sigs[name]
                if len(args) != len(sig.params):
                    raise TranslationError(
                        f"`{name}` called with {len(args)} arguments, expects {len(sig.params)}",
                        node,
                    )
                parts = []
                for arg, (_, pty) in zip(args, sig.params, strict=True):
                    t, ty = self.expr(arg, env, pty)
                    parts.append(self._coerce(t, ty, pty, arg))
                return f"({lean_ident(name)} {' '.join(parts)})", sig.returns
            if name == "len" and len(args) == 1:
                t, ty = self.expr(args[0], env)
                if ty.kind != "list":
                    raise TranslationError("len() is only supported on lists", node)
                return f"({t}.length : Int)", INT
            if name == "abs" and len(args) == 1:
                t, ty = self.expr(args[0], env, INT)
                self._unify(INT, ty, node)
                return f"(Apex.pyAbs {t})", INT
            if name in ("min", "max") and len(args) == 2:
                a, ta = self.expr(args[0], env, INT)
                b, tb = self.expr(args[1], env, INT)
                self._unify(INT, ta, node)
                self._unify(INT, tb, node)
                return f"({name} {a} {b})", INT
            if name == "sum" and len(args) == 1:
                t, ty = self.expr(args[0], env, list_of(INT))
                self._unify(list_of(INT), ty, node)
                return f"(Apex.pySum {t})", INT
            if name in ("min", "max"):
                raise TranslationError(
                    f"{name}() of a sequence raises on empty input, which the MVP does not model",
                    node,
                )
            raise TranslationError(f"call to `{name}` is not in the MVP subset", node)
        raise TranslationError(f"call `{ast.unparse(func)}` is not in the MVP subset", node)

    def _reduce(
        self,
        fn: ast.Lambda,
        xs: ast.expr,
        init: ast.expr,
        env: dict[str, PyType],
        node: ast.AST,
    ) -> tuple[str, PyType]:
        names = [a.arg for a in fn.args.args]
        if len(names) != 2:
            raise TranslationError("reduce needs a two-argument callback", node)
        t_xs, ty_xs = self.expr(xs, env)
        if ty_xs.kind != "list":
            raise TranslationError("reduce is only supported on lists", node)
        t_init, ty_init = self.expr(init, env)
        inner = {**env, names[0]: ty_init, names[1]: ty_xs.args[0]}
        t_body, ty_body = self.expr(fn.body, inner, ty_init)
        ty = self._unify(ty_init, ty_body, node)
        acc, x = (lean_ident(n) for n in names)
        return f"({t_xs}.foldl (fun {acc} {x} => {t_body}) {t_init})", ty

    def _iterable(self, it: ast.expr, env: dict[str, PyType]) -> tuple[str, PyType]:
        """Returns the Lean list and its element type."""
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
            if it.keywords or not 1 <= len(it.args) <= 2:
                raise TranslationError("only range(stop) and range(start, stop) are supported", it)
            bounds = []
            for a in it.args:
                t, ty = self.expr(a, env, INT)
                self._unify(INT, ty, a)
                bounds.append(t)
            lo, hi = (_int_literal(0), bounds[0]) if len(bounds) == 1 else bounds
            return f"(Apex.pyRange {lo} {hi})", INT
        t, ty = self.expr(it, env)
        if ty.kind != "list":
            raise TranslationError("can only iterate over lists and range()", it)
        return t, ty.args[0]

    def _listcomp(
        self, elt: ast.expr, gen: ast.comprehension, env: dict[str, PyType]
    ) -> tuple[str, PyType]:
        if gen.is_async or not isinstance(gen.target, ast.Name):
            raise TranslationError("comprehension target must be a single name", gen.target)
        src, ety = self._iterable(gen.iter, env)
        inner = {**env, gen.target.id: ety}
        x = lean_ident(gen.target.id)
        for cond in gen.ifs:
            src = f"({src}.filter (fun {x} => decide {self.prop(cond, inner)}))"
        body, bty = self.expr(elt, inner)
        return f"({src}.map (fun {x} => {body}))", list_of(bty)

    # ---- conditions (Props) ------------------------------------------------------------------

    def prop(self, node: ast.expr, env: dict[str, PyType]) -> str:
        match node:
            case ast.BoolOp(op=op, values=values):
                joiner = " ∧ " if isinstance(op, ast.And) else " ∨ "
                return "(" + joiner.join(self.prop(v, env) for v in values) + ")"
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return f"(¬{self.prop(operand, env)})"
            case ast.Compare(left=left, ops=ops, comparators=comps):
                parts, prev = [], left
                for op, comp in zip(ops, comps, strict=True):
                    parts.append(self._compare(prev, op, comp, env, node))
                    prev = comp
                return parts[0] if len(parts) == 1 else "(" + " ∧ ".join(parts) + ")"
            case ast.Constant(value=bool() as b):
                return "True" if b else "False"
            case ast.Call(func=ast.Name(id="all" | "any" as q), args=[ast.GeneratorExp() as g]):
                return self._quantifier(q, g, env)
        t, ty = self.expr(node, env)
        if ty != BOOL:
            raise TranslationError(
                f"condition `{ast.unparse(node)}` relies on Python truthiness of "
                f"{ty.python()}; compare explicitly (e.g. `len(xs) == 0`, `x != 0`)",
                node,
            )
        return f"({t} = true)"

    def _quantifier(self, q: str, g: ast.GeneratorExp, env: dict[str, PyType]) -> str:
        """`all(P for x in xs if Q)` is `∀ x ∈ xs, Q → P`; `any(...)` is `∃ x ∈ xs, Q ∧ P`."""
        if len(g.generators) != 1:
            raise TranslationError(f"{q}() over more than one `for` is not supported", g)
        gen = g.generators[0]
        if gen.is_async or not isinstance(gen.target, ast.Name):
            raise TranslationError("generator target must be a single name", gen.target)
        src, ety = self._iterable(gen.iter, env)
        inner = {**env, gen.target.id: ety}
        x = lean_ident(gen.target.id)
        body = self.prop(g.elt, inner)
        conds = [self.prop(c, inner) for c in gen.ifs]
        if q == "all":
            body = " → ".join([*conds, body])
            return f"(∀ {x} ∈ {src}, {body})"
        body = " ∧ ".join([*conds, body])
        return f"(∃ {x} ∈ {src}, {body})"

    def _compare(
        self, left: ast.expr, op: ast.cmpop, right: ast.expr, env: dict[str, PyType], node: ast.AST
    ) -> str:
        if isinstance(op, ast.Is | ast.IsNot):
            if not (isinstance(right, ast.Constant) and right.value is None):
                raise TranslationError("`is` is only supported against None", node)
            t, ty = self.expr(left, env)
            if ty.kind != "option":
                raise TranslationError("`is None` on a non-Optional value", node)
            return f"({t} {'=' if isinstance(op, ast.Is) else '≠'} none)"
        sym = _CMP_SYMBOLS.get(type(op))
        if sym is None:
            raise TranslationError(f"comparison `{type(op).__name__}` is not supported", node)
        if sym in ("∈", "∉"):
            r, rty = self.expr(right, env)
            if rty.kind != "list":
                raise TranslationError("`in` is only supported on lists", node)
            lt, lty = self.expr(left, env, rty.args[0])
            self._unify(rty.args[0], lty, node)
            return f"({lt} {sym} {r})"
        lt, lty = self.expr(left, env)
        rt, rty = self.expr(right, env, lty)
        if sym in ("=", "≠"):
            # `r == 3` where r is Optional compares against `some 3`, as in Python.
            if lty.kind == "option" and rty == lty.args[0]:
                rt, rty = f"(some {rt})", lty
            elif rty.kind == "option" and lty == rty.args[0]:
                lt, lty = f"(some {lt})", rty
        ty = self._unify(lty, rty, node)
        if self.js and sym in ("=", "≠") and ty.kind in ("list", "tuple"):
            raise TranslationError(
                "`===` on arrays compares identity in JavaScript, not contents", node
            )
        if sym not in ("=", "≠") and ty != INT:
            raise TranslationError(f"ordering comparison on {ty.python()} is not supported", node)
        return f"({lt} {sym} {rt})"

    # ---- statements --------------------------------------------------------------------------

    def body(self) -> str:
        env = dict(self.sig.params)
        stmts = _body_without_docstring(self.fn)
        stmts = stmts[len(leading_asserts(self.fn)) :]
        for s in leading_asserts(self.fn):
            self.prop(s.test, env)  # a precondition must itself be in the subset
        return self.block(stmts, env, self._fall_off)

    def _fall_off(self, env: dict[str, PyType]) -> str:
        if self.sig.returns.kind == "option":
            return "none"
        raise TranslationError(
            f"`{self.fn.name}` can reach the end without returning (Python would return None)",
            self.fn,
        )

    def block(
        self,
        stmts: list[ast.stmt],
        env: dict[str, PyType],
        tail: Callable[[dict[str, PyType]], str],
    ) -> str:
        if not stmts:
            return tail(env)
        s, rest = stmts[0], stmts[1:]
        match s:
            case ast.Return(value=value):
                if value is None:
                    return self._fall_off(env)
                t, ty = self.expr(value, env, self.sig.returns)
                return self._coerce(t, ty, self.sig.returns, value)
            case ast.Pass():
                return self.block(rest, env, tail)
            case ast.Expr(value=ast.Constant(value=str())):
                return self.block(rest, env, tail)
            case ast.Assign(targets=[ast.Name(id=name)], value=value):
                t, ty = self.expr(value, env, env.get(name))
                if name in env and env[name] != UNKNOWN:
                    t = self._coerce(t, ty, env[name], value)
                    ty = self._unify(env[name], ty, s)
                return self._let(lean_ident(name), t, {**env, name: ty}, rest, tail)
            case ast.Assign(targets=[ast.Tuple(elts=elts)], value=value) if all(
                isinstance(e, ast.Name) for e in elts
            ):
                t, ty = self.expr(value, env)
                if ty.kind != "tuple" or len(ty.args) != len(elts):
                    raise TranslationError("tuple unpacking needs a matching tuple", s)
                names = [e.id for e in elts if isinstance(e, ast.Name)]
                pattern = "(" + ", ".join(lean_ident(n) for n in names) + ")"
                new_env = {**env, **dict(zip(names, ty.args, strict=True))}
                return self._let(pattern, t, new_env, rest, tail)
            case ast.AnnAssign(target=ast.Name(id=name), annotation=ann, value=value) if value:
                try:
                    declared = parse_annotation(ann)
                except UnsupportedType as e:
                    raise TranslationError(str(e), s) from e
                t, ty = self.expr(value, env, declared)
                t = self._coerce(t, ty, declared, value)
                return self._let(
                    f"{lean_ident(name)} : {declared.lean()}",
                    t,
                    {**env, name: declared},
                    rest,
                    tail,
                )
            case ast.AugAssign(target=ast.Name(id=name), op=op, value=value):
                if name not in env:
                    raise TranslationError(f"`{name}` used before assignment", s)
                binop = ast.BinOp(left=ast.Name(id=name), op=op, right=value)
                ast.copy_location(binop, s)
                t, ty = self.expr(binop, env, env[name])
                return self._let(
                    lean_ident(name), t, {**env, name: self._unify(env[name], ty, s)}, rest, tail
                )
            case ast.Expr(value=ast.Call(func=ast.Name(id=hook))) if hook == UNSUPPORTED_HOOK:
                self.expr(s.value, env)  # raises the gap the hook carries
                raise TranslationError("unsupported construct", s)
            case ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(id=name), attr="append"), args=[arg]
                )
            ):
                if name not in env or env[name].kind != "list":
                    raise TranslationError(f"`{name}.append` on a non-list", s)
                if name in dict(self.sig.params):
                    raise TranslationError(
                        f"mutates its argument `{name}`", s, ReasonCode.IMPURE_IO
                    )
                at, aty = self.expr(arg, env, env[name].args[0])
                ty = self._unify(env[name], list_of(aty), s)
                return self._let(
                    lean_ident(name),
                    f"({lean_ident(name)} ++ [{at}])",
                    {**env, name: ty},
                    rest,
                    tail,
                )
            case ast.If(test=test, body=body, orelse=orelse):
                return self._if(s, test, body, orelse, env, rest, tail)
            case ast.For():
                return self._for(s, env, rest, tail)
            case ast.While():
                raise TranslationError(
                    "while loops may not terminate; rewrite as a for loop over a list or a range",
                    s,
                    ReasonCode.NONTERMINATION,
                )
            case ast.Assert() | ast.Raise() | ast.Try():
                raise TranslationError(
                    f"`{type(s).__name__.lower()}` raises exceptions, which the MVP does not model",
                    s,
                )
        raise TranslationError(
            f"statement `{ast.unparse(s).splitlines()[0]}` is not in the MVP subset", s
        )

    def _let(
        self,
        pattern: str,
        value: str,
        env: dict[str, PyType],
        rest: list[ast.stmt],
        tail: Callable[[dict[str, PyType]], str],
    ) -> str:
        return f"let {pattern} := {value}\n{self.block(rest, env, tail)}"

    def _if(
        self,
        s: ast.If,
        test: ast.expr,
        body: list[ast.stmt],
        orelse: list[ast.stmt],
        env: dict[str, PyType],
        rest: list[ast.stmt],
        tail: Callable[[dict[str, PyType]], str],
    ) -> str:
        cond = self.prop(test, env)
        if _contains(body + orelse, ast.Return):
            # Continuation-passing: each branch carries the rest of the block. Always faithful.
            a = self.block(body + rest, dict(env), tail)
            b = self.block(orelse + rest, dict(env), tail)
            return f"if {cond} then\n{_indent(a)}\nelse\n{_indent(b)}"
        # No returns: join on the variables the branches assign, then continue once.
        joined = [n for n in _assigned_names(body + orelse) if n in env]
        if not joined:
            return self.block(rest, env, tail)

        def pack(e: dict[str, PyType]) -> str:
            return _tuple([lean_ident(n) for n in joined])

        a = self.block(body, dict(env), pack)
        b = self.block(orelse, dict(env), pack)
        pattern = _tuple([lean_ident(n) for n in joined])
        return (
            f"let {pattern} := if {cond} then\n{_indent(a, 4)}\n  else\n{_indent(b, 4)}\n"
            f"{self.block(rest, env, tail)}"
        )

    def _for(
        self,
        s: ast.For,
        env: dict[str, PyType],
        rest: list[ast.stmt],
        tail: Callable[[dict[str, PyType]], str],
    ) -> str:
        if s.orelse:
            raise TranslationError("for/else is not supported", s)
        if _contains(s.body, ast.Return, ast.Break, ast.Continue):
            raise TranslationError(
                "return/break/continue inside a loop is not supported; accumulate a result instead",
                s,
            )
        if not isinstance(s.target, ast.Name):
            raise TranslationError("loop target must be a single name", s.target)
        src, ety = self._iterable(s.iter, env)
        state = [n for n in _assigned_names(s.body) if n in env]
        loop_locals = [n for n in _assigned_names(s.body) if n not in env]
        used_after = {n.id for stmt in rest for n in ast.walk(stmt) if isinstance(n, ast.Name)}
        if leaked := (set(loop_locals) | {s.target.id}) & used_after:
            raise TranslationError(f"uses loop-local `{sorted(leaked)[0]}` after the loop", s)
        if not state:
            return self.block(rest, env, tail)
        x = lean_ident(s.target.id)
        pattern = _tuple([lean_ident(n) for n in state])
        inner = {**env, s.target.id: ety}

        def pack(e: dict[str, PyType]) -> str:
            for n in state:  # loop bodies may refine element types of empty lists
                env[n] = self._unify(env[n], e[n], s)
            return pattern

        step = self.block(s.body, inner, pack)
        fold = f"{src}.foldl (fun {pattern} {x} =>\n{_indent(step, 4)}) {pattern}"
        return f"let {pattern} := {fold}\n{self.block(rest, env, tail)}"


class PropTranslator(_FunctionTranslator):
    """Translates formal hints (Python boolean expressions) to Lean Props over a model."""

    def __init__(self, sigs: dict[str, FunctionSig]) -> None:
        self.sigs = sigs
        # A hint is a specification, not JavaScript that runs: `===` on arrays in a JS-syntax
        # hint means equal contents, so the array-identity rule for JS code does not apply.
        self.js = False

    def translate(self, node: ast.expr, env: dict[str, PyType]) -> str:
        return self.prop(node, env)


def _tuple(items: list[str]) -> str:
    return items[0] if len(items) == 1 else "(" + ", ".join(items) + ")"


def translate(info: FunctionInfo) -> LeanModel:
    """Translate the target function and its same-module callees into one Lean model."""
    functions = [*info.callees, info]
    sigs = {f.qualname: signature(f.node) for f in functions}
    defs, names = [], []
    for f in functions:
        sig = sigs[f.qualname]
        params = " ".join(f"({lean_ident(n)} : {t.lean()})" for n, t in sig.params)
        js = getattr(info, "language", "python") == "js"
        body = _FunctionTranslator(f.node, sigs, js=js).body()
        head = f"def {lean_ident(f.qualname)} {params} : {sig.returns.lean()} :="
        defs.append(f"{head}\n{_indent(body)}")
        names.append(lean_ident(f.qualname))
    return LeanModel(
        namespace=lean_namespace(info.module),
        text="\n\n".join(defs) + "\n",
        def_names=names,
        sigs=sigs,
    )
