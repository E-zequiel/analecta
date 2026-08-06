#!/usr/bin/env python3
"""Age-gated dependency updater for Analecta.

For each ecosystem (Python/uv, Node/pnpm):
  1. Detect packages with available updates.
  2. Query the upstream registry for the release date of the new version.
  3. Skip packages released less than COOLDOWN_DAYS ago.
  4. Apply selective updates for packages that pass the gate.
  5. With --verify: check the whole batch against check.sh; on failure,
     replay the batch one package at a time to isolate and exclude only the
     package(s) that actually broke it, instead of discarding the batch.

A hard error in one Node workspace (or in the Python ecosystem) does not
prevent a PR for the others — it is reported in the PR body next to that
ecosystem's section. The exit code only signals failure when nothing
succeeded anywhere in the run.

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
_WORKSPACE_DIR = {
    "frontend": "frontend",
    "analecta-electron": "electron",
    "analecta": ".",
}

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

type Updated = tuple[str, str, str, datetime]  # (name, old, new, release_dt)
type Skipped = tuple[str, str, datetime]  # (name, new, release_dt)
type Blocked = tuple[str, str, str]  # (name, new, reason)

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


def _run_check(repo_root: Path, scope: str) -> bool:
    """Run check.sh for the given scope ('backend' or 'frontend') and stream output."""
    print(f"\n==> Verifying with check.sh {scope} …")
    result = subprocess.run(["bash", "scripts/check.sh", scope], cwd=repo_root)
    return result.returncode == 0


def _sanitize_reason(text: str, limit: int = 200) -> str:
    """Make subprocess-derived text safe to interpolate into the committed PR body.

    A blocked package's reason can carry raw stderr/stdout from a package's
    own install step (via uv/pnpm) — untruncated, that text ends up verbatim
    in pr-body.md, which is committed and handed to `gh pr create` unreviewed.
    Collapsing whitespace and stripping markdown-structural characters keeps
    it from breaking out of its single-line context or forging PR body
    structure (headings, tables, comments) that a human reads before merging.
    """
    flat = " ".join(text.split())
    flat = flat.replace("`", "'").replace("|", "/")
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


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


def _apply_python_package(name: str, backend: Path) -> tuple[bool, str]:
    """Apply a single Python package bump via `uv lock --upgrade-package`."""
    result = _run(["uv", "lock", "--upgrade-package", name], cwd=backend)
    if result.returncode != 0:
        return False, f"uv lock failed — {result.stderr.strip()}"
    return True, ""


def update_python(cooldown: int) -> tuple[list[Updated], list[Skipped], list[str]]:
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
    errors: list[str] = []
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

        ok, err = _apply_python_package(name, backend)
        if not ok:
            print(f"::error::{name}: {err}")
            errors.append(f"{name}: {err}")
        else:
            print("    [ok] updated")
            updated.append((name, current, latest, release_dt))

    if fetch_attempted > 0 and fetch_ok == 0:
        msg = "All PyPI registry fetches failed — no packages could be checked"
        print(f"::error::{msg}")
        errors.append(msg)

    return updated, skipped, errors


def _verify_python(
    backend: Path, uv_lock_path: Path, snap: bytes, batch: list[Updated]
) -> tuple[list[Updated], list[Blocked]]:
    """Verify a batch of Python updates with check.sh backend; bisect on failure."""
    if _run_check(REPO_ROOT, "backend"):
        return batch, []

    print(
        "::warning::check.sh backend failed on the full batch"
        " — isolating the offending package(s)"
    )
    _ = uv_lock_path.write_bytes(snap)

    survivors: list[Updated] = []
    blocked: list[Blocked] = []
    for name, old, new, release_dt in batch:
        step_snap = uv_lock_path.read_bytes()
        ok, reason = _apply_python_package(name, backend)
        if ok:
            ok = _run_check(REPO_ROOT, "backend")
            reason = "check.sh backend failed"
        if ok:
            print(f"    [ok] {name}: confirmed in isolation")
            survivors.append((name, old, new, release_dt))
        else:
            _ = uv_lock_path.write_bytes(step_snap)
            print(f"::warning::{name}: blocked — {reason}")
            blocked.append((name, new, _sanitize_reason(reason)))
    return survivors, blocked


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


def _apply_node_package(workspace: str, name: str, version: str) -> tuple[bool, str]:
    """Apply a single Node package bump: pnpm add, enforce exact pin, resync, dedupe.

    --ignore-scripts on every pnpm call here: this function's only output is
    pnpm-lock.yaml/package.json bytes that get uploaded as a CI artifact and
    then committed and force-pushed by a separate, higher-privileged job
    (contents: write) — code executing on this disk before that upload could
    tamper with what gets pushed. allowBuilds in pnpm-workspace.yaml already
    restricts lifecycle scripts to `electron`, and nothing check.sh exercises
    (type-checking, lint, vite build) launches Electron or needs its binary,
    so skipping scripts here costs nothing functionally.

    A (False, reason) return is guaranteed to be a no-op on disk: the exact-pin
    rewrite (a direct file write, not a pnpm operation) can succeed and then
    have its follow-up lockfile resync fail, which would otherwise leave
    package.json and pnpm-lock.yaml mutually inconsistent for a package that
    was never actually applied — this function snapshots both before touching
    them and restores on any failure path, so callers never have to reason
    about partial state.

    Returns:
        (True, "") on success, (False, reason) on failure at any step.
    """
    pkg_path = REPO_ROOT / _WORKSPACE_DIR[workspace] / "package.json"
    lock_path = REPO_ROOT / "pnpm-lock.yaml"
    pkg_snap = pkg_path.read_bytes()
    lock_snap = lock_path.read_bytes()

    def _restore() -> None:
        _ = pkg_path.write_bytes(pkg_snap)
        _ = lock_path.write_bytes(lock_snap)

    result = _run(
        [
            "pnpm",
            "add",
            f"{name}@{version}",
            "--save-exact",
            "--ignore-scripts",
            "--filter",
            workspace,
        ],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        _restore()
        return False, f"pnpm add failed — {result.stderr.strip()}"

    if _ensure_exact_specifier(_WORKSPACE_DIR[workspace], name, version):
        # --no-frozen-lockfile: this resync intentionally updates the
        # lockfile, but pnpm defaults frozen-lockfile to on in CI (CI=true),
        # which rejects any install that would change it.
        resync = _run(
            [
                "pnpm",
                "install",
                "--filter",
                workspace,
                "--no-frozen-lockfile",
                "--ignore-scripts",
            ],
            cwd=REPO_ROOT,
        )
        if resync.returncode != 0:
            # pnpm reports ERR_PNPM_OUTDATED_LOCKFILE etc. on stdout, not stderr.
            detail = resync.stderr.strip() or resync.stdout.strip()
            _restore()
            return False, f"lockfile resync failed after exact-pin fix — {detail}"

    # A direct-dependency bump can leave an older resolution of the same
    # package alive elsewhere in the graph (pulled in transitively by an
    # unrelated consumer) unless the lockfile is deduped afterward — this
    # surfaces as duplicate-type errors in svelte-check/tsc, not as a pnpm
    # error, so it has to be handled here rather than left to the caller.
    dedupe = _run(["pnpm", "dedupe", "--ignore-scripts"], cwd=REPO_ROOT)
    if dedupe.returncode != 0:
        detail = dedupe.stderr.strip() or dedupe.stdout.strip()
        _restore()
        return False, f"dedupe failed — {detail}"

    return True, ""


def update_node(
    cooldown: int, workspace: str
) -> tuple[list[Updated], list[Skipped], list[str]]:
    """Check and apply Node dependency updates via pnpm for a given workspace."""
    print(f"\n=== Node (pnpm) — {workspace} ===")

    # pnpm outdated exits 1 when packages are outdated — capture regardless
    result = _run(
        ["pnpm", "outdated", "--json", "--filter", workspace],
        cwd=REPO_ROOT,
    )
    if result.returncode not in {0, 1}:
        msg = (
            f"pnpm outdated failed (exit {result.returncode}): {result.stderr.strip()}"
        )
        print(f"::error::{msg}")
        return [], [], [msg]

    raw = result.stdout.strip()
    if not raw:
        print("  nothing outdated")
        return [], [], []

    try:
        outdated = _parse_pnpm_outdated(raw)
    except json.JSONDecodeError, StopIteration:
        # Try extracting a JSON block from mixed output
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            print("  [warn] could not parse pnpm outdated output")
            return [], [], []
        outdated = _parse_pnpm_outdated(m.group())

    updated: list[Updated] = []
    skipped: list[Skipped] = []
    errors: list[str] = []
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

        ok, err = _apply_node_package(workspace, name, latest)
        if not ok:
            print(f"::error::{name}: {err}")
            errors.append(f"{name}: {err}")
            continue

        print("    [ok] updated")
        updated.append((name, current, latest, release_dt))

    if fetch_attempted > 0 and fetch_ok == 0:
        msg = "All npm registry fetches failed — no packages could be checked"
        print(f"::error::{msg}")
        errors.append(msg)

    return updated, skipped, errors


def _verify_node(
    snapshots: dict[Path, bytes],
    batch: list[tuple[str, Updated]],
) -> tuple[list[tuple[str, Updated]], list[Blocked]]:
    """Verify a batch of Node updates with check.sh frontend; bisect on failure.

    *snapshots* covers every file `_apply_node_package` can touch across all
    workspaces (pnpm-lock.yaml plus each workspace's package.json) — restore
    is a single loop over the dict instead of one write-back per path, so
    adding or removing a workspace can't leave a path out of the rollback.
    """
    if _run_check(REPO_ROOT, "frontend"):
        return batch, []

    print(
        "::warning::check.sh frontend failed on the full batch"
        " — isolating the offending package(s)"
    )
    for path, data in snapshots.items():
        _ = path.write_bytes(data)

    survivors: list[tuple[str, Updated]] = []
    blocked: list[Blocked] = []
    for workspace, (name, old, new, release_dt) in batch:
        step_snap = {path: path.read_bytes() for path in snapshots}
        ok, reason = _apply_node_package(workspace, name, new)
        if ok:
            ok = _run_check(REPO_ROOT, "frontend")
            reason = "check.sh frontend failed"
        if ok:
            print(f"    [ok] {name} ({workspace}): confirmed in isolation")
            survivors.append((workspace, (name, old, new, release_dt)))
        else:
            for path, data in step_snap.items():
                _ = path.write_bytes(data)
            print(f"::warning::{name} ({workspace}): blocked — {reason}")
            blocked.append((name, new, _sanitize_reason(reason)))
    return survivors, blocked


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


def _pr_body(
    py_up: list[Updated],
    py_sk: list[Skipped],
    py_blocked: list[Blocked],
    py_errors: list[str],
    nd_up: list[Updated],
    nd_sk: list[Skipped],
    nd_blocked: list[Blocked],
    *,
    nd_errors: list[str],
    nd_ws: list[str],
    cooldown: int,
) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "## Dependency update",
        "",
        f"Auto-generated on {today} — **{cooldown}-day release cooldown** applied.",
        "",
    ]

    def _section(
        title: str,
        up: list[Updated],
        sk: list[Skipped],
        blocked: list[Blocked],
        errors: list[str],
        workspaces: list[str] | None = None,
    ) -> None:
        lines.append(f"### {title}")
        if up:
            if workspaces is not None:
                lines.append("| Package | Workspace | Old | New | Released |")
                lines.append("|---------|-----------|-----|-----|----------|")
                for (name, old, new, release_dt), ws in zip(
                    up, workspaces, strict=True
                ):
                    lines.append(
                        f"| `{name}` | `{ws}` | {old} | {new}"
                        f" | {release_dt.strftime('%Y-%m-%d')} |"
                    )
            else:
                lines.append("| Package | Old | New | Released |")
                lines.append("|---------|-----|-----|----------|")
                for name, old, new, release_dt in up:
                    lines.append(
                        f"| `{name}` | {old} | {new}"
                        f" | {release_dt.strftime('%Y-%m-%d')} |"
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
        if blocked:
            lines.append("")
            items = [f"`{n}` {v} — _{reason}_" for n, v, reason in blocked]
            lines.append(
                f"**Blocked — broke `check.sh`, excluded from this update:**"
                f" {'; '.join(items)}"
            )
        if errors:
            lines.append("")
            lines.append(
                "**Errors — some packages may not have been checked:**"
                f" {'; '.join(errors)}"
            )
        lines.append("")

    _section("Python", py_up, py_sk, py_blocked, py_errors)
    _section("Node / pnpm", nd_up, nd_sk, nd_blocked, nd_errors, workspaces=nd_ws)

    lines += [
        "---",
        "",
        "Before merging, verify the gate passed: run `./scripts/check.sh`.",
    ]
    if py_blocked or nd_blocked:
        lines += [
            "",
            "Blocked packages were excluded automatically, not reverted wholesale —"
            " re-run the updater once the underlying incompatibility is resolved"
            " upstream.",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CHANGELOG.md
# ---------------------------------------------------------------------------


def _changelog_bullet(py_up: list[Updated], nd_up: list[Updated]) -> str | None:
    """Build the single rollup line for a run's applied updates, or None if none.

    One line per script run (not one per package) — every dependency update
    needs a CHANGELOG entry per CLAUDE.md's Changelog Hard Constraint, but this
    updater has no CVE awareness (that's the hand-crafted `fix(deps)` path),
    so everything it applies is a routine, non-security bump and belongs under
    `### Changed`, not `### Security`.
    """
    total = len(py_up) + len(nd_up)
    if total == 0:
        return None
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    plural = "package" if total == 1 else "packages"
    return f"- Dependency updates: {total} {plural} ({today})."


def _insert_changelog_entry(text: str, bullet: str) -> str | None:
    """Insert *bullet* as the last line of `### Changed` under `## [Unreleased]`.

    Anchors on the literal `## [Unreleased]` heading and only ever edits
    within that block (up to the next `## [` heading) — never a published
    version section. Creates the `### Changed` subsection if the block
    doesn't have one yet, appending it after any other subsections already
    there. Returns None if the anchor isn't found, so the caller can warn
    and skip instead of guessing at a different insertion point.
    """
    lines = text.split("\n")
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == "## [Unreleased]"
        )
    except StopIteration:
        return None

    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    block = lines[start + 1 : end]

    changed_idx = next(
        (i for i, line in enumerate(block) if line.strip() == "### Changed"), None
    )

    if changed_idx is None:
        while block and block[-1].strip() == "":
            block.pop()
        while block and block[0].strip() == "":
            block.pop(0)
        new_block = [""]
        if block:
            new_block += [*block, ""]
        new_block += ["### Changed", "", bullet, ""]
        block = new_block
    else:
        stop = next(
            (
                i
                for i in range(changed_idx + 1, len(block))
                if block[i].startswith("### ")
            ),
            len(block),
        )
        sub = block[changed_idx + 1 : stop]
        while sub and sub[-1].strip() == "":
            sub.pop()
        if not sub:
            sub.append("")
        sub.append(bullet)
        sub.append("")
        block[changed_idx + 1 : stop] = sub

    lines[start + 1 : end] = block
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
        help=(
            "verify each ecosystem's batch with check.sh; on failure, isolate and "
            "exclude only the package(s) that broke it instead of reverting everything"
        ),
    )
    args = parser.parse_args()
    cooldown: int = cast(int, args.cooldown)
    pr_body_file: Path | None = cast(Path | None, args.pr_body_file)
    verify: bool = cast(bool, args.verify)

    backend = REPO_ROOT / "backend"
    uv_lock_path = backend / "uv.lock"
    pnpm_lock_path = REPO_ROOT / "pnpm-lock.yaml"

    uv_snap: bytes | None = uv_lock_path.read_bytes() if verify else None
    node_snap: dict[Path, bytes] | None = (
        {
            p: p.read_bytes()
            for p in (
                pnpm_lock_path,
                *(REPO_ROOT / d / "package.json" for d in _WORKSPACE_DIR.values()),
            )
        }
        if verify
        else None
    )

    py_up, py_sk, py_errors = update_python(cooldown)

    nd_sk: list[Skipped] = []
    nd_up_tagged: list[tuple[str, Updated]] = []
    nd_errors_by_ws: dict[str, list[str]] = {}
    for workspace in _WORKSPACE_DIR:
        up, sk, errs = update_node(cooldown, workspace)
        nd_up_tagged += [(workspace, u) for u in up]
        nd_sk += sk
        if errs:
            nd_errors_by_ws[workspace] = errs

    py_blocked: list[Blocked] = []
    nd_blocked: list[Blocked] = []

    if uv_snap is not None and py_up and not py_errors:
        py_up, py_blocked = _verify_python(backend, uv_lock_path, uv_snap, py_up)

    if node_snap is not None and nd_up_tagged:
        nd_up_tagged, nd_blocked = _verify_node(node_snap, nd_up_tagged)
    nd_up = [u for _, u in nd_up_tagged]
    nd_ws = [w for w, _ in nd_up_tagged]
    nd_errors = [
        f"`{ws}`: {msg}" for ws, msgs in nd_errors_by_ws.items() for msg in msgs
    ]

    total_up = len(py_up) + len(nd_up)
    total_sk = len(py_sk) + len(nd_sk)
    total_blocked = len(py_blocked) + len(nd_blocked)
    print(
        f"\n==> {total_up} updated, {total_sk} skipped (cooldown),"
        f" {total_blocked} blocked (incompatible)"
    )

    if pr_body_file:
        body = _pr_body(
            py_up,
            py_sk,
            py_blocked,
            py_errors,
            nd_up,
            nd_sk,
            nd_blocked,
            nd_errors=nd_errors,
            nd_ws=nd_ws,
            cooldown=cooldown,
        )
        _ = pr_body_file.write_text(body)
        print(f"    PR body written to {pr_body_file}")

        bullet = _changelog_bullet(py_up, nd_up)
        if bullet:
            changelog_path = REPO_ROOT / "CHANGELOG.md"
            updated_changelog = _insert_changelog_entry(
                changelog_path.read_text(), bullet
            )
            if updated_changelog is None:
                print(
                    "::warning::CHANGELOG.md: '## [Unreleased]' heading not found"
                    " — skipping entry, add it manually"
                )
            else:
                _ = changelog_path.write_text(updated_changelog)
                print("    CHANGELOG.md updated")

    had_error = bool(py_errors) or bool(nd_errors_by_ws)
    if had_error and total_up == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
