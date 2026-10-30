"""Two threads, each running racy_worker(counter, 2): N = 2, K = 2 in Spec.cfg."""

import threading

from workers import Counter, racy_worker

N, K = 2, 2


def setup() -> Counter:
    return Counter()


def run(c: Counter) -> None:
    threads = [threading.Thread(target=racy_worker, args=(c, K)) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def observe(c: Counter) -> dict:
    return {"count": c.value}


def check_NoLostUpdate(obs: dict, done: bool) -> bool:
    return not done or obs["count"] == N * K


def check_NeverAhead(obs: dict, done: bool) -> bool:
    return obs["count"] <= N * K
