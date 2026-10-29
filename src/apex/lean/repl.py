"""Warm REPL pool for the fast loop: tactic tiers and fixer iterations.

NEVER a verdict source. The REPL reuses elaborated state, which is what makes iteration fast and
also what you do not want behind a trust claim. Anything that looks proved here still has to go
through `apex.lean.compiler.compile_and_audit` before Apex may call it proven.
"""

import queue
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from apex.lean.compiler import LeanMessage, extract_goal
from apex.lean.project import LeanProject, find_lake

DEFAULT_HEADER = "import Apex.Prelude"


@dataclass
class ReplResult:
    ok: bool
    """No error messages. A fast-loop signal only; it does not mean proven."""
    messages: list[LeanMessage] = field(default_factory=list)
    has_sorry: bool = False
    duration_s: float = 0.0


class ReplPool:
    """A fixed pool of Lean REPL processes, each holding a pre-imported base environment."""

    def __init__(
        self,
        size: int = 2,
        *,
        project: LeanProject | None = None,
        header: str = DEFAULT_HEADER,
        memory_limit_mb: int | None = 8192,
    ) -> None:
        try:
            from lean_interact import AutoLeanServer, Command, LeanREPLConfig, LocalProject
        except ImportError as e:  # optional extra
            raise RuntimeError(
                "REPL pool needs the `repl` extra: uv pip install 'apex-verify[repl]'"
            ) from e

        self._Command = Command
        project = project or LeanProject.locate()
        lake = find_lake()
        config = LeanREPLConfig(
            project=LocalProject(directory=str(project.root), auto_build=False),
            lake_path=str(lake) if lake else "lake",
            memory_hard_limit_mb=memory_limit_mb,
        )
        self._free: queue.Queue[tuple[object, int]] = queue.Queue()
        self.warmup_s: list[float] = []
        for _ in range(size):
            started = time.monotonic()
            server = AutoLeanServer(config)
            resp = server.run(Command(cmd=header), timeout=600)
            env = getattr(resp, "env", None)
            if env is None:
                raise RuntimeError(f"REPL failed to load `{header}`: {resp}")
            self.warmup_s.append(time.monotonic() - started)
            self._free.put((server, env))

    @contextmanager
    def _lease(self) -> Iterator[tuple[object, int]]:
        item = self._free.get()
        try:
            yield item
        finally:
            self._free.put(item)

    def check(self, snippet: str, *, timeout_s: float = 60.0) -> ReplResult:
        """Elaborate `snippet` on top of the base environment, discarding the resulting state."""
        with self._lease() as (server, env):
            started = time.monotonic()
            resp = server.run(self._Command(cmd=snippet, env=env), timeout=timeout_s)  # type: ignore[attr-defined]
            duration = time.monotonic() - started
        raw_messages = getattr(resp, "messages", None)
        if raw_messages is None:
            msg = LeanMessage(
                severity="error", line=0, column=0, message=getattr(resp, "message", str(resp))
            )
            return ReplResult(ok=False, messages=[msg], duration_s=duration)
        messages = [
            LeanMessage(
                severity="information" if m.severity in ("info", "trace") else m.severity,
                line=m.start_pos.line,
                column=m.start_pos.column,
                end_line=m.end_pos.line if m.end_pos else None,
                end_column=m.end_pos.column if m.end_pos else None,
                message=m.data,
                goal=extract_goal(m.data),
            )
            for m in raw_messages
        ]
        has_sorry = bool(getattr(resp, "sorries", None))
        ok = not any(m.severity == "error" for m in messages) and not has_sorry
        return ReplResult(ok=ok, messages=messages, has_sorry=has_sorry, duration_s=duration)

    def close(self) -> None:
        while not self._free.empty():
            server, _ = self._free.get_nowait()
            server.kill()  # type: ignore[attr-defined]
