"""Test miner: examples from assertions, properties from Hypothesis tests.

Recognised shapes (anything else in a test is skipped, never guessed at):

    assert f(1, 2) == 3                                    -> example
    @pytest.mark.parametrize("x,want", [(1, 2), (3, 4)])
    def test_f(x, want): assert f(x) == want               -> one example per row
    @given(st.integers(), st.integers(min_value=0))
    def test_f(a, b):
        assume(a <= b)
        r = f(a, b)
        assert a <= r <= b              -> property `b >= 0 -> a <= b -> a <= r <= b`

A Hypothesis test only claims its property over the inputs its strategies generate, so strategy
bounds (`min_value`, `max_value`, `min_size`) become premises. A strategy Apex does not recognise
(`.filter`, `builds`, a custom composite) makes the domain unknown, so that test is skipped.
"""

import ast
from pathlib import Path
from typing import Any

from apex.analyze.target import FunctionInfo
from apex.core.intent import Source
from apex.intent.candidates import Candidate, Example, Mined, as_example

TEST_CONFIDENCE = 0.95


def find_test_files(info: FunctionInfo) -> list[Path]:
    name = f"test_{info.path.stem}.py"
    here = info.path.resolve().parent
    candidates = [here / name, here / "tests" / name]
    candidates += [p / "tests" / name for p in list(here.parents)[:3]]
    return [p for p in dict.fromkeys(candidates) if p.is_file()]


def _decorator_name(d: ast.expr) -> str:
    target = d.func if isinstance(d, ast.Call) else d
    return ast.unparse(target).split(".")[-1]


class _Subst(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, ast.expr]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return self.mapping.get(node.id, node)


def _parametrize_rows(dec: ast.Call) -> list[dict[str, Any]] | None:
    if len(dec.args) < 2:
        return None
    try:
        names_v = ast.literal_eval(dec.args[0])
        rows_v = ast.literal_eval(dec.args[1])
    except ValueError:
        return None
    names = [n.strip() for n in names_v.split(",")] if isinstance(names_v, str) else list(names_v)
    rows = []
    for row in rows_v:
        values = row if len(names) > 1 else (row,)
        if not isinstance(values, tuple | list) or len(values) != len(names):
            return None
        rows.append(dict(zip(names, values, strict=True)))
    return rows


def _examples_in(
    fn: ast.FunctionDef, info: FunctionInfo, path: Path, rows: list[dict[str, Any]] | None
) -> list[Example]:
    out = []
    for row in rows or [{}]:
        mapping = {k: ast.parse(repr(v), mode="eval").body for k, v in row.items()}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assert):
                continue
            test = _Subst(mapping).visit(ast.parse(ast.unparse(node.test), mode="eval").body)
            pair = as_example(test, info.qualname)
            if pair is None:
                continue
            out.append(
                Example(
                    pair[0],
                    pair[1],
                    Source(type="test", loc=f"{path.name}:{node.lineno}"),
                    ast.unparse(test),
                )
            )
    return out


# ---- Hypothesis tests ------------------------------------------------------------------------


def _strategy_premises(strategy: ast.expr, var: str) -> list[str] | None:
    """Premises a strategy imposes on `var`, or None if the strategy is not understood."""
    if not isinstance(strategy, ast.Call):
        return None
    name = ast.unparse(strategy.func).split(".")[-1]
    kw = {k.arg: k.value for k in strategy.keywords if k.arg}

    def bound(key: str, pos: int) -> str | None:
        node = kw.get(key) or (strategy.args[pos] if len(strategy.args) > pos else None)
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        return ast.unparse(node)

    match name:
        case "integers":
            out = []
            if (lo := bound("min_value", 0)) is not None:
                out.append(f"{var} >= {lo}")
            if (hi := bound("max_value", 1)) is not None:
                out.append(f"{var} <= {hi}")
            return out
        case "booleans" | "none":
            return []
        case "lists":
            elem = kw.get("elements") or (strategy.args[0] if strategy.args else None)
            if elem is None:
                return None
            inner = _strategy_premises(elem, "_v")
            if inner is None:
                return None
            out = []
            if (lo := bound("min_size", 1)) is not None and lo != "0":
                out.append(f"len({var}) >= {lo}")
            if inner:
                out.append(f"all({' and '.join(inner)} for _v in {var})")
            return out
    return None


def _given_property(
    fn: ast.FunctionDef, dec: ast.Call, info: FunctionInfo, path: Path
) -> list[Candidate]:
    test_params = [a.arg for a in fn.args.args]
    strategies: dict[str, ast.expr] = {k.arg: k.value for k in dec.keywords if k.arg}
    if dec.args:
        strategies.update(zip(test_params[-len(dec.args) :], dec.args, strict=False))
    if set(strategies) != set(test_params):
        return []

    premises: list[str] = []
    for var in test_params:
        p = _strategy_premises(strategies[var], var)
        if p is None:
            return []
        premises += p

    call_args: list[str] | None = None
    result_var: str | None = None
    asserts: list[ast.Assert] = []
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body

    def is_target_call(e: ast.expr) -> bool:
        return (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Name)
            and (e.func.id == info.qualname)
        )

    def record_call(c: ast.Call) -> bool:
        nonlocal call_args
        if c.keywords or not all(isinstance(a, ast.Name) for a in c.args):
            return False
        names = [a.id for a in c.args if isinstance(a, ast.Name)]
        if call_args is not None and names != call_args:
            return False
        call_args = names
        return True

    for stmt in body:
        match stmt:
            case ast.Expr(value=ast.Call(func=f, args=[cond])) if (
                ast.unparse(f).split(".")[-1] == "assume"
            ):
                premises.append(ast.unparse(cond))
            case ast.Assign(targets=[ast.Name(id=v)], value=ast.Call() as c) if is_target_call(c):
                if result_var is not None or not record_call(c):
                    return []
                result_var = v
            case ast.Assert(test=test):
                for c in ast.walk(test):
                    if isinstance(c, ast.Call) and is_target_call(c) and not record_call(c):
                        return []
                asserts.append(stmt)
            case _:
                return []
    if call_args is None or not asserts:
        return []
    fn_params = [a.arg for a in info.node.args.args]
    if len(call_args) != len(fn_params) or len(set(call_args)) != len(call_args):
        return []
    if set(test_params) - set(call_args):
        return []  # a generated value that is not an argument: not a property of f alone

    rename = {t: ast.Name(id=p, ctx=ast.Load()) for t, p in zip(call_args, fn_params, strict=True)}
    res_name = next(n for n in ("r", "result") if n not in fn_params)
    res = ast.Name(id=res_name, ctx=ast.Load())

    class _ToHint(_Subst):
        def visit_Call(self, node: ast.Call) -> ast.expr:
            if is_target_call(node):
                return res
            return self.generic_visit(node)  # type: ignore[return-value]

    mapping: dict[str, ast.expr] = {**rename, **({result_var: res} if result_var else {})}

    def to_hint(text: str) -> str:
        return ast.unparse(_ToHint(mapping).visit(ast.parse(text, mode="eval").body))

    pre = [to_hint(p) for p in premises]
    out = []
    for a in asserts:
        hint = " -> ".join([*pre, to_hint(ast.unparse(a.test))])
        out.append(
            Candidate(
                kind="property",
                text=f"{fn.name}: {ast.unparse(a.test)}",
                hint=hint,
                source=Source(type="test", loc=f"{path.name}:{a.lineno}"),
                confidence=TEST_CONFIDENCE,
            )
        )
    return out


def mine_tests(info: FunctionInfo) -> Mined:
    examples: list[Example] = []
    candidates: list[Candidate] = []
    for path in find_test_files(info):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            decs = {_decorator_name(d): d for d in fn.decorator_list}
            if isinstance(decs.get("given"), ast.Call):
                candidates += _given_property(fn, decs["given"], info, path)  # type: ignore[arg-type]
                continue
            rows = None
            if isinstance(p := decs.get("parametrize"), ast.Call):
                rows = _parametrize_rows(p)
                if rows is None:
                    continue
            examples += _examples_in(fn, info, path, rows)
        # Module-level asserts and asserts in plain helpers.
        for node in tree.body:
            if isinstance(node, ast.Assert) and (pair := as_example(node.test, info.qualname)):
                examples.append(
                    Example(
                        pair[0],
                        pair[1],
                        Source(type="test", loc=f"{path.name}:{node.lineno}"),
                        ast.unparse(node.test),
                    )
                )
    return Mined(examples, candidates)
