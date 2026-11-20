"""CLI subcommands of the sidecar binary (owner decision D3).

Same binary, same schema. Invocation without arguments keeps the sidecar
server (Electron spawns it that way); subcommands run through this module.

Connection modes:
- **attached**: a live app sidecar is discovered via the 0600 session file
  (``~/.local/share/analecta/session.json``); requests carry the session
  token.
- **standalone**: no live sidecar — open the configured vault directly as
  the single writer under flock; fails fast when another process owns it.

Output: human text by default, ``--json`` for bots.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analecta.config import load_config
from analecta.extraction import extract
from analecta.manifest import write_vault_manifest

if TYPE_CHECKING:
    from analecta.storage.index import EntryRecord
from analecta.session_security import SESSION_FILE_NAME, SessionDiscovery

_DATA_DIR = Path.home() / ".local" / "share" / "analecta"


def discovery_request_token(
    session_file: Path, path: str, body: dict[str, object] | None = None
) -> tuple[str, dict[str, str]] | None:
    """Assemble the attached-mode request for *path* from a session file.

    Args:
        session_file: Path to ``session.json``.
        path: API path under ``/api/v1``.
        body: Optional JSON body.

    Returns:
        ``(url, headers)`` for the request, or ``None`` when the file is
        missing/unreadable (the caller falls back to standalone mode).
    """
    data = SessionDiscovery(session_file.parent).read()
    if not data:
        return None
    port = data.get("port")
    token = data.get("token")
    if not isinstance(port, int) or port <= 0 or not isinstance(token, str):
        return None
    headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
    return f"http://localhost:{port}/api/v1{path}", headers


def _attached_get(path: str) -> list[dict[str, object]] | None:
    """GET via the discovery file; ``None`` when no live sidecar."""
    req = discovery_request_token(_DATA_DIR / SESSION_FILE_NAME, path)
    if req is None:
        return None
    url, headers = req
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=5
        ) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except urllib.error.URLError, OSError, json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _attached_post(path: str, body: dict[str, object]) -> tuple[int, object] | None:
    """POST via the discovery file; ``None`` when no live sidecar."""
    req = discovery_request_token(_DATA_DIR / SESSION_FILE_NAME, path, body)
    if req is None:
        return None
    url, headers = req
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError, OSError, json.JSONDecodeError:
        return None


def _entry_type(record_source_type: str) -> str:
    """OKF ``type`` for a DB row: captures are references (D1)."""
    return "synthesis" if record_source_type == "synthesis" else "reference"


def _page_path(record_file_path: str) -> str:
    return f"/pages/{Path(record_file_path).name}"


def build_index_output(index: Any, as_json: bool = False) -> str:
    """Build the OKF-style vault index from the index, on the fly (D6).

    Args:
        index: ``VaultIndex`` (standalone mode).
        as_json: Bot contract instead of human text.

    Returns:
        Markdown sections (``# Library`` / ``# Synthesis``) or a JSON array
        of ``{title, type, read_state, created_at, path}`` — synthesis
        items additionally carry a boolean ``stale`` field (captures do
        not). Staleness is classified by reading every synthesis page on
        disk, in both output modes.
    """
    records: list[Any] = index.list_entries()
    items = [
        {
            "title": r.title,
            "type": _entry_type(r.source_type),
            "read_state": r.read_state,
            "created_at": r.created_at,
            "path": _page_path(r.file_path),
        }
        for r in records
    ]
    # Staleness classification reads the synthesis pages (I/O); computed
    # up front so the JSON contract and the human marker share one verdict.
    stale_paths = _stale_synthesis_paths(records)
    if as_json:
        payload = [
            {**item, "stale": item["path"] in stale_paths}
            if item["type"] == "synthesis"
            else item
            for item in items
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)
    captures = [i for i in items if i["type"] == "reference"]
    synths = [i for i in items if i["type"] == "synthesis"]
    lines: list[str] = ["# Library"]
    lines += [f"- [{i['title']}]({i['path']})" for i in captures]
    lines += ["", "# Synthesis"]
    lines += [
        f"- [{i['title']}]({i['path']})"
        + (" — STALE" if i["path"] in stale_paths else "")
        for i in synths
    ]
    return "\n".join(lines)


def _reject_conflicting_flags(flag: str, conflicting: list[str]) -> None:
    """Print the flag-conflict error shared by ``--stale`` and ``--update``.

    Args:
        flag: The subcommand flag whose extras are rejected.
        conflicting: The conflicting flag names, in rejection order.
    """
    print(f"error: {flag} does not support {' '.join(conflicting)}", file=sys.stderr)


def _stale_synthesis_paths(records: list[Any]) -> set[str]:
    """Resources (``/pages/<file>.md``) of stale synthesis records.

    Args:
        records: Index records (``VaultIndex`` or ``_AttachedIndex`` shape).

    Returns:
        The page paths whose synthesis is stale per
        ``synthesis_staleness`` — drifted citations, or a synthesis whose
        own page is missing/unreadable/unparseable. Only fresh syntheses
        are absent.
    """
    from analecta.storage.synthesis import synthesis_staleness

    stale: set[str] = set()
    for r in records:
        if getattr(r, "source_type", None) != "synthesis" or not r.file_path:
            continue
        if synthesis_staleness(r):
            stale.add(_page_path(r.file_path))
    return stale


def _run_stale_report(vault_path: Path) -> int:
    """Report every synthesis's staleness.

    Args:
        vault_path: Root directory of the local vault.

    Returns:
        1 when at least one synthesis is stale, 0 otherwise; 3 when the
        vault lock is held (distinct from 1, which means stale exists).
    """
    from analecta.session_security import VaultLock
    from analecta.storage.index import VaultIndex
    from analecta.storage.synthesis import synthesis_staleness

    lock = VaultLock(vault_path)
    fd = lock.acquire()
    if fd is None:
        print("error: vault is locked by another Analecta process", file=sys.stderr)
        return 3
    try:
        index = VaultIndex(vault_path / "analecta.db")
        try:
            lines: list[str] = []
            stale_count = 0
            fresh_count = 0
            for rec in index.list_entries():
                if rec.source_type != "synthesis" or not rec.file_path:
                    continue
                stale = synthesis_staleness(rec)
                if stale:
                    stale_count += 1
                    lines.append(f"stale: {rec.title} — {', '.join(stale)}")
                else:
                    fresh_count += 1
                    lines.append(f"fresh: {rec.title}")
            lines.append(f"{stale_count} stale, {fresh_count} fresh")
            print("\n".join(lines))
        finally:
            index.close()
    finally:
        lock.release(fd)
    return 1 if stale_count else 0


def _run_synthesis_update(
    vp: Path, *, update_ref: str, body: str, sources: list[str], by: str | None
) -> int:
    """Regenerate an existing synthesis in place.

    Args:
        vp: Root directory of the local vault.
        update_ref: Synthesis entry id or exact URL.
        body: New markdown body.
        sources: Source entry refs (id or exact URL) forming the new
            citation set, in the requested order. Each must resolve to a
            capture entry — a synthesis cannot be cited (its basis would
            be pinned one step behind the regeneration itself).
        by: OKF actor id recorded in ``generated.by``; ``None`` keeps
            the previous actor.

    Returns:
        0 on success, 1 when the ref is unknown, is not a synthesis, a
        source ref is unknown, or a source ref resolves to a synthesis;
        3 when the vault lock is held. Usage errors are rejected by the
        caller.
    """
    from analecta.session_security import VaultLock
    from analecta.storage.index import VaultIndex
    from analecta.storage.synthesis import update_synthesis_entry

    lock = VaultLock(vp)
    fd = lock.acquire()
    if fd is None:
        print("error: vault is locked by another Analecta process", file=sys.stderr)
        return 3
    try:
        index = VaultIndex(vp / "analecta.db")
        try:
            record = _resolve_entry(index, update_ref)
            if record is None:
                print(f"error: entry not found: {update_ref}", file=sys.stderr)
                return 1
            if record.source_type != "synthesis":
                print(
                    f"error: not a synthesis: {update_ref} ({record.title})",
                    file=sys.stderr,
                )
                return 1
            source_records = []
            for ref in sources:
                rec = _resolve_entry(index, ref)
                if rec is None:
                    print(f"error: source not found: {ref}", file=sys.stderr)
                    return 1
                if rec.source_type == "synthesis":
                    print(
                        f"error: source must be a capture, not a synthesis: {ref}",
                        file=sys.stderr,
                    )
                    return 1
                source_records.append(rec)
            update_synthesis_entry(
                index,
                record,
                body=body,
                source_entries=source_records,
                generated_by=by,
            )
            updated = index.get_entry(record.id)
            write_vault_manifest(vp)
        finally:
            index.close()
    finally:
        lock.release(fd)
    if updated is None:
        print(
            f"error: synthesis row disappeared during update: {update_ref}",
            file=sys.stderr,
        )
        return 1
    print(f"synthesis updated: {updated.title}")
    print(f"file:  {updated.file_path}")
    return 0


def build_log_output(index: Any, as_json: bool = False) -> str:
    """Build the chronological vault update log from the index, on the fly (D6).

    Args:
        index: ``VaultIndex`` (standalone mode).
        as_json: Bot contract instead of human text.

    Returns:
        Date-grouped ``## YYYY-MM-DD`` sections, newest first, with
        ``**Creation**`` items; or a JSON array of ``{date, items}``.
    """
    records: list[Any] = index.list_entries()
    groups: dict[str, list[dict[str, str]]] = {}
    for r in sorted(records, key=lambda x: x.created_at, reverse=True):
        date = r.created_at[:10]
        groups.setdefault(date, []).append(
            {
                "title": r.title,
                "path": _page_path(r.file_path),
                "kind": "Creation",
            }
        )
    ordered = sorted(groups.items(), reverse=True)
    if as_json:
        return json.dumps(
            [{"date": d, "items": items} for d, items in ordered],
            ensure_ascii=False,
            indent=2,
        )
    lines: list[str] = ["# Vault Update Log"]
    for date, items in ordered:
        lines.append(f"\n## {date}")
        lines += [f"- **{i['kind']}**: [{i['title']}]({i['path']})" for i in items]
    return "\n".join(lines)


def run_extract_standalone(vault_path: Path, url: str) -> EntryRecord:
    """Extract *url* into *vault_path* as the single writer (flock).

    Args:
        vault_path: Root directory of the local vault.
        url: Target URL.

    Returns:
        The created ``EntryRecord``.

    Raises:
        RuntimeError: When another process holds the vault flock.
    """
    import asyncio

    from analecta.extraction.pipeline import extract_to_vault
    from analecta.session_security import VaultLock
    from analecta.storage.index import VaultIndex
    from analecta.storage.vault import VaultManager

    lock = VaultLock(vault_path)
    fd = lock.acquire()
    if fd is None:
        raise RuntimeError("vault is locked by another Analecta process (flock held)")
    try:
        index = VaultIndex(vault_path / "analecta.db")
        try:
            vault = VaultManager(vault_path)
            created = asyncio.run(
                extract_to_vault(index, vault, url, extract_fn=extract)
            )
            write_vault_manifest(vault_path)
            return created
        finally:
            index.close()
    finally:
        lock.release(fd)


class _AttachedIndex:
    """Read-only view over attached-mode records, shaped like VaultIndex."""

    def __init__(self, records: list[dict[str, object]]) -> None:
        self._records = records

    def list_entries(self) -> list[Any]:
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                title=r.get("title"),
                source_type=r.get("source_type"),
                read_state=r.get("read_state"),
                created_at=r.get("created_at"),
                file_path=r.get("file_path"),
            )
            for r in self._records
        ]


def _resolve_entry(index: Any, ref: str) -> Any | None:
    """Resolve an entry by numeric id or exact URL (case-sensitive)."""
    if ref.isdigit():
        return index.get_entry(int(ref))
    for r in index.list_entries():
        if r.url == ref:
            return r
    return None


def run(argv: list[str], vault_path: Path | None = None) -> int:
    """Dispatch CLI subcommands; returns the process exit code.

    Args:
        argv: Arguments after the program name (e.g. ``["extract", url]``).
        vault_path: Vault override (tests); defaults to the configured
            vault.

    Returns:
        0 on success, 1 on failure, 2 on usage errors, 3 when a
        ``synthesize --stale`` or ``synthesize --update`` run cannot start
        because the vault lock is held.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="analecta", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="extract a URL into the vault")
    p_extract.add_argument("url")
    p_extract.add_argument("--json", action="store_true", dest="as_json")

    p_index = sub.add_parser("index", help="print the vault index (on the fly)")
    p_index.add_argument("--json", action="store_true", dest="as_json")

    p_log = sub.add_parser("log", help="print the vault update log (on the fly)")
    p_log.add_argument("--json", action="store_true", dest="as_json")

    p_sum = sub.add_parser("summary", help="record a bot-side entry summary")
    p_sum.add_argument("entry", help="entry id or exact URL")
    p_sum.add_argument("--text", required=True, help="summary text (<= 500 chars)")
    p_sum.add_argument("--by", required=True, help="OKF actor id of the bot")

    p_syn = sub.add_parser(
        "synthesize",
        help="create a synthesis entry, update one in place, or report staleness",
    )
    p_syn.add_argument(
        "--stale",
        action="store_true",
        help="report stale syntheses instead of creating or updating one",
    )
    p_syn.add_argument(
        "--update",
        metavar="REF",
        help="update the synthesis with this id or exact URL in place",
    )
    p_syn.add_argument("--title")
    p_syn.add_argument("--body", help="markdown body")
    p_syn.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="source entry id or URL (repeatable)",
    )
    p_syn.add_argument("--by", help="OKF actor id of the bot")
    p_syn.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.command == "extract":
        record = None
        posted = _attached_post("/extract", {"url": args.url})
        if posted is not None:
            status, payload = posted
            if status == 200 and isinstance(payload, dict):
                record = payload
            else:
                detail = payload if isinstance(payload, str) else "extract failed"
                print(f"error: {detail}", file=sys.stderr)
                return 1
        if record is None:
            try:
                created = run_extract_standalone(load_config().vault_path, args.url)
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            record = {
                "title": created.title,
                "url": created.url,
                "file_path": created.file_path,
                "source_type": created.source_type,
                "read_state": created.read_state,
                "created_at": created.created_at,
            }
        if args.as_json:
            print(json.dumps(record, ensure_ascii=False, indent=2))
        else:
            assert isinstance(record, dict)
            print(f"saved: {record['title']}")
            print(f"file:  {record.get('file_path', record.get('path', ''))}")
        return 0

    if args.command == "summary":
        if len(args.text) > 500:
            print("error: summary exceeds 500 characters", file=sys.stderr)
            return 1
        from analecta.markdown.frontmatter import body_sha16, set_summary_fields
        from analecta.session_security import VaultLock
        from analecta.storage.index import VaultIndex
        from analecta.storage.vault import VaultManager

        vp = vault_path if vault_path is not None else load_config().vault_path
        lock = VaultLock(vp)
        fd = lock.acquire()
        if fd is None:
            print(
                "error: vault is locked by another Analecta process",
                file=sys.stderr,
            )
            return 1
        try:
            index = VaultIndex(vp / "analecta.db")
            try:
                record = _resolve_entry(index, args.entry)
                if record is None or record.file_path is None:
                    print(f"error: entry not found: {args.entry}", file=sys.stderr)
                    return 1
                page = Path(record.file_path)

                def _write() -> None:
                    text = page.read_text(encoding="utf-8")
                    basis = body_sha16(text)
                    page.write_text(
                        set_summary_fields(text, args.text, basis),
                        encoding="utf-8",
                    )

                _write()
                write_vault_manifest(vp)
            finally:
                index.close()
        finally:
            lock.release(fd)
        if getattr(args, "as_json", False):
            print(
                json.dumps(
                    {"entry": args.entry, "summary": args.text}, ensure_ascii=False
                )
            )
        else:
            print(f"summary recorded for {args.entry}")
        return 0

    if args.command == "synthesize":
        vp = vault_path if vault_path is not None else load_config().vault_path
        if args.stale:
            # A flag the user passed explicitly conflicts even when its
            # value is an empty string (falsy); only absence counts as no
            # conflict, so str flags test `is not None`, not truthiness.
            conflicting: list[str] = []
            if args.as_json:
                conflicting.append("--json")
            if args.title is not None:
                conflicting.append("--title")
            if args.body is not None:
                conflicting.append("--body")
            if args.by is not None:
                conflicting.append("--by")
            if args.sources:
                conflicting.append("--source")
            if args.update is not None:
                conflicting.append("--update")
            if conflicting:
                _reject_conflicting_flags("--stale", conflicting)
                return 2
            return _run_stale_report(vp)
        if args.update is not None:
            # --update precedes the generic missing-args check: its usage
            # errors are --update-specific. --title is rejected because an
            # in-place update never refreshes the title (the slug — hence
            # the URL and the file path — derives from it); --json stays
            # report-only, like for --stale.
            update_conflicts: list[str] = []
            if args.title is not None:
                update_conflicts.append("--title")
            if args.as_json:
                update_conflicts.append("--json")
            if update_conflicts:
                _reject_conflicting_flags("--update", update_conflicts)
                return 2
            missing = [
                flag for flag, value in (("--body", args.body),) if value is None
            ]
            if not args.sources:
                missing.append("--source")
            if missing:
                print(
                    f"error: --update requires {', '.join(missing)}",
                    file=sys.stderr,
                )
                return 2
            return _run_synthesis_update(
                vp,
                update_ref=args.update,
                body=args.body,
                sources=args.sources,
                by=args.by,
            )
        missing = [
            flag
            for flag, value in (
                ("--title", args.title),
                ("--body", args.body),
                ("--by", args.by),
            )
            if value is None
        ]
        if not args.sources:
            missing.append("--source")
        if missing:
            print(
                f"error: {', '.join(missing)} required unless --stale or --update",
                file=sys.stderr,
            )
            return 2
        from analecta.session_security import VaultLock
        from analecta.storage.index import VaultIndex
        from analecta.storage.synthesis import create_synthesis_entry
        from analecta.storage.vault import VaultManager

        lock = VaultLock(vp)
        fd = lock.acquire()
        if fd is None:
            print(
                "error: vault is locked by another Analecta process",
                file=sys.stderr,
            )
            return 1
        try:
            index = VaultIndex(vp / "analecta.db")
            try:
                source_records = []
                for ref in args.sources:
                    rec = _resolve_entry(index, ref)
                    if rec is None:
                        print(f"error: source not found: {ref}", file=sys.stderr)
                        return 1
                    source_records.append(rec)
                eid = create_synthesis_entry(
                    index,
                    VaultManager(vp),
                    title=args.title,
                    body=args.body,
                    source_entries=source_records,
                    generated_by=args.by,
                )
                created = index.get_entry(eid)
                write_vault_manifest(vp)
            finally:
                index.close()
        finally:
            lock.release(fd)
        assert created is not None
        if args.as_json:
            print(
                json.dumps(
                    {
                        "id": eid,
                        "title": created.title,
                        "url": created.url,
                        "file_path": created.file_path,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"synthesis created: {created.title}")
            print(f"file:  {created.file_path}")
        return 0

    # index / log: attached first, standalone fallback.
    if args.command in ("index", "log"):
        path = "/entries"
        records = _attached_get(path)
        index = None
        if records is not None:
            index = _AttachedIndex(records)
        else:
            from analecta.session_security import VaultLock
            from analecta.storage.index import VaultIndex

            vp2 = vault_path if vault_path is not None else load_config().vault_path
            lock = VaultLock(vp2)
            fd = lock.acquire()
            if fd is None:
                print(
                    "error: vault is locked by another Analecta process",
                    file=sys.stderr,
                )
                return 1
            try:
                index = VaultIndex(vp2 / "analecta.db")
            finally:
                # Read-only use: release immediately; keep instance short-lived.
                lock.release(fd)
        if args.command == "index":
            print(build_index_output(index, as_json=args.as_json))
        else:
            print(build_log_output(index, as_json=args.as_json))
        close = getattr(index, "close", None)
        if close is not None:
            close()
        return 0

    return 2
