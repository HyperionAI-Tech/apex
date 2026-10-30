"""Refutation on the real Python, and the LLM provider plumbing (no network, no Lean)."""

import json
import os
import stat
import textwrap
import time
from pathlib import Path

import pytest

from apex.analyze.purity import classify
from apex.analyze.target import load_function
from apex.core.config import ApexConfig
from apex.fidelity.bridge import CallTimeout, call_user
from apex.fidelity.refute import refute
from apex.intent.docstring import mine_docstring
from apex.intent.fuse import fuse
from apex.intent.source import mine_source
from apex.llm.providers import ClaudeCodeProvider, CodexProvider, ProviderError, get_provider
from apex.translate.lean import signature


def _refute(tmp_path: Path, src: str, fn: str, **kw):
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(src))
    info = load_function(f"{path}::{fn}")
    sig = signature(info.node)
    intent, examples = fuse(
        info,
        classify(info.node, info.module_tree),
        sig.params,
        sig.returns,
        mine_docstring(info) + mine_source(info),
    )
    return {p.id: p for p in refute(info, intent, sig.params, examples, **kw).properties}


def test_hypothesis_finds_and_python_replays(tmp_path: Path) -> None:
    res = _refute(
        tmp_path,
        '''
        def clamp(x: int, lo: int, hi: int) -> int:
            """post: lo <= __return__ <= hi"""
            assert lo <= hi
            if x < lo:
                return lo
            if x >= hi:
                return hi - 1
            return x
        ''',
        "clamp",
        budget_s=2,
    )
    cx = res["P1"].counterexample
    assert res["P1"].refuted and cx is not None and cx.found_by == "hypothesis"
    assert int(cx.inputs["lo"]) <= int(cx.inputs["hi"])  # inside the precondition


def test_crosshair_finds_what_random_testing_misses(tmp_path: Path) -> None:
    res = _refute(
        tmp_path,
        '''
        def sq(x: int) -> int:
            """post: __return__ >= 0"""
            if x == 104729:
                return -1
            return x * x
        ''',
        "sq",
        budget_s=5,
    )
    cx = res["P1"].counterexample
    assert cx is not None and cx.found_by == "crosshair" and cx.inputs == {"x": "104729"}


def test_correct_function_is_not_refuted(tmp_path: Path) -> None:
    res = _refute(
        tmp_path,
        '''
        def my_abs(x: int) -> int:
            """post: __return__ >= 0

            >>> my_abs(-3)
            3
            """
            if x < 0:
                return -x
            return x
        ''',
        "my_abs",
        budget_s=1,
    )
    assert not res["P1"].refuted and res["P1"].searched_by == ["hypothesis", "crosshair"]
    assert not res["E1"].refuted and res["E1"].searched_by == ["example"]


def test_exception_inside_the_precondition_refutes(tmp_path: Path) -> None:
    res = _refute(
        tmp_path,
        '''
        def div(a: int, b: int) -> int:
            """post: __return__ * b <= a or b < 0"""
            return a // b
        ''',
        "div",
        budget_s=1,
    )
    cx = res["P1"].counterexample
    assert cx is not None and "ZeroDivisionError" in (cx.actual or "")


def test_recursion_limit_is_skipped_not_refuted_and_never_crashes(tmp_path: Path) -> None:
    # Whether fact(~1000) overflows depends on how deep in the stack Hypothesis calls it, which
    # used to surface as a FlakyFailure that crashed the run.
    src = '''
        def fact(n: int) -> int:
            """post: __return__ >= 1"""
            if n <= 0:
                return 1
            return n * fact(n - 1)
        '''
    first = _refute(tmp_path, src, "fact", budget_s=1)["P1"]
    assert not first.refuted and "recursion limit" in (first.note or "")
    assert _refute(tmp_path, src, "fact", budget_s=1)["P1"] == first


def test_slow_calls_are_skipped_and_the_search_stops(tmp_path: Path) -> None:
    started = time.monotonic()
    res = _refute(
        tmp_path,
        """
        def fact(n: int) -> int:
            \"\"\"post: __return__ >= 1\"\"\"
            r = 1
            for i in range(1, n + 1):
                r *= i
            return r
        """,
        "fact",
        budget_s=1,
    )
    assert not res["P1"].refuted and "ran past" in (res["P1"].note or "")
    assert time.monotonic() - started < 30  # used to loop on n near 2**40 forever


def test_user_code_cannot_swallow_the_timeout() -> None:
    def stubborn() -> int:
        while True:
            try:
                while True:
                    pass
            except Exception:
                continue

    with pytest.raises(CallTimeout):
        call_user(stubborn, (), timeout_s=0.2)


# ---- providers -------------------------------------------------------------------------------


def _fake_cli(tmp_path: Path, name: str, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


ANSWER = {"properties": [], "preconditions": []}


def test_claude_code_provider_reads_structured_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(
        tmp_path,
        "claude",
        f"""
        import json, sys
        args = sys.argv[1:]
        assert args[0] == "-p" and args[args.index("--tools") + 1] == ""
        assert json.loads(args[args.index("--json-schema") + 1])["type"] == "object"
        sys.stdin.read()
        print(json.dumps({{"is_error": False, "structured_output": {ANSWER!r}}}))
        """,
        monkeypatch,
    )
    out = ClaudeCodeProvider(None, 30).complete_json("sys", "prompt", {"type": "object"})
    assert out == ANSWER


def test_codex_provider_reads_the_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_cli(
        tmp_path,
        "codex",
        f"""
        import json, sys
        args = sys.argv[1:]
        assert args[0] == "exec" and args[args.index("--sandbox") + 1] == "read-only"
        assert sys.stdin.read().startswith("sys")
        open(args[args.index("-o") + 1], "w").write(json.dumps({ANSWER!r}))
        """,
        monkeypatch,
    )
    out = CodexProvider(None, 30).complete_json("sys", "prompt", {"type": "object"})
    assert out == ANSWER


def test_cli_failure_is_a_provider_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_cli(
        tmp_path,
        "claude",
        "import sys\nprint('not logged in', file=sys.stderr)\nsys.exit(1)\n",
        monkeypatch,
    )
    with pytest.raises(ProviderError, match="not logged in"):
        ClaudeCodeProvider(None, 30).complete_json("s", "p", {})


@pytest.mark.parametrize("provider", ["none", "host"])
def test_no_provider_explains_the_alternatives(provider: str) -> None:
    with pytest.raises(ProviderError, match="intent submit"):
        get_provider(ApexConfig(model_provider=provider))  # type: ignore[arg-type]


def test_openai_needs_an_explicit_model() -> None:
    with pytest.raises(ProviderError, match="model"):
        get_provider(ApexConfig(model_provider="openai"))


def test_proposal_schema_is_exported(tmp_path: Path) -> None:
    from apex.core.schema import export_schemas

    names = {p.name for p in export_schemas(tmp_path)}
    assert {"proposal_set.schema.json", "refute_result.schema.json"} <= names
    schema = json.loads((tmp_path / "proposal_set.schema.json").read_text())
    assert "properties" in schema["properties"]
