import ast
import textwrap
from pathlib import Path

import pytest

from apex.analyze.purity import classify
from apex.analyze.target import TargetError, load_function
from apex.core.verdict import ReasonCode
from apex.translate.lean import TranslationError, translate


def _write(tmp_path: Path, src: str, name: str = "m.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(src))
    return path


def model(tmp_path: Path, src: str, fn: str = "f") -> str:
    return translate(load_function(f"{_write(tmp_path, src)}::{fn}")).text


def gap(tmp_path: Path, src: str, fn: str = "f") -> TranslationError:
    with pytest.raises(TranslationError) as e:
        translate(load_function(f"{_write(tmp_path, src)}::{fn}"))
    return e.value


def test_if_elif_chain(tmp_path: Path) -> None:
    out = model(
        tmp_path,
        """
        def f(x: int, lo: int, hi: int) -> int:
            if x < lo:
                return lo
            elif x > hi:
                return hi
            return x
    """,
    )
    assert "def f (x : Int) (lo : Int) (hi : Int) : Int :=" in out
    assert "if (x < lo) then" in out and "if (x > hi) then" in out


def test_python_floor_semantics(tmp_path: Path) -> None:
    out = model(tmp_path, "def f(a: int, b: int) -> int:\n    return a // b + a % b\n")
    assert "Apex.pyFloorDiv a b" in out and "Apex.pyMod a b" in out


def test_for_loop_becomes_fold(tmp_path: Path) -> None:
    out = model(
        tmp_path,
        """
        def f(xs: list[int]) -> int:
            total = 0
            for x in xs:
                total += x
            return total
    """,
    )
    assert "xs.foldl (fun total x =>" in out


def test_multi_variable_loop_state_is_a_tuple(tmp_path: Path) -> None:
    out = model(
        tmp_path,
        """
        def f(xs: list[int]) -> int:
            lo = 0
            hi = 0
            for x in xs:
                lo = min(lo, x)
                hi = max(hi, x)
            return hi - lo
    """,
    )
    assert "let (lo, hi) := xs.foldl (fun (lo, hi) x =>" in out


def test_optional_return_wraps_some(tmp_path: Path) -> None:
    out = model(
        tmp_path,
        """
        def f(a: int, b: int) -> int | None:
            if b == 0:
                return None
            return a // b
    """,
    )
    assert "none" in out and "(some (Apex.pyFloorDiv a b))" in out


def test_bool_results_use_decide(tmp_path: Path) -> None:
    out = model(tmp_path, "def f(x: int) -> bool:\n    return x > 0 and x < 10\n")
    assert "(decide ((x > (0 : Int)) ∧ (x < (10 : Int))))" in out


def test_callees_are_translated_first(tmp_path: Path) -> None:
    out = model(
        tmp_path,
        """
        def helper(x: int) -> int:
            return x + 1

        def f(x: int) -> int:
            return helper(x) * 2
    """,
    )
    assert out.index("def helper") < out.index("def f")


def test_lean_keywords_are_escaped(tmp_path: Path) -> None:
    out = model(tmp_path, "def f(fun: int) -> int:\n    return fun\n")
    assert "(«fun» : Int)" in out


@pytest.mark.parametrize(
    ("src", "code"),
    [
        (
            "def f(x: int) -> int:\n    while x > 0:\n        x -= 1\n    return x\n",
            ReasonCode.NONTERMINATION,
        ),
        ("def f(x: int) -> int:\n    return x / 2\n", ReasonCode.FLOAT_SEMANTICS),
        ("def f(x: float) -> float:\n    return x\n", ReasonCode.FLOAT_SEMANTICS),
        ("def f(xs: list[int]) -> int:\n    return xs[0]\n", ReasonCode.UNSUPPORTED_CONSTRUCT),
        (
            "def f(x: int) -> int:\n    if x:\n        return 1\n    return 0\n",
            ReasonCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            "def f(x: int) -> int:\n    if x > 0:\n        return 1\n",
            ReasonCode.UNSUPPORTED_CONSTRUCT,
        ),
        ("def f(x):\n    return x\n", ReasonCode.UNSUPPORTED_CONSTRUCT),
    ],
)
def test_unsupported_constructs_are_never_guessed(
    tmp_path: Path, src: str, code: ReasonCode
) -> None:
    assert gap(tmp_path, src).reason_code is code


def test_purity_classifier(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        import random

        def noisy(x: int) -> int:
            print(x)
            return x + 1

        def mutates(xs: list[int]) -> int:
            xs.append(1)
            return len(xs)

        def rolls(n: int) -> int:
            return random.randint(1, n)

        def clean(x: int) -> int:
            return x + 1
    """,
    )

    def cls(fn: str):
        info = load_function(f"{path}::{fn}")
        return classify(info.node, info.module_tree)

    noisy = cls("noisy")
    assert noisy.class_ == "impure" and noisy.pure_core is not None
    assert "print" not in noisy.pure_core and "return x + 1" in noisy.pure_core
    assert cls("mutates").class_ == "impure"
    assert cls("rolls").findings[0].reason_code is ReasonCode.IMPURE_IO
    assert cls("clean").class_ == "pure"


def test_function_hash_covers_callees(tmp_path: Path) -> None:
    src = "def g(x: int) -> int:\n    return x\n\ndef f(x: int) -> int:\n    return g(x)\n"
    path = _write(tmp_path, src)
    before = load_function(f"{path}::f").sha256
    path.write_text(src.replace("return x\n", "return x + 1\n", 1))
    assert load_function(f"{path}::f").sha256 != before


def test_bad_targets(tmp_path: Path) -> None:
    path = _write(tmp_path, "def f() -> int:\n    return 1\n")
    for bad in [str(path), f"{path}::g", f"{path}::C.m", "missing.py::f"]:
        with pytest.raises(TargetError):
            load_function(bad)
    assert ast.parse(path.read_text())
