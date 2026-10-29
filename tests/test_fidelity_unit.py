from apex.analyze.types import BOOL, INT, PyType, list_of, option_of
from apex.fidelity.bridge import generate_inputs
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
