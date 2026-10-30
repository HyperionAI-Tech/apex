"""English spec reader.

A spec is a Markdown or plain-text file. Every list item is one requirement. An item may end with a
formal hint in backticks, in which case it is formal intent written by a person:

    ## clamp
    - Requires: lo <= hi                                   `lo <= hi`
    - The result always lies between lo and hi.            `lo <= r <= hi`
    - Values already inside the range come back unchanged. `lo <= x <= hi -> r == x`
    - Never allocates.

An item without a hint is still intent, just not machine-checkable yet: it enters the Intent Model
as a text-only property below the confidence floor (`SPEC_AMBIGUOUS`) until a model or an agent
formalizes it (`apex intent propose` / `apex intent submit`), and the human approves the result.

If headings name functions, only the section whose heading names the target is read.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from apex.core.intent import Source
from apex.intent.candidates import Candidate, Mined
from apex.intent.hints import HintError, parse_hint

FORMAL_CONFIDENCE = 0.9
"""A person wrote both the sentence and the hint."""
TEXT_ONLY_CONFIDENCE = 0.5
"""Below the default floor (0.6): an unformalized sentence cannot be proven or refuted."""

_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<body>.+?)\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*$")
_TRAILING_HINT = re.compile(r"`(?P<hint>[^`]+)`\s*$")
_PRE = re.compile(r"^(?:requires|precondition|pre|assumes?)\s*:\s*", re.I)


@dataclass
class SpecItem:
    id: str
    """S1, S2, ... in file order; a model's formalization refers back to it."""
    text: str
    hint: str | None
    is_precondition: bool
    line: int


def _section(lines: list[str], qualname: str) -> list[tuple[int, str]]:
    headings = [(k, m["title"]) for k, line in enumerate(lines) if (m := _HEADING.match(line))]
    if not headings:
        return list(enumerate(lines))
    word = re.compile(rf"(?<![\w.]){re.escape(qualname)}(?!\w)")
    mine = [k for k, title in headings if word.search(title.replace("`", ""))]
    if not mine:
        return []
    start = mine[0]
    level = len(lines[start]) - len(lines[start].lstrip().lstrip("#"))
    end = next(
        (
            k
            for k, _ in headings
            if k > start and len(lines[k]) - len(lines[k].lstrip().lstrip("#")) <= level
        ),
        len(lines),
    )
    return [(k, lines[k]) for k in range(start + 1, end)]


def spec_items(text: str, qualname: str) -> list[SpecItem]:
    items = []
    for k, line in _section(text.splitlines(), qualname):
        m = _ITEM.match(line)
        if not m:
            continue
        body = m["body"]
        hint = None
        if hm := _TRAILING_HINT.search(body):
            hint = hm["hint"].strip()
            body = body[: hm.start()].rstrip(" :—-")
        pre = bool(_PRE.match(body))
        body = _PRE.sub("", body) if pre else body
        items.append(SpecItem(f"S{len(items) + 1}", body or (hint or ""), hint, pre, k + 1))
    return items


def mine_english(
    path: Path,
    qualname: str,
    params: list[str],
    helpers: set[str],
    convert: Callable[[str], str] | None = None,
) -> tuple[Mined, list[SpecItem]]:
    """`convert`: how hints in this spec's language become internal hints (JavaScript specs
    write `lo <= r && r <= hi`; Python specs need none)."""
    items = spec_items(path.read_text(), qualname)
    out = []
    for it in items:
        hint = it.hint
        if hint is not None:
            try:
                hint = convert(hint) if convert else hint
                parse_hint(hint, params, helpers)
            except (HintError, ValueError):
                hint = None  # backticked text that is not a hint is just emphasis
        out.append(
            Candidate(
                kind="precondition" if it.is_precondition else "property",
                text=it.text,
                hint=hint,
                source=Source(type="spec", loc=f"{path.name}:{it.line}"),
                confidence=FORMAL_CONFIDENCE if hint else TEXT_ONLY_CONFIDENCE,
                item=it.id,
            )
        )
    return Mined([], out), items
