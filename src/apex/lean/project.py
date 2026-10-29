"""Locating the pinned Lake project and the Lean toolchain."""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV_LEAN_PROJECT = "APEX_LEAN_PROJECT"
ENV_APEX_HOME = "APEX_HOME"
ENV_CACHE_NAME = "apex-lean-env.json"
PIN_FILES = ("lean-toolchain", "lake-manifest.json", "lakefile.toml")
SOURCE_FILES = ("Apex.lean",)
SOURCE_DIRS = ("Apex",)


def apex_home() -> Path:
    """Where Apex keeps state shared by every project: the installed Lean project (~5 GB)."""
    return Path(os.environ.get(ENV_APEX_HOME) or Path.home() / ".apex")


def bundled_project() -> Path | None:
    """The pinned Lean project shipped inside the installed package (a template: never built)."""
    here = Path(__file__).resolve()
    candidate = here.parents[1] / "_lean"
    return candidate if (candidate / "lakefile.toml").is_file() else None


def checkout_project() -> Path | None:
    """`lean/` in a source checkout (editable installs and development)."""
    candidate = Path(__file__).resolve().parents[3] / "lean"
    return candidate if (candidate / "lakefile.toml").is_file() else None


def pin_key(root: Path) -> str:
    """Identity of a toolchain + Mathlib pin. Prelude edits do not change it (they rebuild in
    seconds); a new toolchain or Mathlib does, and gets a fresh directory and cache."""
    h = hashlib.sha256()
    for name in PIN_FILES:
        path = root / name
        h.update(path.read_bytes() if path.is_file() else b"")
    return h.hexdigest()[:12]


class LeanUnavailable(RuntimeError):
    """The Lean toolchain or pinned project is missing. Maps to exit code 3."""


def find_lake() -> Path | None:
    found = shutil.which("lake")
    if found:
        return Path(found)
    elan_lake = Path.home() / ".elan" / "bin" / ("lake.exe" if os.name == "nt" else "lake")
    return elan_lake if elan_lake.exists() else None


def find_elan() -> Path | None:
    found = shutil.which("elan")
    if found:
        return Path(found)
    elan = Path.home() / ".elan" / "bin" / ("elan.exe" if os.name == "nt" else "elan")
    return elan if elan.exists() else None


@dataclass(frozen=True)
class LeanProject:
    root: Path

    @classmethod
    def locate(cls) -> "LeanProject":
        """Env override, then a source checkout, then the copy `apex init` installed under
        ~/.apex for the bundled pin, then the bundled template itself (not yet initialised)."""
        if env := os.environ.get(ENV_LEAN_PROJECT):
            if not (Path(env) / "lakefile.toml").is_file():
                raise LeanUnavailable(f"{ENV_LEAN_PROJECT}={env} has no lakefile.toml")
            return cls(Path(env))
        if checkout := checkout_project():
            return cls(checkout)
        if bundle := bundled_project():
            installed = cls.installed_for(bundle)
            return installed if (installed.root / "lakefile.toml").is_file() else cls(bundle)
        raise LeanUnavailable(
            "pinned Lean project not found; set APEX_LEAN_PROJECT or reinstall Apex"
        )

    @classmethod
    def installed_for(cls, bundle: Path) -> "LeanProject":
        return cls(apex_home() / "lean" / pin_key(bundle))

    @property
    def is_bundle(self) -> bool:
        """True for the template inside the installed package, which is never built in place."""
        bundle = bundled_project()
        return bundle is not None and self.root.resolve() == bundle.resolve()

    def install(self) -> "LeanProject":
        """Copy this (bundled) project to ~/.apex/lean/<pin>, refreshing Apex's own sources.

        Pin files are written only into a fresh directory; an existing install keeps its
        `.lake` (toolchain, Mathlib cache), and only the prelude sources are synced.
        """
        target = LeanProject.installed_for(self.root)
        target.root.mkdir(parents=True, exist_ok=True)
        for name in (*PIN_FILES, *SOURCE_FILES):
            src, dst = self.root / name, target.root / name
            if src.is_file() and (not dst.is_file() or dst.read_bytes() != src.read_bytes()):
                shutil.copy2(src, dst)
        for d in SOURCE_DIRS:
            for src in (self.root / d).rglob("*.lean"):
                dst = target.root / src.relative_to(self.root)
                if not dst.is_file() or dst.read_bytes() != src.read_bytes():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        return target

    @property
    def toolchain(self) -> str:
        return (self.root / "lean-toolchain").read_text().strip()

    @property
    def manifest(self) -> dict | None:
        path = self.root / "lake-manifest.json"
        return json.loads(path.read_text()) if path.is_file() else None

    @property
    def mathlib_rev(self) -> str | None:
        manifest = self.manifest
        if not manifest:
            return None
        for pkg in manifest.get("packages", []):
            if pkg.get("name") == "mathlib":
                return pkg.get("rev")
        return None

    @property
    def mathlib_dir(self) -> Path:
        return self.root / ".lake" / "packages" / "mathlib"

    @property
    def mathlib_cache_present(self) -> bool:
        return (self.mathlib_dir / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean").is_file()

    def _prelude_sources_sha(self) -> str:
        h = hashlib.sha256()
        for path in sorted([self.root / "Apex.lean", *(self.root / "Apex").rglob("*.lean")]):
            if path.is_file():
                h.update(path.relative_to(self.root).as_posix().encode() + b"\0")
                h.update(path.read_bytes())
        return h.hexdigest()

    @property
    def _prelude_stamp(self) -> Path:
        return self.root / ".lake" / "apex-prelude.sha256"

    def mark_prelude_built(self) -> None:
        """Record which prelude sources the last successful `lake build Apex` compiled."""
        self._prelude_stamp.parent.mkdir(parents=True, exist_ok=True)
        self._prelude_stamp.write_text(self._prelude_sources_sha() + "\n")

    @property
    def prelude_built(self) -> bool:
        """The prelude's .olean exists and matches its sources. A stale .olean would make
        generated models mean something other than what the translator assumes.

        `apex init` stamps the sources it built (Lake rebuilds on content, so mtimes alone can
        mislead); without a stamp, as in a source checkout, the .olean must be newer.
        """
        olean = self.root / ".lake" / "build" / "lib" / "lean" / "Apex" / "Prelude.olean"
        if not olean.is_file():
            return False
        if self._prelude_stamp.is_file():
            return self._prelude_stamp.read_text().strip() == self._prelude_sources_sha()
        source = self.root / "Apex" / "Prelude.lean"
        return not source.is_file() or olean.stat().st_mtime >= source.stat().st_mtime

    def require_lake(self) -> Path:
        lake = find_lake()
        if lake is None:
            raise LeanUnavailable("`lake` not found; run `apex init` to install the Lean toolchain")
        return lake

    def _env_key(self) -> str:
        h = hashlib.sha256()
        for name in PIN_FILES:
            path = self.root / name
            h.update(path.read_bytes() if path.is_file() else b"")
        return h.hexdigest()

    def lean_command(self) -> tuple[Path, dict[str, str]]:
        """The `lean` binary and the environment `lake env` would run it with.

        Lake's own startup (re-elaborating every dependency's lakefile) can dominate a cold
        compile by an order of magnitude, so it runs once and the result is cached, keyed on the
        toolchain, manifest and lakefile. The compile itself stays cold and file-isolated.
        """
        if self.is_bundle:
            raise LeanUnavailable(
                "Apex's Lean project is not set up yet; run `apex init` (one-time download)"
            )
        if self.mathlib_rev and not self.mathlib_dir.is_dir():
            # `lake env` would start cloning Mathlib here; that is `apex init`'s job, not a check's.
            raise LeanUnavailable(f"Mathlib is not fetched in {self.root}; run `apex init`")
        cache = self.root / ".lake" / ENV_CACHE_NAME
        key = self._env_key()
        if cache.is_file():
            data = json.loads(cache.read_text())
            if data.get("key") == key and Path(data["lean"]).exists():
                return Path(data["lean"]), {**os.environ, **data["env"]}
        lake = self.require_lake()
        out = subprocess.run(
            [str(lake), "env", "printenv"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if out.returncode != 0:
            raise LeanUnavailable(f"`lake env` failed: {(out.stderr or out.stdout).strip()[-500:]}")
        lake_env = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
        delta = {
            k: v
            for k, v in lake_env.items()
            if k.startswith(("LEAN", "LAKE"))
            or k in ("PATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")
        }
        sysroot = lake_env.get("LEAN_SYSROOT")
        if not sysroot:
            raise LeanUnavailable("`lake env` did not report LEAN_SYSROOT")
        lean = Path(sysroot) / "bin" / ("lean.exe" if os.name == "nt" else "lean")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "lean": str(lean), "env": delta}, indent=2))
        return lean, {**os.environ, **delta}
