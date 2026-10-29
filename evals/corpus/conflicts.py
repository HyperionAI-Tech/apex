"""Spec/code conflicts: intent sources that disagree with each other."""


def double(x: int) -> int:
    """Twice x.

    >>> double(2)
    4
    """
    return x + x


def half(x: int) -> int:
    """Half of x, rounded down.

    post: __return__ * 2 <= x
    """
    return x // 2


def inc(x: int) -> int:
    """One more than x.

    >>> inc(1)
    3
    """
    return x + 1


def neg(x: int) -> int:
    """The negation of x.

    >>> neg(-3)
    3
    """
    return -x
