"""Source-only intent: what the code itself states. Leading `assert`s are preconditions.

Anything beyond that (what the function is *for*) cannot be read off the code without assuming the
code is right, so it comes from a model as low-confidence `inferred` proposals, never from here.
"""

import ast

from apex.analyze.target import FunctionInfo
from apex.core.intent import Source
from apex.intent.candidates import Candidate, Mined
from apex.translate.lean import leading_asserts


def mine_source(info: FunctionInfo) -> Mined:
    out = []
    for a in leading_asserts(info.node):
        expr = ast.unparse(a.test)
        out.append(
            Candidate(
                kind="precondition",
                text=f"assert {expr}",
                hint=expr,
                source=Source(type="source", loc=f"{info.path.name}:{a.lineno}"),
                confidence=1.0,
            )
        )
    return Mined([], out)
