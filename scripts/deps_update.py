#!/usr/bin/env python3
"""Age-gated dependency updater for Analecta.

For each ecosystem (Python/uv, Node/pnpm):
  1. Detect packages with available updates.
  2. Query the upstream registry for the release date of the new version.
  3. Skip packages released less than COOLDOWN_DAYS ago.
  4. Apply selective updates for packages that pass the gate.

Usage (from repo root):
    python scripts/deps_update.py [--cooldown DAYS] [--pr-body-file PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).parent.parent
COOLDOWN_DAYS = 10
_REGISTRY_TIMEOUT = 15
_WORKSPACE_DIR = {"frontend": "frontend", "analecta-electron": "electron"}

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

type Updated = tuple[str, str, str, datetime]  # (name, old, new, release_dt)
type Skipped = tuple[str, str, datetime]  # (name, new, release_dt)

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _fetch_json(
    url: str, *, headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # pyright: ignore[reportAny]
            return cast(dict[str, Any], json.loads(resp.read()))  # pyright: ignore[reportAny]
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"    [warn] registry error for {url}: {exc}")
        return None


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _age_ok(release_dt: datetime, cooldown: int) -> bool:
    return datetime.now(UTC) - release_dt >= timedelta(days=cooldown)


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _run_check(repo_root: Path) -> bool:
    """Run check.sh and stream its output. Returns True on success."""
    print("\n==> Verifying with check.sh …")
    result = subprocess.run(["bash", "scripts/check.sh"], cwd=repo_root)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Python / uv
# ---------------------------------------------------------------------------


def _pkg_name(dep: str) -> str:
    """Extract bare package name from a PEP 508 dependency string."""
    m = re.match(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", dep.strip())
    return m.group(1) if m else ""


def _pypi_release_date(
    _name: str, version: str, all_releases: dict[str, Any]
) -> datetime | None:
    files = cast(list[dict[str, Any]], all_releases.get(version, []))
    times: list[str] = [
        cast(str, f.get("upload_time_iso_8601") or f.get("upload_time"))
        for f in files
        if f.get("upload_time_iso_8601") or f.get("upload_time")
    ]
    if not times:
        return None
    try:
        return _parse_iso(min(times))
    except ValueError, TypeError:
        return None


def update_python(cooldown: int) -> tuple[list[Updated], list[Skipped], bool]:
    """Check and apply Python dependency updates via uv."""
    print("\n=== Python (uv) ===")
    backend = REPO_ROOT / "backend"

    with (backend / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    project_section = cast(dict[str, Any], pyproject.get("project", {}))
    direct: list[str] = list(cast(list[str], project_section.get("dependencies", [])))
    dep_groups = cast(dict[str, list[Any]], pyproject.get("dependency-groups", {}))
    for group in dep_groups.values():
        direct.extend(d for d in group if isinstance(d, str))

    names = sorted({_pkg_name(d) for d in direct if _pkg_name(d)})

    with (backend / "uv.lock").open("rb") as f:
        lock = tomllib.load(f)
    packages = cast(list[dict[str, Any]], lock.get("package", []))
    current_versions: dict[str, str] = {
        cast(str, p["name"]): cast(str, p["version"]) for p in packages
    }

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    had_error = False
    fetch_attempted = 0
    fetch_ok = 0

    for name in names:
        fetch_attempted += 1
        data = _fetch_json(f"https://pypi.org/pypi/{name}/json")
        if data is None:
            continue
        fetch_ok += 1
        info = cast(dict[str, Any], data.get("info", {}))
        latest: str = cast(str, info.get("version", ""))
        current = current_versions.get(name, "")
        if not latest or latest == current:
            continue
        print(f"  {name}: {current} -> {latest}")

        releases = cast(dict[str, Any], data.get("releases", {}))
        release_dt = _pypi_release_date(name, latest, releases)
        if release_dt is None:
            print("    [warn] release date unavailable, skipping")
            continue
        if not _age_ok(release_dt, cooldown):
            days_old = (datetime.now(UTC) - release_dt).days
            print(f"    [skip] {days_old}d old (cooldown: {cooldown}d)")
            skipped.append((name, latest, release_dt))
            continue

        result = _run(["uv", "lock", "--upgrade-package", name], cwd=backend)
        if result.returncode != 0:
            print(f"::error::{name}: uv lock failed — {result.stderr.strip()}")
            had_error = True
        else:
            print("    [ok] updated")
            updated.append((name, current, latest, release_dt))

    if fetch_attempted > 0 and fetch_ok == 0:
        print(
            "::error::All PyPI registry fetches failed — no packages could be checked"
        )
        had_error = True

    return updated, skipped, had_error


# ---------------------------------------------------------------------------
# Node / pnpm
# ---------------------------------------------------------------------------


def _parse_pnpm_outdated(raw: str) -> dict[str, dict[str, Any]]:
    """Parse pnpm outdated --json output (flat or workspace-nested)."""
    data = cast(dict[str, Any], json.loads(raw))
    if not data:
        return {}
    first = next(iter(data.values()), None)
    # Flat format: first value has "current" / "wanted" keys directly
    if isinstance(first, dict) and ("current" in first or "wanted" in first):
        return cast(dict[str, dict[str, Any]], data)
    # Workspace-nested: {"frontend": {"pkg": {...}}}
    merged: dict[str, dict[str, Any]] = {}
    for ws_pkgs in data.values():
        if isinstance(ws_pkgs, dict):
            merged.update(cast(dict[str, dict[str, Any]], ws_pkgs))
    return merged


def _npm_release_date(name: str, version: str) -> datetime | None:
    encoded = name.replace("/", "%2F")
    data = _fetch_json(f"https://registry.npmjs.org/{encoded}")
    if data is None:
        return None
    time_dict = cast(dict[str, str], data.get("time", {}))
    ts = time_dict.get(version)
    if not ts:
        return None
    try:
        return _parse_iso(ts)
    except ValueError, TypeError:
        return None


def _ensure_exact_specifier(workspace_dir: str, name: str, version: str) -> bool:
    """Rewrite the package.json specifier for name to an exact pin.

    pnpm's --save-exact does not reliably strip the range operator when
    rewriting an existing specifier — observed leaving ^8.0.0 in place
    after `pnpm add pkg@8.0.0 --save-exact` over a prior ^7 range.

    Returns:
        True if the specifier was rewritten, meaning the lockfile needs
        a resync via `pnpm install`.
    """
    pkg_path = REPO_ROOT / workspace_dir / "package.json"
    text = pkg_path.read_text()
    pattern = re.compile(rf'("{re.escape(name)}":\s*")([^"]*)(")')
    match = pattern.search(text)
    if match is None or match.group(2) == version:
        return False
    new_text = pattern.sub(rf"\g<1>{version}\g<3>", text, count=1)
    _ = pkg_path.write_text(new_text)
    return True


def update_node(
    cooldown: int, workspace: str
) -> tuple[list[Updated], list[Skipped], bool]:
    """Check and apply Node dependency updates via pnpm for a given workspace."""
    print(f"\n=== Node (pnpm) — {workspace} ===")

    # pnpm outdated exits 1 when packages are outdated — capture regardless
    result = _run(
        ["pnpm", "outdated", "--json", "--filter", workspace],
        cwd=REPO_ROOT,
    )
    if result.returncode not in {0, 1}:
        print(
            f"::error::pnpm outdated failed (exit {result.returncode}):"
            f" {result.stderr.strip()}"
        )
        return [], [], True

    raw = result.stdout.strip()
    if not raw:
        print("  nothing outdated")
        return [], [], False

    try:
        outdated = _parse_pnpm_outdated(raw)
    except json.JSONDecodeError, StopIteration:
        # Try extracting a JSON block from mixed output
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            print("  [warn] could not parse pnpm outdated output")
            return [], [], False
        outdated = _parse_pnpm_outdated(m.group())

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    had_error = False
    fetch_attempted = 0
    fetch_ok = 0

    for name, info in sorted(outdated.items()):
        current: str = cast(str, info.get("current", ""))
        latest: str = cast(str, info.get("latest", ""))
        if not latest or latest == current:
            continue
        print(f"  {name}: {current} -> {latest}")

        fetch_attempted += 1
        release_dt = _npm_release_date(name, latest)
        if release_dt is None:
            print("    [warn] release date unavailable, skipping")
            continue
        fetch_ok += 1
        if not _age_ok(release_dt, cooldown):
            days_old = (datetime.now(UTC) - release_dt).days
            print(f"    [skip] {days_old}d old (cooldown: {cooldown}d)")
            skipped.append((name, latest, release_dt))
            continue

        result = _run(
            ["pnpm", "add", f"{name}@{latest}", "--save-exact", "--filter", workspace],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"::error::{name}: pnpm add failed — {result.stderr.strip()}")
            had_error = True
            continue

        if _ensure_exact_specifier(_WORKSPACE_DIR[workspace], name, latest):
            resync = _run(["pnpm", "install", "--filter", workspace], cwd=REPO_ROOT)
            if resync.returncode != 0:
                print(
                    f"::error::{name}: lockfile resync failed after exact-pin fix"
                    f" — {resync.stderr.strip()}"
                )
                had_error = True
                continue
            print("    [fix] forced exact pin, resynced lockfile")

        print("    [ok] updated")
        updated.append((name, current, latest, release_dt))

    if fetch_attempted > 0 and fetch_ok == 0:
        print("::error::All npm registry fetches failed — no packages could be checked")
        had_error = True

    return updated, skipped, had_error


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


def _pr_body(
    py_up: list[Updated],
    py_sk: list[Skipped],
    nd_up: list[Updated],
    nd_sk: list[Skipped],
    cooldown: int,
) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "## Dependency update",
        "",
        f"Auto-generated on {today} — **{cooldown}-day release cooldown** applied.",
        "",
    ]

    def _section(title: str, up: list[Updated], sk: list[Skipped]) -> None:
        lines.append(f"### {title}")
        if up:
            lines.append("| Package | Old | New | Released |")
            lines.append("|---------|-----|-----|----------|")
            for name, old, new, release_dt in up:
                lines.append(
                    f"| `{name}` | {old} | {new} | {release_dt.strftime('%Y-%m-%d')} |"
                )
        else:
            lines.append("_No updates applied._")
        if sk:
            lines.append("")
            eligible = [
                f"`{n}` {v} _(eligible "
                f"{(release_dt + timedelta(days=cooldown)).strftime('%Y-%m-%d')})_"
                for n, v, release_dt in sk
            ]
            lines.append(f"**Skipped — too recent:** {', '.join(eligible)}")
        lines.append("")

    _section("Python", py_up, py_sk)
    _section("Node / pnpm", nd_up, nd_sk)

    lines += [
        "---",
        "",
        "Before merging, verify the gate passed: run `./scripts/check.sh`.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the age-gated dependency updater."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--cooldown",
        type=int,
        default=COOLDOWN_DAYS,
        metavar="DAYS",
        help="minimum age in days (default: 10)",
    )
    _ = parser.add_argument(
        "--pr-body-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="write PR body markdown to this file",
    )
    _ = parser.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help="run check.sh after updating and revert lockfiles on failure",
    )
    args = parser.parse_args()
    cooldown: int = cast(int, args.cooldown)
    pr_body_file: Path | None = cast(Path | None, args.pr_body_file)
    verify: bool = cast(bool, args.verify)

    uv_lock_path = REPO_ROOT / "backend" / "uv.lock"
    pnpm_lock_path = REPO_ROOT / "pnpm-lock.yaml"
    uv_snap: bytes | None = uv_lock_path.read_bytes() if verify else None
    pnpm_snap: bytes | None = pnpm_lock_path.read_bytes() if verify else None

    py_up, py_sk, py_err = update_python(cooldown)
    nd_up_fe, nd_sk_fe, nd_err_fe = update_node(cooldown, "frontend")
    nd_up_el, nd_sk_el, nd_err_el = update_node(cooldown, "analecta-electron")
    nd_up = nd_up_fe + nd_up_el
    nd_sk = nd_sk_fe + nd_sk_el
    nd_err = nd_err_fe or nd_err_el

    total_up = len(py_up) + len(nd_up)
    total_sk = len(py_sk) + len(nd_sk)
    print(f"\n==> {total_up} updated, {total_sk} skipped (cooldown)")

    if pr_body_file:
        body = _pr_body(py_up, py_sk, nd_up, nd_sk, cooldown)
        _ = pr_body_file.write_text(body)
        print(f"    PR body written to {pr_body_file}")

    had_error = py_err or nd_err
    if verify and total_up > 0 and not had_error:
        if not _run_check(REPO_ROOT):
            print("::error::check.sh failed — reverting lockfiles")
            if uv_snap is not None:
                uv_lock_path.write_bytes(uv_snap)
            if pnpm_snap is not None:
                pnpm_lock_path.write_bytes(pnpm_snap)
            sys.exit(1)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
