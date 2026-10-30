"""Formal hints: the machine-readable half of an Intent Model property.

A hint is a Python boolean expression over the function's parameters and its result, with `->` for
implication:

    lo <= hi -> lo <= r <= hi
    all(v > 0 for v in r)
    r is not None -> r * b <= a

The same text is evaluated in Python (to detect conflicts between intent sources and to search for
counterexamples on the real code) and translated to a Lean statement (stage 2). Keeping one
language for both is what lets a counterexample and a theorem be about the same claim.

Hints often come from a model, so they are parsed into an AST and checked against an allowlist
before anything evaluates them: no attribute access, no strings, no calls except a few builtins and
functions defined in the target's own module.
"""

import ast
import builtins
from dataclasses import dataclass
from typing import Any

RESULT_NAMES = ("r", "result", "__return__")


def _tmod(a: int, b: int) -> int:
    """JavaScript's `%` on integers: the remainder truncated toward zero (sign of `a`)."""
    return a - b * _tdiv(a, b)


def _tdiv(a: int, b: int) -> int:
    """`Math.trunc(a / b)` on integers."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


JS_INT_OPS = {"__apex_tmod__": _tmod, "__apex_tdiv__": _tdiv}
"""What the JS lowering writes for `%` and `Math.trunc(a / b)`, so hints on JS code mean what
JavaScript means (and the translator gives them Lean's Int.tmod / Int.tdiv)."""
BUILTINS = frozenset({"all", "any", "len", "abs", "min", "max", "sum", "range", *JS_INT_OPS})

_ALLOWED = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Call,
    ast.List,
    ast.Tuple,
    ast.Subscript,
    ast.Slice,
    ast.IfExp,
    ast.GeneratorExp,
    ast.ListComp,
    ast.comprehension,
)


class HintError(ValueError):
    """The hint is not in the hint language or names something it may not."""


class HintEvalError(RuntimeError):
    """Evaluating a valid hint raised (e.g. a helper function failed on this input)."""


def result_name(params: list[str]) -> str:
    """`r`, unless a parameter is already called `r`."""
    return next(n for n in RESULT_NAMES if n not in params)


@dataclass(frozen=True)
class Hint:
    premises: tuple[ast.expr, ...]
    conclusion: ast.expr
    result: str
    """The name the result goes by in this hint (`r` unless a parameter is called `r`)."""

    @property
    def text(self) -> str:
        """Canonical form: equal for hints that differ only in spacing, aliases or `&&`/`||`."""
        return " -> ".join(ast.unparse(e) for e in (*self.premises, self.conclusion))

    @property
    def mentions_result(self) -> bool:
        return any(
            isinstance(n, ast.Name) and n.id == self.result
            for e in (*self.premises, self.conclusion)
            for n in ast.walk(e)
        )


def _split_implication(text: str) -> list[str]:
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith("->", i):
            parts.append(text[start:i])
            start = i + 2
            i += 1
        i += 1
    parts.append(text[start:])
    return parts


class _Rename(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id in self.mapping:
            return ast.copy_location(ast.Name(id=self.mapping[node.id], ctx=node.ctx), node)
        return node


def _check(node: ast.expr, allowed_names: set[str], callables: set[str]) -> None:
    bound = {
        t.id
        for n in ast.walk(node)
        if isinstance(n, ast.comprehension)
        for t in ast.walk(n.target)
        if isinstance(t, ast.Name)
    }
    for n in ast.walk(node):
        if not isinstance(n, _ALLOWED):
            raise HintError(f"`{type(n).__name__}` is not allowed in a formal hint")
        if isinstance(n, ast.Constant) and not (n.value is None or isinstance(n.value, bool | int)):
            raise HintError(f"literal {n.value!r} is not allowed (integers, booleans, None only)")
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            k = n.right
            if not (isinstance(k, ast.Constant) and type(k.value) is int and 0 <= k.value <= 64):
                raise HintError("`**` needs an integer literal exponent between 0 and 64")
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in callables:
                raise HintError(
                    f"call `{ast.unparse(n.func)}` is not allowed; use "
                    f"{', '.join(sorted(BUILTINS))} or a function from the same module"
                )
            if n.keywords:
                raise HintError("keyword arguments are not allowed in a formal hint")
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id.startswith("__") and n.id not in JS_INT_OPS:  # JS `%`, `Math.trunc(a / b)`
                raise HintError(f"name `{n.id}` is not allowed")
            if n.id not in allowed_names | bound | callables:
                raise HintError(
                    f"unknown name `{n.id}`; a hint may use the parameters, "
                    f"the result, and names bound inside it"
                )


def parse_hint(
    text: str, params: list[str], helpers: set[str] | frozenset[str] = frozenset()
) -> Hint:
    """Parse and validate a hint. Raises `HintError` with a message an agent can act on."""
    res = result_name(params)
    src = text.strip().replace("&&", " and ").replace("||", " or ").replace("==>", "->")
    if not src:
        raise HintError("empty hint")
    exprs = []
    for part in _split_implication(src):
        try:
            e = ast.parse(part.strip(), mode="eval").body
        except SyntaxError as err:
            raise HintError(f"`{part.strip()}` is not a Python expression: {err.msg}") from err
        aliases = {a: res for a in RESULT_NAMES if a != res and a not in params}
        e = _Rename(aliases).visit(e)
        _check(e, {*params, res}, set(BUILTINS) | set(helpers))
        exprs.append(ast.fix_missing_locations(e))
    return Hint(premises=tuple(exprs[:-1]), conclusion=exprs[-1], result=res)


def parse_condition(
    text: str, params: list[str], helpers: set[str] | frozenset[str] = frozenset()
) -> ast.expr:
    """A precondition: a hint with no implication that does not mention the result."""
    h = parse_hint(text, params, helpers)
    if h.premises:
        raise HintError("a precondition cannot contain `->`")
    if h.mentions_result:
        raise HintError(f"a precondition cannot mention the result `{h.result}`")
    return h.conclusion


_SAFE_BUILTINS = {
    name: getattr(builtins, name) for name in BUILTINS if name not in JS_INT_OPS
} | JS_INT_OPS


def evaluate(expr: ast.expr, env: dict[str, Any], helpers: dict[str, Any] | None = None) -> bool:
    """Evaluate a validated hint expression. Names go in globals so generators can see them."""
    code = compile(ast.Expression(body=expr), "<hint>", "eval")
    scope = {"__builtins__": _SAFE_BUILTINS, **(helpers or {}), **env}
    try:
        return bool(eval(code, scope))
    except Exception as e:
        raise HintEvalError(f"{type(e).__name__}: {e}") from e


def holds(hint: Hint, env: dict[str, Any], helpers: dict[str, Any] | None = None) -> bool | None:
    """None when a premise is false (the hint says nothing here), else whether it holds."""
    for p in hint.premises:
        if not evaluate(p, env, helpers):
            return None
    return evaluate(hint.conclusion, env, helpers)
