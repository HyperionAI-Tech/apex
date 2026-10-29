"""Spec/code conflicts: intent sources that disagree with each other."""


def double(x: int) -> int:
    """Twice x.

    >>> double(2)
    4
    """
    return x + x
