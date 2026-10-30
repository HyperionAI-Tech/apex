"""Running real JavaScript from Python: a Node worker behind callables that look like functions.

Refutation, fuzzing and the fidelity bridge call the function under test as `fn(*args)`. For a JS
target they get a `JsCallable`, which sends the call to a long-lived Node worker (`runner.mjs`)
and translates the answer back:

- JS integers come back as `int` (a non-integral or non-finite `number` as `float`), `bigint` as
  `int`, arrays as `list` (`tuple` where the signature says so), `null` / `undefined` as `None`;
- a `number` result outside the safe-integer range raises `OutsideSafeIntegers`: that input is
  outside the assumption `number` proofs are stated under, so the searches skip it with a note;
- a thrown error raises `JsError`; a stack overflow raises `RecursionError`, which the searches
  already treat as a resource limit;
- if the function mutated an argument, the change is written back into the Python argument, so
  the existing mutation checks see it;
- `apex_call(args, timeout_s)` enforces the per-call budget: on timeout the worker is killed (a
  JS loop cannot be interrupted any other way) and restarted on the next call.

Workers are shared per module file and source, so thousands of calls cost one Node start.
"""

import atexit
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from apex.analyze.target import FunctionInfo
from apex.analyze.types import PyType, parse_annotation
from apex.fidelity.bridge import OutsideSafeIntegersError
from apex.intent.hints import JS_INT_OPS

RUNNER = Path(__file__).with_name("runner.mjs")
_SAFE = 2**53 - 1


class JsError(Exception):
    """The JavaScript function threw."""


OutsideSafeIntegers = OutsideSafeIntegersError
"""A `number` result left the safe-integer range: outside what `number` proofs assume."""


class NodeUnavailable(RuntimeError):
    pass


JS_BUILTINS = dict(JS_INT_OPS)


def node_binary() -> str:
    node = shutil.which("node")
    if node is None:
        raise NodeUnavailable("running JavaScript needs Node.js 22.6 or later on PATH")
    return node


class _Worker:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.proc: subprocess.Popen[str] | None = None
        self.exports: list[str] = []

    def _start(self) -> None:
        self.proc = subprocess.Popen(
            [node_binary(), "--no-warnings", str(RUNNER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=self.path.parent,
        )
        reply = self._send({"op": "load", "path": str(self.path.resolve())}, timeout_s=30)
        if not reply.get("ok"):
            self.close()
            raise JsError(f"loading {self.path.name} failed: {reply.get('error')}")
        self.exports = reply["exports"]

    def request(self, req: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        return self._send(req, timeout_s)

    def _send(self, req: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout_s
        while True:
            left = deadline - time.monotonic()
            ready, _, _ = select.select([self.proc.stdout], [], [], max(left, 0))
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    self.close()
                    raise JsError("the Node worker exited")
                return json.loads(line)
            if left <= 0:
                self.close()
                raise TimeoutError

    def close(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


_WORKERS: dict[tuple[str, int], _Worker] = {}


def _worker(path: Path) -> _Worker:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns ^ stat.st_size)
    if key not in _WORKERS:
        for k in [k for k in _WORKERS if k[0] == key[0]]:  # the file changed: drop the old one
            _WORKERS.pop(k).close()
        _WORKERS[key] = _Worker(path)
    return _WORKERS[key]


@atexit.register
def _close_all() -> None:
    for w in _WORKERS.values():
        w.close()


def _kind(ty: PyType | None, numeric: str) -> dict[str, Any] | None:
    if ty is None:
        return None
    if ty.kind == "int":
        return {"k": numeric}
    if ty.kind in ("list", "tuple", "option"):
        return {"k": ty.kind, "a": [_kind(a, numeric) for a in ty.args]}
    return {"k": ty.kind}


def _encode(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v if abs(v) <= _SAFE else {"$int": str(v)}
    if isinstance(v, list | tuple):
        return [_encode(x) for x in v]
    return v


def _decode(v: Any, ty: PyType | None) -> Any:
    if isinstance(v, dict):
        if "$big" in v:
            return int(v["$big"])
        if "$float" in v:
            return float(v["$float"])
        return v.get("$other")
    if isinstance(v, list):
        if ty is not None and ty.kind == "tuple":
            return tuple(_decode(x, t) for x, t in zip(v, ty.args, strict=False))
        inner = ty.args[0] if ty is not None and ty.kind == "list" else None
        return [_decode(x, inner) for x in v]
    if ty is not None and ty.kind == "option" and v is not None:
        return _decode(v, ty.args[0])
    return v


class JsCallable:
    """A JavaScript function, callable from Python."""

    def __init__(self, path: Path, name: str, info: FunctionInfo | None) -> None:
        self.path = path
        self.__name__ = name
        self.params: list[PyType | None] = []
        self.returns: PyType | None = None
        number = "bigint" if info is not None and info.numeric == {"bigint"} else "number"
        self.number = number
        if info is not None:
            for a in info.node.args.args:
                self.params.append(_safe_type(a.annotation))
            self.returns = _safe_type(info.node.returns)

    def __call__(self, *args: Any) -> Any:
        from apex.fidelity.bridge import CALL_TIMEOUT_S

        return self.apex_call(args, CALL_TIMEOUT_S)

    def apex_call(self, args: tuple[Any, ...], timeout_s: float) -> Any:
        from apex.fidelity.bridge import CallTimeout

        kinds = [_kind(t, self.number) for t in self.params] or None
        req = {
            "op": "call",
            "fn": self.__name__,
            "args": [_encode(a) for a in args],
            "kinds": kinds,
        }
        try:
            reply = _worker(self.path).request(req, timeout_s)
        except TimeoutError:
            raise CallTimeout from None
        if not reply.get("ok"):
            if reply.get("kind") == "recursion":
                raise RecursionError(reply.get("error"))
            raise JsError(reply.get("error") or "the JavaScript function threw")
        if reply.get("after") is not None:
            for arg, after in zip(args, reply["after"], strict=False):
                if isinstance(arg, list):
                    arg[:] = _decode(after, None)
        if reply.get("unsafe") and self.number == "number":
            raise OutsideSafeIntegers
        return _decode(reply.get("value"), self.returns)


def _safe_type(ann: Any) -> PyType | None:
    try:
        return parse_annotation(ann)
    except Exception:
        return None


def load_js_module(info: FunctionInfo) -> SimpleNamespace:
    """The module's functions as JsCallables (plus the helpers hints may use), like a Python
    module object for refutation, fuzzing and the fidelity bridge."""
    from apex.js.target import load_js_function

    path = Path(info.path)
    ns: dict[str, Any] = dict(JS_BUILTINS)
    worker = _worker(path)
    if worker.proc is None:
        worker.request({"op": "load", "path": str(path.resolve())}, timeout_s=30)
    names = {f.qualname for f in [info, *info.callees]} | set(worker.exports)
    for name in names:
        try:
            fi = info if name == info.qualname else load_js_function(path, name)
        except Exception:
            fi = None
        ns[name] = JsCallable(path, name, fi)
    return SimpleNamespace(**ns, __apex_exports__=list(worker.exports))


def exported(info: FunctionInfo) -> bool:
    if os.environ.get("APEX_JS_ASSUME_EXPORTED"):
        return True
    return info.exported
