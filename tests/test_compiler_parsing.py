import json

from apex.lean.compiler import (
    FOOTER_MARKER,
    build_file,
    extract_goal,
    parse_axiom_message,
    parse_lean_json,
)


def test_build_file_reports_footer_line() -> None:
    text, footer = build_file("theorem a : True := trivial\n\n\n", ["a", "b"])
    lines = text.splitlines()
    assert lines[footer - 1] == FOOTER_MARKER
    assert lines[footer] == "#print axioms a"
    assert lines[footer + 1] == "#print axioms b"


def test_parse_axiom_messages() -> None:
    assert parse_axiom_message("'t' does not depend on any axioms") == ("t", [])
    assert parse_axiom_message(
        "'Ns.t' depends on axioms: [propext, Classical.choice,\n Quot.sound]"
    ) == (
        "Ns.t",
        ["propext", "Classical.choice", "Quot.sound"],
    )
    assert parse_axiom_message("'t' depends on axioms: [sorryAx]") == ("t", ["sorryAx"])
    assert parse_axiom_message("something else") is None


def test_extract_goal() -> None:
    assert extract_goal("unsolved goals\nx : Int\n⊢ x = x") == "x : Int\n⊢ x = x"
    assert extract_goal("unknown identifier 'foo'") is None


def test_parse_lean_json_lines() -> None:
    line = json.dumps(
        {
            "severity": "error",
            "pos": {"line": 3, "column": 2},
            "endPos": {"line": 3, "column": 7},
            "fileName": "f.lean",
            "data": "unsolved goals\n⊢ False",
            "caption": "",
        }
    )
    [(msg, raw)] = parse_lean_json("noise\n" + line + "\n")
    assert (msg.severity, msg.line, msg.column, msg.end_column) == ("error", 3, 2, 7)
    assert msg.goal == "⊢ False"
    assert raw == "unsolved goals\n⊢ False"
