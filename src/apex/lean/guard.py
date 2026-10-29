"""Static screen over Lean source before it reaches the verdict compiler.

This is defense in depth, not the trust anchor: `#print axioms` already catches `sorry`, user
axioms and `native_decide`. The guard exists for the holes the axiom audit cannot see:

- `set_option debug.skipKernelTC` (the kernel never checks the proof at all)
- metaprogramming that could forge the audit's own output (`run_cmd logInfo ...`, `macro`)
- imports outside the pinned libraries, which would pull in arbitrary metaprograms

and to give clear, early error messages for the obvious cases.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel

# Imports must come from the pinned project. Anything else could smuggle in metaprograms.
ALLOWED_IMPORT_ROOTS = frozenset({"Init", "Std", "Mathlib", "Batteries", "Aesop", "Apex"})

FORBIDDEN_TOKENS: dict[str, str] = {
    "sorry": "incomplete proof",
    "admit": "incomplete proof",
    "axiom": "user axioms are never allowed in proven results",
    "native_decide": "trusts the compiler, not the kernel (adds Lean.ofReduceBool)",
    "Lean.ofReduceBool": "trusts the compiler, not the kernel",
    "Lean.trustCompiler": "trusts the compiler, not the kernel",
    "unsafe": "unsafe definitions bypass the kernel",
    "implemented_by": "replaces kernel-checked code with unchecked code",
    "extern": "replaces kernel-checked code with unchecked code",
    "#exit": "truncates the file before the axiom audit",
    "#eval": "runs arbitrary code during elaboration",
    "run_cmd": "metaprogramming can forge verifier output",
    "run_elab": "metaprogramming can forge verifier output",
    "run_meta": "metaprogramming can forge verifier output",
    "run_tac": "metaprogramming can forge verifier output",
    "elab": "custom elaborators can forge verifier output",
    "elab_rules": "custom elaborators can forge verifier output",
    "macro": "custom macros can shadow verifier commands",
    "macro_rules": "custom macros can shadow verifier commands",
    "syntax": "custom syntax can shadow verifier commands",
    "initialize": "runs arbitrary code at import time",
    "builtin_initialize": "runs arbitrary code at import time",
}


class GuardFinding(BaseModel):
    token: str
    line: int
    column: int
    reason: str


@dataclass
class _Token:
    text: str
    line: int
    column: int


_IDENT_START = re.compile(r"[A-Za-z_#Ͱ-Ͽἀ-῿℀-⅏]")
_IDENT_CHAR = re.compile(r"[A-Za-z0-9_'!?.Ͱ-Ͽἀ-῿₀-₏℀-⅏]")


def _tokens(source: str) -> list[_Token]:
    """Identifier-like tokens with comments and string/char literals removed.

    Handles nested `/- -/` block comments, `--` line comments, string escapes, and the fact that
    `'` is both a char-literal delimiter and a legal identifier character (`x'`).
    """
    out: list[_Token] = []
    i, n = 0, len(source)
    line, col = 1, 1

    def advance(k: int) -> None:
        nonlocal i, line, col
        for _ in range(k):
            if i < n and source[i] == "\n":
                line, col = line + 1, 1
            else:
                col += 1
            i += 1

    while i < n:
        c = source[i]
        if source.startswith("--", i):
            while i < n and source[i] != "\n":
                advance(1)
        elif source.startswith("/-", i):
            depth = 0
            while i < n:
                if source.startswith("/-", i):
                    depth += 1
                    advance(2)
                elif source.startswith("-/", i):
                    depth -= 1
                    advance(2)
                    if depth == 0:
                        break
                else:
                    advance(1)
        elif c == '"':
            advance(1)
            while i < n and source[i] != '"':
                advance(2 if source[i] == "\\" else 1)
            advance(1)
        elif c == "'" and (i + 2 < n) and (source[i + 2] == "'" or source[i + 1] == "\\"):
            # Char literal like 'a' or '\n'. Identifier primes are consumed inside identifiers.
            advance(1)
            while i < n and source[i] != "'":
                advance(2 if source[i] == "\\" else 1)
            advance(1)
        elif c == "«":
            start_line, start_col, j = line, col, i
            while i < n and source[i] != "»":
                advance(1)
            advance(1)
            out.append(_Token(source[j + 1 : i - 1], start_line, start_col))
        elif _IDENT_START.match(c):
            start_line, start_col, j = line, col, i
            advance(1)
            while i < n and _IDENT_CHAR.match(source[i]):
                advance(1)
            out.append(_Token(source[j:i].rstrip("."), start_line, start_col))
        else:
            advance(1)
    return out


def scan(source: str) -> list[GuardFinding]:
    findings: list[GuardFinding] = []
    toks = _tokens(source)
    for k, tok in enumerate(toks):
        nxt = toks[k + 1].text if k + 1 < len(toks) else ""
        if tok.text in FORBIDDEN_TOKENS:
            findings.append(
                GuardFinding(
                    token=tok.text,
                    line=tok.line,
                    column=tok.column,
                    reason=FORBIDDEN_TOKENS[tok.text],
                )
            )
        elif tok.text == "set_option" and nxt.startswith("debug."):
            findings.append(
                GuardFinding(
                    token=f"set_option {nxt}",
                    line=tok.line,
                    column=tok.column,
                    reason="debug options can disable kernel checking",
                )
            )
        elif tok.text == "import":
            root = nxt.split(".", 1)[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                findings.append(
                    GuardFinding(
                        token=f"import {nxt}",
                        line=tok.line,
                        column=tok.column,
                        reason="only pinned libraries may be imported",
                    )
                )
    return findings
