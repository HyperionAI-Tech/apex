from pathlib import Path

import pytest

from apex.analyze.target import load_function
from apex.analyze.types import BOOL, INT, PyType, list_of, option_of
from apex.fidelity import bridge
from apex.fidelity.bridge import generate_inputs
from apex.translate.lean import translate
from apex.translate.literals import normalize, to_lean


def test_to_lean_literals() -> None:
    assert to_lean(-3, INT) == "(-3 : Int)"
    assert to_lean([1, 2], list_of(INT)) == "([(1 : Int), (2 : Int)] : (List Int))"
    assert to_lean(None, option_of(INT)) == "none"
    assert to_lean(4, option_of(INT)) == "(some (4 : Int))"
    assert to_lean((1, True), PyType("tuple", (INT, BOOL))) == "((1 : Int), true)"


def test_normalize_distinguishes_bool_from_int_and_nests_tuples() -> None:
    assert normalize(True) != normalize(1)
    assert normalize((1, 2, 3)) == normalize((1, (2, 3)))


def test_generate_inputs_is_deterministic() -> None:
    a = generate_inputs([INT, list_of(INT)], 50)
    b = generate_inputs([INT, list_of(INT)], 50)
    assert a == b and len(a) > 10


def test_inputs_past_the_recursion_limit_are_skipped_not_disagreements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    py = tmp_path / "m.py"
    py.write_text(
        "def _down(k):\n    return 0 if k <= 0 else _down(k - 1)\n\n\n"
        "def g(x: int) -> int:\n    if x > 10:\n        _down(100000)\n    return x\n"
    )
    ident = tmp_path / "ident.py"
    ident.write_text("def g(x: int) -> int:\n    return x\n")
    model = translate(load_function(f"{ident}::g"))
    monkeypatch.setattr(bridge, "run_lean_model", lambda m, inputs, p, t: [a[0] for a in inputs])
    summary = bridge.check_fidelity(load_function(f"{py}::g"), model, project=object(), n=50)  # type: ignore[arg-type]
    assert summary.agree and summary.skipped > 0 and summary.inputs_checked > 0


def test_slow_inputs_are_skipped_smallest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    py = tmp_path / "m.py"
    py.write_text(
        "def g(x: int) -> int:\n    t = 0\n    for _ in range(abs(x)):\n        t += 1\n"
        "    return x\n"
    )
    ident = tmp_path / "ident.py"
    ident.write_text("def g(x: int) -> int:\n    return x\n")
    model = translate(load_function(f"{ident}::g"))
    monkeypatch.setattr(bridge, "run_lean_model", lambda m, inputs, p, t: [a[0] for a in inputs])
    summary = bridge.check_fidelity(load_function(f"{py}::g"), model, project=object(), n=50)  # type: ignore[arg-type]
    assert summary.agree and summary.skipped > 0 and summary.inputs_checked > 0
