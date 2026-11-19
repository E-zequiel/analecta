"""T12/S16 backend contract: SYNTHESIS section counter in entry counts.

QA finding: every Sidebar section shows an item counter fed by
``GET /api/v1/entries/counts`` except SYNTHESIS. The contract pinned here
is consistency between each counter and the section list the Sidebar
actually renders for that section (``sectionParams`` in
``frontend/src/lib/components/Sidebar.svelte``):

- ``library``/``unread``/``read``/``bookmark``/``gem`` are captures-only:
  they exclude both archived entries (``flag: archive``) and synthesis
  entries (``source_type: synthesis``) — D11, no double-listing.
- ``synthesis`` counts every synthesis entry with no archive exclusion —
  exactly the filter the SYNTHESIS section list uses.
- ``archive`` counts every archived entry, synthesis included — exactly
  the filter the ARCHIVE section list uses.
"""

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from fastapi import FastAPI

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex

SECTION_IDS = ["library", "unread", "read", "bookmark", "gem", "synthesis", "archive"]


def _make_app(tmp_path: Path) -> tuple[FastAPI, VaultIndex]:
    """App with the entries router; state populated explicitly.

    ``httpx2.ASGITransport`` does not run the FastAPI lifespan, so the
    singletons ``get_index``/``get_event_bus`` read from ``app.state`` are
    wired here directly, mirroring conftest's ``_build_full_app`` lifespan.
    """
    config = AppConfig(vault_path=tmp_path / "vault")
    app = FastAPI()
    app.include_router(entries_router, prefix="/api/v1")
    index = VaultIndex(config.vault_path / "analecta.db")
    app.state.config = config
    app.state.index = index
    app.state.event_bus = EventBus()
    return app, index


def _add(
    index: VaultIndex,
    title: str,
    *,
    source_type: str,
    read_state: str,
    flags: list[str],
) -> EntryRecord:
    slug = title.lower().replace(" ", "-")
    entry = EntryRecord(
        title=title,
        url=f"analecta://synthesis/{slug}"
        if source_type == "synthesis"
        else f"https://example.com/{slug}",
        file_path=f"/vault/pages/{slug}.md",
        source_type=source_type,
        created_at="2026-11-19T00:00:00+00:00",
        updated_at="2026-11-19T00:00:00+00:00",
        read_state=read_state,
        flags_json=json.dumps(flags),
    )
    entry.id = index.add_entry(entry)
    return entry


@pytest_asyncio.fixture
async def seeded(
    tmp_path: Path,
) -> AsyncGenerator[tuple[httpx2.AsyncClient, VaultIndex]]:
    """In-process app seeded with every section filter combination.

    Captures: unread, read+bookmark, unread+gem, archived.
    Syntheses: unread, read+bookmark+gem, archived.
    """
    app, index = _make_app(tmp_path)
    _add(index, "Capture Unread", source_type="article", read_state="unread", flags=[])
    _add(
        index,
        "Capture Read Bookmark",
        source_type="article",
        read_state="read",
        flags=["bookmark"],
    )
    _add(
        index,
        "Capture Unread Gem",
        source_type="article",
        read_state="unread",
        flags=["gem"],
    )
    _add(
        index,
        "Capture Archived",
        source_type="article",
        read_state="unread",
        flags=["archive"],
    )
    _add(
        index,
        "Synthesis Unread",
        source_type="synthesis",
        read_state="unread",
        flags=[],
    )
    _add(
        index,
        "Synthesis Read Flagged",
        source_type="synthesis",
        read_state="read",
        flags=["bookmark", "gem"],
    )
    _add(
        index,
        "Synthesis Archived",
        source_type="synthesis",
        read_state="unread",
        flags=["archive"],
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c, index
    index.close()


def _section_params(section: str) -> dict[str, str]:
    """Mirror of Sidebar ``sectionParams`` — the list each section renders."""
    if section == "archive":
        return {"flag": "archive"}
    if section == "synthesis":
        return {"source_type": "synthesis"}
    if section in ("bookmark", "gem"):
        return {
            "flag": section,
            "exclude_flag": "archive",
            "exclude_source_type": "synthesis",
        }
    if section == "library":
        return {"exclude_flag": "archive", "exclude_source_type": "synthesis"}
    return {
        "read_state": section,
        "exclude_flag": "archive",
        "exclude_source_type": "synthesis",
    }


@pytest.mark.parametrize("section", SECTION_IDS)
@pytest.mark.asyncio
async def test_counts_match_section_lists(
    seeded: tuple[httpx2.AsyncClient, VaultIndex], section: str
) -> None:
    """Each counter equals the number of entries its section list shows."""
    c, _ = seeded
    r = await c.get("/api/v1/entries/counts")
    assert r.status_code == 200
    counts = r.json()
    assert section in counts
    listing = await c.get("/api/v1/entries", params=_section_params(section))
    assert listing.status_code == 200
    assert counts[section] == len(listing.json()), section


@pytest.mark.asyncio
async def test_counts_returns_full_contract_dict(
    seeded: tuple[httpx2.AsyncClient, VaultIndex],
) -> None:
    """The aggregated payload carries every section counter, SYNTHESIS included."""
    c, _ = seeded
    r = await c.get("/api/v1/entries/counts")
    assert r.status_code == 200
    assert r.json() == {
        "library": 3,
        "unread": 2,
        "read": 1,
        "bookmark": 1,
        "gem": 1,
        "synthesis": 3,
        "archive": 2,
    }


@pytest.mark.asyncio
async def test_synthesis_counter_has_no_archive_exclusion(
    seeded: tuple[httpx2.AsyncClient, VaultIndex],
) -> None:
    """SYNTHESIS counts archived synthesis entries too — its list does."""
    c, _ = seeded
    counts = (await c.get("/api/v1/entries/counts")).json()
    assert counts["synthesis"] == 3
    r = await c.get("/api/v1/entries", params={"source_type": "synthesis"})
    assert counts["synthesis"] == len(r.json())


@pytest.mark.asyncio
async def test_archive_counter_includes_synthesis(
    seeded: tuple[httpx2.AsyncClient, VaultIndex],
) -> None:
    """ARCHIVE counts archived synthesis entries — its list has no exclusion."""
    c, _ = seeded
    counts = (await c.get("/api/v1/entries/counts")).json()
    assert counts["archive"] == 2
    r = await c.get("/api/v1/entries", params={"flag": "archive"})
    assert counts["archive"] == len(r.json())


@pytest.mark.asyncio
async def test_counts_synthesis_zero_without_synthesis(tmp_path: Path) -> None:
    """A vault with captures only still reports ``synthesis`` as 0."""
    app, index = _make_app(tmp_path)
    _add(index, "Only Capture", source_type="article", read_state="unread", flags=[])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        r = await c.get("/api/v1/entries/counts")
        assert r.status_code == 200
        assert r.json()["synthesis"] == 0
        assert r.json()["library"] == 1
    index.close()
