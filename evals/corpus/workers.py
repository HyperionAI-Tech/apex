"""Concurrent code Lean cannot model, checked against TLA+ specs (bundles in tla/<function>/)."""

import threading


class Counter:
    def __init__(self) -> None:
        self.value = 0
        self.lock = threading.Lock()


def racy_worker(c: Counter, k: int) -> None:
    """Add k to the shared counter. BUG: the read and the write are not atomic."""
    for _ in range(k):
        v = c.value
        c.value = v + 1


def locked_worker(c: Counter, k: int) -> None:
    """Add k to the shared counter, one locked increment at a time."""
    for _ in range(k):
        with c.lock:
            c.value += 1


def double_worker(c: Counter, k: int) -> None:
    """Add k to the shared counter. Its spec says +1 per step; the code adds 2."""
    for _ in range(k):
        with c.lock:
            c.value += 2


def jittery_worker(c: Counter, k: int) -> None:
    """Add k to the shared counter, but each step dips by one before adding two: never wrong at
    the end, yet a behavior its spec (+1 per step) does not allow."""
    for _ in range(k):
        with c.lock:
            c.value -= 1
            c.value += 2
