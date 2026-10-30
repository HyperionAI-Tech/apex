"""Coverage-guided fuzzing of JavaScript / TypeScript with Jazzer.js (`--engine jazzer`).

Jazzer.js (libFuzzer for Node) is pinned and installed by `apex init --js` into `~/.apex/js`;
it is the only npm package Apex uses. A campaign writes a small ES-module fuzz target that imports
the real module, builds arguments from libFuzzer's bytes with the same small/medium/large integer
mix the Python engines use, and checks the property *inside* Node at full speed. To do that, the
property's internal hint is emitted back to JavaScript (`hint_to_js`), with deep, bigint-aware
equality and JS's truncated `%`. The input Jazzer reports is then replayed in Python through the
Node proxy by refutation's own evaluator before it counts, so the JS emitter can make a campaign
miss a bug but never invent one.
"""

import ast
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType
from apex.intent.hints import Hint
from apex.lean.project import apex_home

JAZZER_VERSION = "4.0.0"
_RUNS = re.compile(r"Done (\d+) runs in")


class JazzerUnavailable(RuntimeError):
    pass


def jazzer_home() -> Path:
    return apex_home() / "js"


def jazzer_bin() -> Path:
    path = jazzer_home() / "node_modules" / ".bin" / "jazzer"
    if not path.is_file():
        raise JazzerUnavailable(
            "Jazzer.js is not installed: run `apex init --js` (needs Node.js and npm), or use "
            "--engine hypothesis"
        )
    return path


def install() -> Path:
    """Install the pinned Jazzer.js into ~/.apex/js with npm; returns the jazzer binary."""
    npm = shutil.which("npm")
    if npm is None:
        raise JazzerUnavailable("installing Jazzer.js needs npm on PATH")
    home = jazzer_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "package.json").write_text(
        json.dumps({"private": True, "dependencies": {"@jazzer.js/core": JAZZER_VERSION}}, indent=2)
        + "\n"
    )
    run = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"], cwd=home, capture_output=True, text=True
    )
    if run.returncode != 0:
        raise JazzerUnavailable(f"npm install failed: {run.stderr.strip()[-300:]}")
    return jazzer_bin()


# ---- hints back to JavaScript ------------------------------------------------------------------

_RUNTIME = r"""
const __eq = (a, b) => {
  if (Array.isArray(a) && Array.isArray(b))
    return a.length === b.length && a.every((x, i) => __eq(x, b[i]));
  if ((a === null || a === undefined) && (b === null || b === undefined)) return true;
  if (typeof a === "bigint" || typeof b === "bigint") {
    try { return BigInt(a) === BigInt(b); } catch { return false; }
  }
  return a === b;
};
const __in = (x, xs) => xs.some((y) => __eq(x, y));
const __abs = (x) => (x < 0 ? -x : x);
const __min = (a, b) => (a < b ? a : b);
const __max = (a, b) => (a > b ? a : b);
const __sum = (xs, zero) => xs.reduce((a, b) => a + b, zero);
const __fdiv = (a, b) => {
  if (typeof a !== "bigint") return Math.floor(a / b);
  const q = a / b;
  return a % b !== 0n && (a < 0n) !== (b < 0n) ? q - 1n : q;
};
const __fmod = (a, b) => a - b * __fdiv(a, b);
const __tdiv = (a, b) => (typeof a === "bigint" ? a / b : Math.trunc(a / b));
"""


class _ToJs:
    def __init__(self, bigint: bool, helpers: set[str], result: str) -> None:
        self.bigint = bigint
        self.helpers = helpers
        self.result = result

    def __call__(self, e: ast.expr) -> str:
        return self.emit(e)

    def num(self, v: int) -> str:
        return f"{v}n" if self.bigint else str(v)

    def emit(self, e: ast.expr) -> str:
        match e:
            case ast.Constant(value=bool() as b):
                return "true" if b else "false"
            case ast.Constant(value=None):
                return "null"
            case ast.Constant(value=int() as v):
                return self.num(v)
            case ast.Name(id=name):
                return "result" if name == self.result else name
            case ast.BoolOp(op=op, values=vals):
                sep = " && " if isinstance(op, ast.And) else " || "
                return "(" + sep.join(self.emit(v) for v in vals) + ")"
            case ast.UnaryOp(op=ast.Not(), operand=x):
                return f"!({self.emit(x)})"
            case ast.UnaryOp(op=ast.USub(), operand=x):
                return f"(-{self.emit(x)})"
            case ast.BinOp(left=a, op=op, right=b):
                la, lb = self.emit(a), self.emit(b)
                if isinstance(op, ast.FloorDiv):
                    return f"__fdiv({la}, {lb})"
                if isinstance(op, ast.Mod):
                    return f"__fmod({la}, {lb})"
                sym = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Pow: "**", ast.Div: "/"}
                return f"({la} {sym[type(op)]} {lb})"
            case ast.Compare(left=left, ops=ops, comparators=comps):
                parts, prev = [], left
                for op, nxt in zip(ops, comps, strict=True):
                    parts.append(self.compare(prev, op, nxt))
                    prev = nxt
                return "(" + " && ".join(parts) + ")"
            case ast.IfExp(test=t, body=b, orelse=o):
                return f"({self.emit(t)} ? {self.emit(b)} : {self.emit(o)})"
            case ast.List(elts=elts) | ast.Tuple(elts=elts):
                return "[" + ", ".join(self.emit(x) for x in elts) + "]"
            case ast.Subscript(value=v, slice=ast.Constant(value=int() as k)):
                return f"{self.emit(v)}[{k}]"
            case ast.Subscript(value=v, slice=ast.Slice(lower=lo, upper=hi, step=None)):
                lo_s = self.emit(lo) if lo is not None else "0"
                hi_s = f", {self.emit(hi)}" if hi is not None else ""
                if self.bigint:  # slice bounds are plain numbers even in bigint code
                    lo_s, hi_s = lo_s.replace("n", ""), hi_s.replace("n", "")
                return f"{self.emit(v)}.slice({lo_s}{hi_s})"
            case ast.Call(func=ast.Name(id=fn), args=args):
                return self.call(fn, args)
            case ast.ListComp(elt=elt, generators=[gen]):
                return self.comprehension(elt, gen)
        raise ValueError(f"no JavaScript form for `{ast.unparse(e)}`")

    def compare(self, a: ast.expr, op: ast.cmpop, b: ast.expr) -> str:
        la, lb = self.emit(a), self.emit(b)
        if isinstance(op, ast.Eq):
            return f"__eq({la}, {lb})"
        if isinstance(op, ast.NotEq):
            return f"!__eq({la}, {lb})"
        if isinstance(op, ast.Is):
            return f"({la} === null || {la} === undefined)"
        if isinstance(op, ast.IsNot):
            return f"!({la} === null || {la} === undefined)"
        if isinstance(op, ast.In):
            return f"__in({la}, {lb})"
        if isinstance(op, ast.NotIn):
            return f"!__in({la}, {lb})"
        sym = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}[type(op)]  # type: ignore[index]
        return f"{la} {sym} {lb}"

    def call(self, fn: str, args: list[ast.expr]) -> str:
        a = [self.emit(x) for x in args]
        if fn == "len":
            return f"BigInt({a[0]}.length)" if self.bigint else f"{a[0]}.length"
        if fn in ("abs", "min", "max"):
            return f"__{fn}({', '.join(a)})"
        if fn == "sum":
            return f"__sum({a[0]}, {self.num(0)})"
        if fn == "__apex_tmod__":
            return f"({a[0]} % {a[1]})"
        if fn == "__apex_tdiv__":
            return f"__tdiv({a[0]}, {a[1]})"
        if fn in ("all", "any") and args and isinstance(args[0], ast.GeneratorExp):
            g = args[0]
            gen = g.generators[0]
            var = ast.unparse(gen.target)
            method = "every" if fn == "all" else "some"
            body = self.emit(g.elt)
            src = self.emit(gen.iter)
            for cond in gen.ifs:
                src = f"{src}.filter(({var}) => {self.emit(cond)})"
            return f"{src}.{method}(({var}) => {body})"
        if fn in self.helpers:
            return f"__mod.{fn}({', '.join(a)})"
        raise ValueError(f"no JavaScript form for a call to `{fn}`")

    def comprehension(self, elt: ast.expr, gen: ast.comprehension) -> str:
        var = ast.unparse(gen.target)
        src = self.emit(gen.iter)
        for cond in gen.ifs:
            src = f"{src}.filter(({var}) => {self.emit(cond)})"
        return f"{src}.map(({var}) => {self.emit(elt)})"


def hint_to_js(
    hint: Hint, pres: list[ast.expr], helpers: set[str], bigint: bool
) -> tuple[str, str]:
    """(precondition, check) as JavaScript expressions over the parameters and `result`."""
    to_js = _ToJs(bigint, helpers, hint.result)
    mentions = lambda e: any(  # noqa: E731
        isinstance(n, ast.Name) and n.id == hint.result for n in ast.walk(e)
    )
    pre_parts = [to_js(p) for p in pres] + [to_js(p) for p in hint.premises if not mentions(p)]
    post_premises = [to_js(p) for p in hint.premises if mentions(p)]
    conclusion = to_js(hint.conclusion)
    pre = " && ".join(pre_parts) or "true"
    check = conclusion if not post_premises else f"!({' && '.join(post_premises)}) || {conclusion}"
    return pre, check


def _consume(ty: PyType, bigint: bool) -> str:
    match ty.kind:
        case "int":
            expr = "__int(p)"
            return f"BigInt({expr})" if bigint else expr
        case "bool":
            return "p.consumeBoolean()"
        case "list":
            item = _consume(ty.args[0], bigint)
            return f"Array.from({{ length: p.consumeIntegralInRange(0, 8) }}, () => {item})"
        case "tuple":
            return "[" + ", ".join(_consume(a, bigint) for a in ty.args) + "]"
        case "option":
            return f"(p.consumeBoolean() ? null : {_consume(ty.args[0], bigint)})"
    raise ValueError(f"no fuzz input for {ty.kind}")


def fuzz_target_source(
    info: FunctionInfo,
    params: list[tuple[str, PyType]],
    pre: str,
    check: str,
    found: Path,
) -> str:
    bigint = info.numeric == {"bigint"}
    names = [n for n, _ in params]
    consume = ", ".join(_consume(t, bigint) for _, t in params)
    module_url = Path(info.path).resolve().as_uri()
    require_root = (jazzer_home() / "package.json").as_posix()
    return f"""import {{ createRequire }} from "node:module";
import fs from "node:fs";
const require = createRequire({json.dumps(require_root)});
const {{ FuzzedDataProvider }} = require("@jazzer.js/core");
const __mod_ns = await import({json.dumps(module_url)});
const __mod = {{ ...(__mod_ns.default ?? {{}}), ...__mod_ns }};
const __fn = __mod[{json.dumps(info.qualname)}];
{_RUNTIME}
const __int = (p) => {{
  const bound = [16, 2 ** 16, 2 ** 26][p.consumeIntegralInRange(0, 2)];
  return p.consumeIntegralInRange(-bound, bound);
}};
const __enc = (v) => typeof v === "bigint" ? v.toString() : Array.isArray(v) ? v.map(__enc) : v;
const __found = {json.dumps(str(found))};
const __report = (args) => fs.writeFileSync(__found, JSON.stringify({{ args: __enc(args) }}));
export function fuzz(data) {{
  const p = new FuzzedDataProvider(data);
  const [{", ".join(names)}] = [{consume}];
  if (!({pre})) return;
  let result;
  try {{
    result = __fn({", ".join(names)});
  }} catch (e) {{
    if (e instanceof RangeError && /call stack/i.test(e.message)) return;
    __report([{", ".join(names)}]);
    throw e;
  }}
  const unsafe = typeof result === "number" && !Number.isSafeInteger(result);
  if (unsafe && Number.isInteger(result)) return;  // outside the safe-integer assumption
  if (!({check})) {{
    __report([{", ".join(names)}]);
    throw new Error("apex: property violated");
  }}
}}
"""


def run_campaign(
    info: FunctionInfo,
    params: list[tuple[str, PyType]],
    pre: str,
    check: str,
    budget_s: float,
) -> tuple[int, list[Any] | None, str | None]:
    """(executions, the reported arguments or None, a note)."""
    binary = jazzer_bin()
    with tempfile.TemporaryDirectory(prefix="apex-jazzer-") as tmp:
        found = Path(tmp) / "found.json"
        target = Path(tmp) / "target.mjs"
        target.write_text(fuzz_target_source(info, params, pre, check, found))
        cmd = [
            str(binary),
            str(target),
            "-i",
            Path(info.path).name,
            "--",
            f"-max_total_time={max(1, int(budget_s))}",
            "-use_value_profile=1",
            f"-artifact_prefix={tmp}/",
        ]
        try:
            run = subprocess.run(
                cmd,
                cwd=Path(info.path).resolve().parent,
                capture_output=True,
                text=True,
                timeout=budget_s + 60,
            )
        except subprocess.TimeoutExpired:
            return 0, None, "the Jazzer.js campaign did not stop within its budget"
        out = run.stdout + run.stderr
        m = _RUNS.search(out)
        runs = int(m.group(1)) if m else 0
        if found.is_file():
            return max(runs, 1), json.loads(found.read_text())["args"], None
        if run.returncode != 0 and not m:
            tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["no output"]
            return 0, None, f"Jazzer.js failed: {tail[0][:200]}"
        return runs, None, None
