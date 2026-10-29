"""Locating the pinned Lake project and the Lean toolchain."""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV_LEAN_PROJECT = "APEX_LEAN_PROJECT"
ENV_CACHE_NAME = "apex-lean-env.json"


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
        """Env override first, then the project shipped with the package, then the repo checkout."""
        candidates: list[Path] = []
        if env := os.environ.get(ENV_LEAN_PROJECT):
            candidates.append(Path(env))
        here = Path(__file__).resolve()
        candidates.append(here.parents[1] / "_lean")  # bundled in the wheel
        candidates.append(here.parents[3] / "lean")  # src/apex/lean/project.py -> repo/lean
        for c in candidates:
            if (c / "lakefile.toml").is_file():
                return cls(c)
        raise LeanUnavailable(
            "pinned Lean project not found; set APEX_LEAN_PROJECT or reinstall Apex"
        )

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

    @property
    def prelude_built(self) -> bool:
        return (self.root / ".lake" / "build" / "lib" / "lean" / "Apex" / "Prelude.olean").is_file()

    def require_lake(self) -> Path:
        lake = find_lake()
        if lake is None:
            raise LeanUnavailable("`lake` not found; run `apex init` to install the Lean toolchain")
        return lake

    def _env_key(self) -> str:
        h = hashlib.sha256()
        for name in ("lean-toolchain", "lake-manifest.json", "lakefile.toml"):
            path = self.root / name
            h.update(path.read_bytes() if path.is_file() else b"")
        return h.hexdigest()

    def lean_command(self) -> tuple[Path, dict[str, str]]:
        """The `lean` binary and the environment `lake env` would run it with.

        Lake's own startup (re-elaborating every dependency's lakefile) can dominate a cold
        compile by an order of magnitude, so it runs once and the result is cached, keyed on the
        toolchain, manifest and lakefile. The compile itself stays cold and file-isolated.
        """
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
