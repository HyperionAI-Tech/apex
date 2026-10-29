from apex.lean.guard import scan


def tokens(src: str) -> list[str]:
    return [f.token for f in scan(src)]


def test_clean_source_passes() -> None:
    src = "import Apex.Prelude\ntheorem t (x : Int) : x = x := by omega\n"
    assert scan(src) == []


def test_flags_incomplete_proofs_and_axioms() -> None:
    assert tokens("theorem t : False := sorry") == ["sorry"]
    assert tokens("axiom cheat : False") == ["axiom"]
    assert tokens("example : 1 = 1 := by native_decide") == ["native_decide"]


def test_ignores_comments_and_strings() -> None:
    src = '-- sorry\n/- axiom /- nested sorry -/ still comment -/\ndef s := "sorry"\n'
    assert scan(src) == []


def test_identifiers_containing_forbidden_words_are_fine() -> None:
    assert scan("theorem sorry_free : True := trivial\ndef axiomatic := 1") == []
    assert scan("theorem x' : True := trivial\ndef y := 'a'") == []


def test_flags_kernel_skip_and_meta() -> None:
    assert tokens("set_option debug.skipKernelTC true in\ntheorem t : True := trivial") == [
        "set_option debug.skipKernelTC"
    ]
    assert tokens('run_cmd Lean.logInfo "x"') == ["run_cmd"]
    assert tokens("#exit") == ["#exit"]


def test_allows_ordinary_options() -> None:
    assert scan("set_option maxHeartbeats 400000 in\ntheorem t : True := trivial") == []


def test_import_allowlist() -> None:
    assert scan("import Mathlib.Tactic.Ring\nimport Apex.Prelude") == []
    assert tokens("import Lean.Elab.Command") == ["import Lean.Elab.Command"]


def test_positions_are_one_based() -> None:
    [f] = scan("\n  sorry")
    assert (f.line, f.column) == (2, 3)
