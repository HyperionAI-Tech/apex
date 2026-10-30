"""A function's TLA+ bundle: `Spec.tla`, `Spec.cfg` and `harness.py`, in `.apex/tla/<mod>/<fn>/`.

- `Spec.tla`: module `Spec`, defining `Init`, `Next` and `Spec == Init /\\ [][Next]_vars`. PlusCal
  is allowed; Apex runs the translator on a copy.
- `Spec.cfg`: `SPECIFICATION Spec`, the `CONSTANTS` that bound the model, and the `INVARIANT`s.
  Each invariant is one property of the function, `T_<Name>`.
- `harness.py`: runs the real code in the configuration the constants describe::

      def setup():          # build the shared objects; returns a state
      def run(state):       # run the real code on them (start threads, join them)
      def observe(state):   # {spec variable: Python value} for the variables the code can show
      def check_<Name>(obs, done) -> bool   # optional: invariant <Name> on one observation;
                                            # `done`: the run has finished

The three files are reviewed and approved together: every `T_` property's statement is the whole
bundle, so an edit to any of them voids every approval, exactly as for Lean statements.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SPEC = "Spec.tla"
CFG = "Spec.cfg"
HARNESS = "harness.py"
FILES = (SPEC, CFG, HARNESS)
PREFIX = "T_"

_KEYWORDS = (
    "SPECIFICATION",
    "INIT",
    "NEXT",
    "INVARIANTS",
    "INVARIANT",
    "PROPERTIES",
    "PROPERTY",
    "CONSTANTS",
    "CONSTANT",
    "CONSTRAINTS",
    "CONSTRAINT",
    "ACTION_CONSTRAINTS",
    "ACTION_CONSTRAINT",
    "SYMMETRY",
    "VIEW",
    "CHECK_DEADLOCK",
    "POSTCONDITION",
    "ALIAS",
)
_SECTION = re.compile(r"^\s*(" + "|".join(_KEYWORDS) + r")\b(.*)$")
_ASSIGNMENT = re.compile(r"(\w+)\s*(?:=|<-)\s*(\S.*?)\s*(?=\w+\s*(?:=|<-)|$)")


class BundleError(ValueError):
    """The bundle is missing a file or its config does not say what to check."""


@dataclass
class Bundle:
    dir: Path
    spec: str
    cfg: str
    harness: str
    invariants: list[str]
    constants: dict[str, str]

    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        for text in (self.spec, self.cfg, self.harness):
            h.update(text.encode())
            h.update(b"\0")
        return h.hexdigest()

    @property
    def property_ids(self) -> list[str]:
        return [PREFIX + inv for inv in self.invariants]

    def statement(self, invariant: str) -> str:
        """What a reviewer approves for `T_<invariant>`: the invariant's name and the bundle."""
        return (
            f"(* Invariant {invariant} of this TLA+ bundle. *)\n"
            f"---- {SPEC} ----\n{self.spec}\n---- {CFG} ----\n{self.cfg}\n"
            f"---- {HARNESS} ----\n{self.harness}"
        )

    def cfg_without_invariants(self) -> str:
        """The config with its INVARIANT and PROPERTY sections removed (Apex adds one back)."""
        return "".join(line for line, kind in _sections(self.cfg) if kind not in _CHECKS)


_CHECKS = {"INVARIANT", "INVARIANTS", "PROPERTY", "PROPERTIES"}


def _sections(cfg: str) -> list[tuple[str, str | None]]:
    """Each line of the config with the section keyword it belongs to."""
    out: list[tuple[str, str | None]] = []
    kind: str | None = None
    for line in cfg.splitlines(keepends=True):
        code = line.split("\\*", 1)[0]
        if m := _SECTION.match(code):
            kind = m.group(1)
        out.append((line, kind))
    return out


def parse_cfg(cfg: str) -> tuple[list[str], dict[str, str]]:
    """(invariant names, constant assignments) from a TLC config."""
    invariants: list[str] = []
    constants: dict[str, str] = {}
    for line, kind in _sections(cfg):
        code = line.split("\\*", 1)[0]
        if m := _SECTION.match(code):
            code = m.group(2)
        if kind in ("INVARIANT", "INVARIANTS"):
            invariants += re.findall(r"[A-Za-z_]\w*", code)
        elif kind in ("CONSTANT", "CONSTANTS"):
            for name, value in _ASSIGNMENT.findall(code):
                constants[name] = value
    return list(dict.fromkeys(invariants)), constants


def load_bundle(directory: Path) -> Bundle:
    missing = [f for f in FILES if not (directory / f).is_file()]
    if missing:
        raise BundleError(f"{directory}: missing {', '.join(missing)}")
    spec, cfg, harness = ((directory / f).read_text() for f in FILES)
    if not re.search(r"^-{4,}\s*MODULE\s+Spec\b", spec, re.M):
        raise BundleError(f"{directory / SPEC}: the module must be named Spec")
    invariants, constants = parse_cfg(cfg)
    if not invariants:
        raise BundleError(f"{directory / CFG}: no INVARIANT to check")
    for fn in ("setup", "run", "observe"):
        if not re.search(rf"^def {fn}\(", harness, re.M):
            raise BundleError(f"{directory / HARNESS}: no `def {fn}(...)`")
    return Bundle(directory, spec, cfg, harness, invariants, constants)
