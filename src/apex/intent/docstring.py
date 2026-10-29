"""Docstring reader: doctests become examples, PEP 316 `pre:`/`post:` lines become formal intent.

    def clamp(x: int, lo: int, hi: int) -> int:
        '''Return x limited to [lo, hi].

        pre: lo <= hi
        post: lo <= __return__ <= hi

        >>> clamp(5, 0, 3)
        3
        '''

`pre:`/`post:` is the convention CrossHair also reads, so a docstring written for one tool works
for both. Prose is left alone here: turning prose into a formal hint takes a model
(`apex intent propose`) or an agent (`apex intent submit`).
"""

import ast
import doctest
import re

from apex.analyze.target import FunctionInfo
from apex.core.intent import Source
from apex.intent.candidates import EMPTY, Candidate, Example, Mined, as_example

_CONTRACT = re.compile(r"^\s*(?P<kind>pre|post)\s*(?:\[[^\]]*\])?\s*:\s*(?P<expr>.+?)\s*$")


def _doc_line(info: FunctionInfo) -> int:
    return info.node.body[0].lineno if info.node.body else info.lineno


def doctest_examples(info: FunctionInfo) -> list[Example]:
    doc = info.docstring
    if not doc:
        return []
    out = []
    for ex in doctest.DocTestParser().get_examples(doc):
        try:
            call = ast.parse(ex.source.strip(), mode="eval").body
            want = ast.parse(ex.want.strip(), mode="eval").body
        except SyntaxError:
            continue
        pair = as_example(ast.Compare(left=call, ops=[ast.Eq()], comparators=[want]), info.qualname)
        if pair is None:
            continue
        loc = f"{info.path.name}:{_doc_line(info) + ex.lineno}"
        out.append(
            Example(
                pair[0],
                pair[1],
                Source(type="docstring", loc=loc),
                f"{ex.source.strip()} == {ex.want.strip()}",
            )
        )
    return out


def contract_lines(info: FunctionInfo) -> list[Candidate]:
    doc = info.docstring
    if not doc:
        return []
    out = []
    for k, line in enumerate(doc.splitlines()):
        m = _CONTRACT.match(line)
        if not m:
            continue
        loc = f"{info.path.name}:{_doc_line(info) + k}"
        expr = m["expr"]
        kind = "precondition" if m["kind"] == "pre" else "property"
        out.append(
            Candidate(
                kind=kind,
                text=f"{m['kind']}: {expr}",
                hint=expr,
                source=Source(type="docstring", loc=loc),
                confidence=1.0,
            )
        )
    return out


def mine_docstring(info: FunctionInfo) -> Mined:
    if not info.docstring:
        return EMPTY
    return Mined(doctest_examples(info), contract_lines(info))
