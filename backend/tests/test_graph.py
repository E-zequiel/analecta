"""Tests for VaultIndex.get_graph() and GET /entries/graph."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(
    index: VaultIndex,
    *,
    n: int = 1,
    title: str | None = None,
    file_path: str | None = None,
    source_type: str = "article",
) -> int:
    return index.add_entry(
        EntryRecord(
            title=title or f"Article {n}",
            url=f"https://example.com/{n}",
            file_path=file_path or f"/vault/pages/article-{n}.md",
            source_type=source_type,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )


def _write_md(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_app(tmp_path: Path) -> FastAPI:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        idx = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = idx
        app.state.event_bus = EventBus()
        yield
        idx.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(entries_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Storage layer — get_graph()
# ---------------------------------------------------------------------------


def test_empty_vault_returns_empty_graph(tmp_path: Path) -> None:
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        nodes, edges = idx.get_graph()
    assert nodes == []
    assert edges == []


def test_single_entry_no_backlinks_excluded(tmp_path: Path) -> None:
    md = tmp_path / "note.md"
    md.write_text("No links here.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        _seed(idx, title="Standalone", file_path=str(md))
        idx.index_backlinks(1)
        nodes, edges = idx.get_graph()
    assert nodes == []
    assert edges == []


def test_wikilink_creates_entry_to_entry_edge(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("See [[Target Article]].", encoding="utf-8")
    tgt_path = tmp_path / "tgt.md"
    tgt_path.write_text("Target content.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source Article", file_path=str(src_path))
        tgt_id = _seed(idx, n=2, title="Target Article", file_path=str(tgt_path))
        idx.index_backlinks(src_id)

        nodes, edges = idx.get_graph()

    node_ids = {n.node_id for n in nodes}
    assert f"entry:{src_id}" in node_ids
    assert f"entry:{tgt_id}" in node_ids
    assert len(edges) == 1
    assert edges[0].source == f"entry:{src_id}"
    assert edges[0].target == f"entry:{tgt_id}"
    assert edges[0].weight == 1


def test_unresolved_wikilink_skipped(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("See [[Nonexistent Note]].", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        _seed(idx, title="Source", file_path=str(src_path))
        idx.index_backlinks(1)
        nodes, edges = idx.get_graph()

    assert nodes == []
    assert edges == []


def test_hashtag_resolving_to_entry_creates_edge(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("Great post #python_programming", encoding="utf-8")
    tgt_path = tmp_path / "tgt.md"
    tgt_path.write_text("Python Programming content.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source", file_path=str(src_path))
        tgt_id = _seed(idx, n=2, title="Python Programming", file_path=str(tgt_path))
        idx.index_backlinks(src_id)

        nodes, edges = idx.get_graph()

    node_ids = {n.node_id for n in nodes}
    assert f"entry:{tgt_id}" in node_ids
    assert any(
        e.source == f"entry:{src_id}" and e.target == f"entry:{tgt_id}" for e in edges
    )
    assert not any(n.kind == "tag" for n in nodes)


def test_unresolved_hashtag_creates_virtual_tag_node(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("Discussing #machine_learning today.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, title="Source", file_path=str(src_path))
        idx.index_backlinks(src_id)
        nodes, edges = idx.get_graph()

    tag_nodes = [n for n in nodes if n.kind == "tag"]
    assert len(tag_nodes) == 1
    assert tag_nodes[0].node_id == "tag:machine_learning"
    assert tag_nodes[0].label == "#machine_learning"
    assert tag_nodes[0].source_type is None
    assert len(edges) == 1
    assert edges[0].source == f"entry:{src_id}"
    assert edges[0].target == "tag:machine_learning"


def test_multiple_occurrences_collapsed_into_weighted_edge(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text(
        "First [[Target]]. Second [[Target]]. Third [[Target]].",
        encoding="utf-8",
    )
    tgt_path = tmp_path / "tgt.md"
    tgt_path.write_text("Target.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source", file_path=str(src_path))
        tgt_id = _seed(idx, n=2, title="Target", file_path=str(tgt_path))
        idx.index_backlinks(src_id)

        _, edges = idx.get_graph()

    assert len(edges) == 1
    assert edges[0].weight == 3
    assert edges[0].source == f"entry:{src_id}"
    assert edges[0].target == f"entry:{tgt_id}"


def test_self_reference_skipped(tmp_path: Path) -> None:
    md = tmp_path / "self.md"
    md.write_text("I reference [[Self Note]] here.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        eid = _seed(idx, title="Self Note", file_path=str(md))
        idx.index_backlinks(eid)
        nodes, edges = idx.get_graph()

    assert nodes == []
    assert edges == []


def test_entry_node_attributes(tmp_path: Path) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("[[Target]]", encoding="utf-8")
    tgt_path = tmp_path / "tgt.md"
    tgt_path.write_text(".", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(
            idx, n=1, title="My Source", file_path=str(src_path), source_type="youtube"
        )
        _seed(idx, n=2, title="Target", file_path=str(tgt_path), source_type="article")
        idx.index_backlinks(src_id)

        nodes, _ = idx.get_graph()

    src_node = next(n for n in nodes if n.node_id == f"entry:{src_id}")
    assert src_node.label == "My Source"
    assert src_node.kind == "entry"
    assert src_node.source_type == "youtube"


def test_isolated_entry_excluded_from_graph(tmp_path: Path) -> None:
    connected_path = tmp_path / "connected.md"
    connected_path.write_text("[[Linked]]", encoding="utf-8")
    linked_path = tmp_path / "linked.md"
    linked_path.write_text(".", encoding="utf-8")
    isolated_path = tmp_path / "isolated.md"
    isolated_path.write_text("No links at all.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Connected", file_path=str(connected_path))
        lnk_id = _seed(idx, n=2, title="Linked", file_path=str(linked_path))
        _seed(idx, n=3, title="Isolated", file_path=str(isolated_path))
        idx.index_backlinks(src_id)

        nodes, _ = idx.get_graph()

    node_ids = {n.node_id for n in nodes}
    assert f"entry:{src_id}" in node_ids
    assert f"entry:{lnk_id}" in node_ids
    assert "entry:3" not in node_ids


# ---------------------------------------------------------------------------
# API endpoint — GET /entries/graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_graph_empty(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/v1/entries/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_api_graph_returns_nodes_and_edges(tmp_path: Path) -> None:
    src_path = tmp_path / "vault" / "pages" / "src.md"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_text("See [[Target Note]].", encoding="utf-8")
    tgt_path = tmp_path / "vault" / "pages" / "tgt.md"
    tgt_path.write_text("Target.", encoding="utf-8")

    app = _make_app(tmp_path)
    with TestClient(app) as client:
        index: VaultIndex = app.state.index
        src_id = _seed(index, n=1, title="Source Note", file_path=str(src_path))
        tgt_id = _seed(index, n=2, title="Target Note", file_path=str(tgt_path))
        index.index_backlinks(src_id)

        resp = client.get("/api/v1/entries/graph")

    assert resp.status_code == 200
    data = resp.json()
    node_ids = {n["node_id"] for n in data["nodes"]}
    assert f"entry:{src_id}" in node_ids
    assert f"entry:{tgt_id}" in node_ids
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["source"] == f"entry:{src_id}"
    assert edge["target"] == f"entry:{tgt_id}"
    assert edge["weight"] == 1


@pytest.mark.asyncio
async def test_api_graph_not_confused_with_entry_id(tmp_path: Path) -> None:
    """Ensure /entries/graph is not matched as /entries/{entry_id}."""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/v1/entries/graph")
    assert resp.status_code == 200
    assert "nodes" in resp.json()
