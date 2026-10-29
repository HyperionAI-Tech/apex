from conflicts import double


def test_double() -> None:
    assert double(2) == 5  # contradicts the docstring
