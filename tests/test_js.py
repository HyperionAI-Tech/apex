"""The JavaScript / TypeScript frontend: lowering, hints, mining, the Node bridge, and verdicts."""

import shutil
import textwrap
from pathlib import Path

import pytest

from apex.analyze.target import load_function
from apex.core.verdict import ReasonCode, Verdict
from apex.fidelity.bridge import CallTimeout
from apex.intent.hints import JS_INT_OPS
from apex.js.intent import js_hint, mine_js_tests, mine_jsdoc
from apex.js.runner import JsError, OutsideSafeIntegers, load_js_module
from apex.pipeline import Workspace, _analyze, discover_targets, verify
from apex.translate.lean import TranslationError, translate

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js 22.6+")


def _ts(tmp_path: Path, src: str, name: str = "m.ts") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(src))
    return path


def _model(path: Path, fn: str) -> str:
    return translate(load_function(f"{path}::{fn}")).text


def _gap(path: Path, fn: str) -> TranslationError:
    with pytest.raises(TranslationError) as e:
        translate(load_function(f"{path}::{fn}"))
    return e.value


# ---- lowering: JavaScript meaning, made explicit ----------------------------------------------


def test_js_arithmetic_keeps_its_meaning(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        export function rem(a: number, b: number): number { return a % b; }
        export function fdiv(a: number, b: number): number { return Math.floor(a / b); }
        export function tdiv(a: number, b: number): number { return Math.trunc(a / b); }
        export function prod(xs: bigint[]): bigint { return xs.reduce((acc, x) => acc * x, 1n); }
        """,
    )
    assert "Int.tmod a b" in _model(path, "rem")  # -7 % 2 is -1 in JavaScript
    assert "Apex.pyFloorDiv a b" in _model(path, "fdiv")  # Math.floor(a / b): floor division
    assert "Int.tdiv a b" in _model(path, "tdiv")
    assert ".foldl (fun acc x => (acc * x)) (1 : Int)" in _model(path, "prod")


def test_constructs_that_would_change_meaning_are_refused(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        export function div(a: number, b: number): number { return a / b; }
        export function loose(a: number, b: number): boolean { return a == b; }
        export function bits(a: number): number { return a | 1; }
        export function same(xs: number[], ys: number[]): boolean { return xs === ys; }
        export function spin(n: number): number { let i = 0; while (i < n) { i++; } return i; }
        """,
    )
    assert _gap(path, "div").reason_code is ReasonCode.FLOAT_SEMANTICS
    assert "loose equality" in str(_gap(path, "loose"))
    assert "32-bit" in str(_gap(path, "bits"))
    assert "identity" in str(_gap(path, "same"))  # === on arrays compares references in JS
    spin = _gap(path, "spin")
    assert spin.reason_code is ReasonCode.NONTERMINATION and spin.line == 6


def test_counted_for_loops_and_array_methods_lower_to_the_subset(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        export function tri(n: number): number {
          let t = 0;
          for (let i = 0; i <= n; i++) { t += i; }
          return t;
        }
        export function evens(xs: number[]): number[] { return xs.filter((x) => x % 2 === 0); }
        export function pos(xs: number[]): boolean { return xs.every((x) => x > 0); }
        export function rev(xs: number[]): number[] {
          let out: number[] = [];
          for (const x of xs) { out = [x, ...out]; }
          return out;
        }
        """,
    )
    assert "Apex.pyRange (0 : Int) (n + (1 : Int))" in _model(path, "tri")
    assert ".filter" in _model(path, "evens")
    assert "∀ x ∈ xs, (x > (0 : Int))" in _model(path, "pos")
    assert "[x] ++ out" in _model(path, "rev").replace("(", "").replace(")", "")


def test_purity_sees_inside_refused_expressions(tmp_path: Path) -> None:
    # Math.floor(Math.random() * n) is refused (not a quotient), but it must still be found
    # impure, or refutation would call it.
    path = _ts(
        tmp_path,
        """
        export function roll(n: number): number { return Math.floor(Math.random() * n) + 1; }
        export function noisy(x: number): number { console.log(x); return x; }
        export async function later(x: number): Promise<number> { return x; }
        function hidden(x: number): number { return x; }
        export function untyped(x) { return x; }
        """,
    )
    ws = Workspace.discover(tmp_path)
    reasons = {
        fn: _analyze(f"{path}::{fn}", ws).gap.reason_code  # type: ignore[union-attr]
        for fn in ("roll", "noisy", "later", "hidden", "untyped")
    }
    assert reasons == {
        "roll": ReasonCode.IMPURE_IO,
        "noisy": ReasonCode.IMPURE_IO,
        "later": ReasonCode.NEEDS_CONCURRENCY_TOOL,
        "hidden": ReasonCode.UNSUPPORTED_CONSTRUCT,  # not exported: Apex cannot run it
        "untyped": ReasonCode.UNSUPPORTED_CONSTRUCT,
    }


def test_jsdoc_types_type_plain_javascript(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        /** @param {number[]} xs @returns {number} */
        export function total(xs) { return xs.reduce((a, b) => a + b, 0); }
        """,
        "m.js",
    )
    info = load_function(f"{path}::total")
    assert info.typed and info.numeric == {"number"}
    assert "Apex.pySum xs" in translate(info).text


# ---- hints and mining --------------------------------------------------------------------------


def test_js_hints_become_internal_hints() -> None:
    assert js_hint("lo <= result && result <= hi") == "lo <= result and result <= hi"
    assert js_hint("lo <= x && x <= hi ==> result === x") == "lo <= x and x <= hi -> result == x"
    assert js_hint("xs.length === 0 ==> result === 0n") == "len(xs) == 0 -> result == 0"
    assert js_hint("n % 2 !== 0") == "__apex_tmod__(n, 2) != 0"
    assert js_hint("result === xs.filter((v) => v > 0).length") == (
        "result == len([v for v in xs if v > 0])"
    )
    assert JS_INT_OPS["__apex_tmod__"](-7, 2) == -1 and JS_INT_OPS["__apex_tdiv__"](-7, 2) == -3


def test_jsdoc_and_tests_are_mined(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        /**
         * @pre lo <= hi
         * @post lo <= result && result <= hi
         * @example clamp(5, 0, 3) // => 3
         * @example
         * clamp(-2, 0, 3) === 0
         */
        export function clamp(x: number, lo: number, hi: number): number {
          return Math.max(lo, Math.min(x, hi));
        }
        """,
    )
    (tmp_path / "m.test.ts").write_text(
        textwrap.dedent(
            """
            import fc from "fast-check";
            import { clamp } from "./m";
            test("c", () => {
              expect(clamp(9, 0, 3)).toBe(3);
              fc.assert(fc.property(fc.integer(), fc.integer({ min: 0, max: 10 }),
                (x, hi) => clamp(x, 0, hi) >= 0));
            });
            """
        )
    )
    info = load_function(f"{path}::clamp")
    doc = mine_jsdoc(info)
    assert [(e.args, e.expected) for e in doc.examples] == [((5, 0, 3), 3), ((-2, 0, 3), 0)]
    assert {c.kind: c.hint for c in doc.candidates} == {
        "precondition": "lo <= hi",
        "property": "lo <= result and result <= hi",
    }
    tests = mine_js_tests(info)
    assert [(e.args, e.expected) for e in tests.examples] == [((9, 0, 3), 3)]
    (prop,) = tests.candidates
    # clamp(x, 0, hi) is a claim about lo == 0, not about every lo: binding regression.
    assert prop.hint == "(lo == 0 and hi >= 0) and hi <= 10 -> result >= 0"


def test_discovery_finds_sources_and_skips_tests_and_declarations(tmp_path: Path) -> None:
    for name in ("a.ts", "b.test.ts", "c.d.ts", "d.spec.js"):
        (tmp_path / name).write_text("export function f(x: number): number { return x; }\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "e.ts").write_text("export function g(): number { return 1; }\n")
    targets = discover_targets([str(tmp_path)], Workspace.discover(tmp_path))
    assert targets == [f"{tmp_path / 'a.ts'}::f"]


# ---- the Node bridge ---------------------------------------------------------------------------


@needs_node
def test_real_javascript_runs_with_its_own_semantics(tmp_path: Path) -> None:
    path = _ts(
        tmp_path,
        """
        export function rem(a: number, b: number): number { return a % b; }
        export function big(xs: bigint[]): bigint { return xs.reduce((a, x) => a * x, 1n); }
        export function sq(x: number): number { return x * x; }
        export function push(xs: number[]): number { xs.push(1); return xs.length; }
        export function boom(x: number): number { throw new TypeError("no " + x); }
        export function deep(n: number): number { return n <= 0 ? 0 : 1 + deep(n - 1); }
        export function spin(n: number): number { while (true) {} }
        """,
    )
    m = load_js_module(load_function(f"{path}::rem"))
    assert m.rem(-7, 2) == -1
    big = load_js_module(load_function(f"{path}::big"))
    assert big.big([2, 3, 10**20]) == 6 * 10**20  # bigint stays exact
    with pytest.raises(OutsideSafeIntegers):
        m.sq(2**30)  # 2**60 is not a safe integer: outside what `number` proofs assume
    xs = [5]
    assert m.push(xs) == 2 and xs == [5, 1]  # the mutation shows up in Python
    with pytest.raises(JsError, match="TypeError: no 1"):
        m.boom(1)
    with pytest.raises(RecursionError):
        m.deep(10**7)
    with pytest.raises(CallTimeout):
        m.spin.apex_call((1,), 0.5)
    assert m.rem(7, 3) == 1  # the worker restarts after the timeout


# ---- end to end through Lean -------------------------------------------------------------------


@needs_node
@pytest.mark.lean
def test_typescript_end_to_end(tmp_path: Path, lean_project) -> None:
    path = _ts(
        tmp_path,
        """
        /**
         * @pre lo <= hi
         * @post lo <= result && result <= hi
         * @example clamp(5, 0, 3) // => 3
         */
        export function clamp(x: number, lo: number, hi: number): number {
          return Math.max(lo, Math.min(x, hi));
        }
        /** @post lo <= x && x <= hi ==> result === x */
        export function bad(x: number, lo: number, hi: number): number {
          return Math.max(lo, Math.min(x, hi - 1));
        }
        /** @example total([1n, 2n]) // => 3n */
        export function total(xs: bigint[]): bigint {
          let t = 0n;
          for (const x of xs) { t += x; }
          return t;
        }
        """,
    )
    from apex.pipeline import prove_tactics, scaffold

    ws = Workspace.discover(tmp_path)
    for fn in ("clamp", "bad", "total"):
        scaffold(f"{path}::{fn}", ws, lean_project)
        prove_tactics(f"{path}::{fn}", ws, project=lean_project)
    report = verify(
        [f"{path}::{fn}" for fn in ("clamp", "bad", "total")],
        ws,
        project=lean_project,
        fidelity_n=50,
        write_report=False,
    )
    by = {f.qualname.split(".")[-1]: f for f in report.functions}
    clamp = {p.id: p for p in by["clamp"].properties}
    assert clamp["P1"].verdict is Verdict.PROVEN_CONDITIONAL  # `number`: safe-integer assumption
    assert "safe integer" in clamp["P1"].added_preconditions[0]
    assert by["clamp"].language == "js" and by["clamp"].fidelity and by["clamp"].fidelity.agree
    assert {p.verdict for p in by["bad"].properties} == {Verdict.REFUTED}
    assert {p.verdict for p in by["total"].properties} == {Verdict.PROVEN_UNREVIEWED}  # bigint
    assert report.exit_code == 1


@needs_node
def test_jazzer_campaign_finds_what_refutation_misses(tmp_path: Path) -> None:
    from apex.js.jazzer import JazzerUnavailable, jazzer_bin
    from apex.pipeline import fuzz_target

    try:
        jazzer_bin()
    except JazzerUnavailable:
        pytest.skip("Jazzer.js is not installed (`apex init --js`)")
    path = _ts(
        tmp_path,
        """
        /**
         * @pre n >= 0
         * @post result >= 0
         */
        export function mix(n: number): number {
          let total = 0;
          let m = n;
          while (m > 0) {
            total = (total * 31 + (m % 10)) % 10007;
            m = Math.floor(m / 10);
          }
          if (total === 4242) return -1;
          return total;
        }
        """,
    )
    ws = Workspace.discover(tmp_path)
    (e,) = fuzz_target(f"{path}::mix", ws, budget_s=30, engine="jazzer").properties
    assert e.found is not None and e.found.found_by == "fuzz" and e.engine == "jazzer"
