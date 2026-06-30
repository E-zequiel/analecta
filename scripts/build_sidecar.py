#!/usr/bin/env python3
"""Build analecta-sidecar with PyInstaller and stage it for Electron.

Usage:
    python scripts/build_sidecar.py

Idempotent: skips the build if pyproject.toml, backend.spec, and all
backend/src/**/*.py files are unchanged since the last run.
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
BINARIES = ROOT / "binaries"
CACHE_FILE = ROOT / ".build" / "sidecar_hash"


def _compute_hash() -> str:
    h = hashlib.sha256()
    for path in [BACKEND / "pyproject.toml", BACKEND / "backend.spec"]:
        h.update(path.read_bytes())
    for f in sorted((BACKEND / "src").rglob("*.py")):
        h.update(f.read_bytes())
    return h.hexdigest()


def main() -> None:
    """Build the PyInstaller sidecar binary, skipping if sources are unchanged."""
    current = _compute_hash()
    if CACHE_FILE.exists() and CACHE_FILE.read_text().strip() == current:
        print("sidecar: cached, skipping build")
        return

    print("sidecar: building …")

    out_dir = BINARIES / "analecta-sidecar"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    subprocess.run(
        [
            "mise",
            "exec",
            "--",
            "uv",
            "run",
            "pyinstaller",
            "backend.spec",
            "--distpath",
            str(BINARIES),
            "--noconfirm",
            "--clean",
        ],
        cwd=BACKEND,
        check=True,
    )

    inner = out_dir / "analecta-sidecar"
    if not inner.exists():
        sys.exit(f"error: expected binary not found at {inner}")

    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(current)
    print(f"sidecar: built → {inner}")


if __name__ == "__main__":
    main()
