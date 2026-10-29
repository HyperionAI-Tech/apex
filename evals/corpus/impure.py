"""Out-of-scope functions: I/O, floats, global mutation, randomness, threads."""

import random
import threading

COUNTER = 0


def logged_clamp(x: int, lo: int, hi: int) -> int:
    print("clamping", x)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def mean(xs: list[int]) -> float:
    return sum(xs) / len(xs)


def bump() -> int:
    global COUNTER
    COUNTER += 1
    return COUNTER


def roll(n: int) -> int:
    return random.randint(1, n)


def spawn(n: int) -> int:
    threading.Thread(target=print).start()
    return n
