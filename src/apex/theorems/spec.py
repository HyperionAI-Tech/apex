"""Spec files: the Lean theorems an agent or human writes about a model.

A spec file is a trust boundary. The statement text is what a human approves, so nothing else in
the file may be able to change what that text means. A `local notation "≤" => ...`, a new
`instance : LE Int`, a helper `def` or `set_option autoImplicit true` could each make an approved
statement say something different while its text, and therefore its hash, stays the same.

So a spec file may contain only:
  - `import` lines (allowlisted libraries; enforced again by the verdict guard),
  - comments and doc comments,
  - `theorem` / `lemma` declarations, optionally prefixed by `@[simp]` or
    `set_option maxHeartbeats <n> in`.

Theorems named like property IDs (`P1`, `E3`, ...) are the properties; anything else is a
helper lemma that proofs may use.
"""

import re
from dataclasses import dataclass, field

from apex.core.hashing import normalize_lean_statement, statement_hash

PROPERTY_NAME = re.compile(r"^[A-Z]\d+$")
_DECL_PREFIX = re.compile(
    r"^(?:@\[simp\]\s*)?(?:set_option\s+maxHeartbeats\s+\d+\s+in\s+)?(?:@\[simp\]\s*)?"
    r"(?P<kw>theorem|lemma)\s+(?P<name>[^\s(:{\[⦃]+)"
)


_PREFIX_LINE = re.compile(r"^(?:@\[simp\]|set_option\s+maxHeartbeats\s+\d+\s+in)$")


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class SpecDecl:
    name: str
    statement: str
    """Binders and type: the text between the name and `:=`, comments removed."""
    proof: str
    doc: str | None
    text: str
    """The declaration as written, including its doc comment, for inclusion in verdict files."""
    line: int

    @property
    def is_property(self) -> bool:
        return bool(PROPERTY_NAME.match(self.name))

    @property
    def full_statement(self) -> str:
        return normalize_lean_statement(f"theorem {self.name} {self.statement}")

    @property
    def statement_sha256(self) -> str:
        return statement_hash(self.full_statement)

    @property
    def has_proof(self) -> bool:
        """False for an empty proof or a bare `sorry` placeholder."""
        return not re.fullmatch(r"(by\s+)?(sorry)?", self.proof.strip())


@dataclass
class Spec:
    imports: list[str] = field(default_factory=list)
    decls: list[SpecDecl] = field(default_factory=list)

    @property
    def properties(self) -> list[SpecDecl]:
        return [d for d in self.decls if d.is_property]

    @property
    def helpers(self) -> list[SpecDecl]:
        return [d for d in self.decls if not d.is_property]

    def get(self, name: str) -> SpecDecl | None:
        return next((d for d in self.decls if d.name == name), None)


def _mask(text: str, *, strings: bool) -> str:
    """Replace comments (and optionally string literals) with spaces, preserving positions."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif text.startswith("/-", i):
            depth = 0
            while i < n:
                if text.startswith("/-", i):
                    depth += 1
                    out[i] = out[i + 1] = " "
                    i += 2
                elif text.startswith("-/", i):
                    depth -= 1
                    out[i] = out[i + 1] = " "
                    i += 2
                    if depth == 0:
                        break
                else:
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if strings:
                for k in range(i, min(j + 1, n)):
                    if text[k] != "\n":
                        out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _split_at_assign(masked: str) -> int | None:
    """Index of the first `:=` at bracket depth 0."""
    depth = 0
    for i, c in enumerate(masked):
        if c in "([{⟨⦃":
            depth += 1
        elif c in ")]}⟩⦄":
            depth -= 1
        elif c == ":" and depth == 0 and masked.startswith(":=", i):
            return i
    return None


def _doc_before(text: str, start: int) -> tuple[str | None, int]:
    """A `/-- ... -/` doc comment ending right before `start` (whitespace only between)."""
    j = start
    while j > 0 and text[j - 1] in " \t\n":
        j -= 1
    if not text[:j].endswith("-/"):
        return None, start
    k = text.rfind("/--", 0, j)
    if k == -1:
        return None, start
    return text[k + 3 : j - 2].strip(), k


def parse_spec(text: str) -> Spec:
    masked = _mask(text, strings=True)
    no_comments = _mask(text, strings=False)
    lines = masked.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)

    # A top-level command starts on a line whose first column is not whitespace.
    starts: list[int] = []
    for k, line in enumerate(lines):
        if not line.strip() or line[0].isspace():
            continue
        # `@[simp]` or `set_option maxHeartbeats n in` on its own line prefixes the next command.
        if (
            starts
            and _PREFIX_LINE.match(lines[starts[-1]].strip())
            and all(not lines[j].strip() for j in range(starts[-1] + 1, k))
        ):
            continue
        starts.append(k)
    spec, problems = Spec(), []
    for idx, k in enumerate(starts):
        begin = offsets[k]
        end = offsets[starts[idx + 1]] if idx + 1 < len(starts) else len(text)
        end = _doc_before(text, end)[1]  # the next declaration's doc comment is not ours
        chunk = masked[begin:end]
        head = chunk.lstrip()
        if head.startswith("import "):
            spec.imports.append(head.split()[1])
            if len(head.split()) != 2:
                problems.append(f"line {k + 1}: one module per import line")
            continue
        m = _DECL_PREFIX.match(head)
        if not m:
            first = head.split()[0] if head.split() else head
            problems.append(
                f"line {k + 1}: `{first}` is not allowed in a spec file; only "
                "imports and theorem/lemma declarations are (anything else could "
                "change what an approved statement means)"
            )
            continue
        assign = _split_at_assign(chunk)
        if assign is None:
            problems.append(f"line {k + 1}: `{m['name']}` has no `:=`")
            continue
        name_end = begin + (len(chunk) - len(head)) + m.end()
        statement = no_comments[name_end : begin + assign].strip()
        # The declaration ends at its last code character: comment lines after it (a TODO for
        # the next property, say) are not part of its proof.
        end = begin + len(no_comments[begin:end].rstrip())
        proof = text[begin + assign + 2 : end].strip()
        doc, doc_start = _doc_before(text, begin)
        if m["name"] in {d.name for d in spec.decls}:
            problems.append(f"line {k + 1}: duplicate declaration `{m['name']}`")
        spec.decls.append(
            SpecDecl(
                name=m["name"],
                statement=statement,
                proof=proof,
                doc=doc,
                text=text[doc_start:end].rstrip() + "\n",
                line=k + 1,
            )
        )
    if problems:
        raise SpecError(problems)
    return spec


def render_decl(decl: SpecDecl, proof: str) -> str:
    """Re-emit a declaration with a different proof, keeping its doc and statement verbatim."""
    head = decl.text[: decl.text.rfind(decl.proof)] if decl.proof else decl.text
    return head.rstrip() + " " + proof.strip() + "\n"
