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


def read_limit(path: str) -> int:
    with open(path) as f:
        return int(f.read())


def to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9


def greet(name: str) -> str:
    return "hello " + name


def push(xs: list[int], x: int) -> list[int]:
    xs.append(x)
    return xs


def stamp(x: int) -> int:
    import time

    return x + int(time.time())
