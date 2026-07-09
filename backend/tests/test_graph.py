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


def test_hashtag_resolving_to_entry_creates_both_entry_and_tag_edge(
    tmp_path: Path,
) -> None:
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
    assert "tag:python_programming" in node_ids
    assert any(
        e.source == f"entry:{src_id}" and e.target == f"entry:{tgt_id}" for e in edges
    )
    assert any(
        e.source == f"entry:{src_id}" and e.target == "tag:python_programming"
        for e in edges
    )
    tag_nodes = [n for n in nodes if n.kind == "tag"]
    assert len(tag_nodes) == 1


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


# ---------------------------------------------------------------------------
# Storage layer — get_subgraph()
# ---------------------------------------------------------------------------


def test_subgraph_none_for_missing_entry(tmp_path: Path) -> None:
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        result = idx.get_subgraph(999)
    assert result is None


def test_subgraph_focus_always_included(tmp_path: Path) -> None:
    md = tmp_path / "solo.md"
    md.write_text("No links.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        eid = _seed(idx, title="Solo", file_path=str(md))
        idx.index_backlinks(eid)
        result = idx.get_subgraph(eid)
    assert result is not None
    nodes, edges = result
    assert len(nodes) == 1
    assert nodes[0].node_id == f"entry:{eid}"
    assert nodes[0].kind == "entry"
    assert edges == []


def test_subgraph_outlink_hashtag_resolving_to_entry_creates_both_edges(
    tmp_path: Path,
) -> None:
    src_path = tmp_path / "src.md"
    src_path.write_text("Great post #python_programming", encoding="utf-8")
    tgt_path = tmp_path / "tgt.md"
    tgt_path.write_text("Python Programming content.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source", file_path=str(src_path))
        tgt_id = _seed(idx, n=2, title="Python Programming", file_path=str(tgt_path))
        idx.index_backlinks(src_id)
        result = idx.get_subgraph(src_id)

    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{tgt_id}" in node_ids
    assert "tag:python_programming" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{src_id}", f"entry:{tgt_id}") in edge_pairs
    assert (f"entry:{src_id}", "tag:python_programming") in edge_pairs
    # The target entry carries neither the structural tag nor the hashtag
    # itself, so it must not surface again as a spurious tag-hub neighbor.
    assert edge_pairs == {
        (f"entry:{src_id}", f"entry:{tgt_id}"),
        (f"entry:{src_id}", "tag:python_programming"),
    }


def test_subgraph_outlink(tmp_path: Path) -> None:
    src_md = tmp_path / "src.md"
    src_md.write_text("See [[Target]].", encoding="utf-8")
    tgt_md = tmp_path / "tgt.md"
    tgt_md.write_text("Target.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source", file_path=str(src_md))
        tgt_id = _seed(idx, n=2, title="Target", file_path=str(tgt_md))
        idx.index_backlinks(src_id)
        result = idx.get_subgraph(src_id)
    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{src_id}" in node_ids
    assert f"entry:{tgt_id}" in node_ids
    assert any(
        e.source == f"entry:{src_id}" and e.target == f"entry:{tgt_id}" for e in edges
    )


def test_subgraph_inlink(tmp_path: Path) -> None:
    src_md = tmp_path / "src.md"
    src_md.write_text("See [[Focus]].", encoding="utf-8")
    focus_md = tmp_path / "focus.md"
    focus_md.write_text("Focus content.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        src_id = _seed(idx, n=1, title="Source", file_path=str(src_md))
        focus_id = _seed(idx, n=2, title="Focus", file_path=str(focus_md))
        idx.index_backlinks(src_id)
        result = idx.get_subgraph(focus_id)
    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{focus_id}" in node_ids
    assert f"entry:{src_id}" in node_ids
    assert any(
        e.source == f"entry:{src_id}" and e.target == f"entry:{focus_id}" for e in edges
    )


def test_subgraph_bidirectional(tmp_path: Path) -> None:
    a_md = tmp_path / "a.md"
    a_md.write_text("Links to [[B]].", encoding="utf-8")
    b_md = tmp_path / "b.md"
    b_md.write_text("Links to [[A]].", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        a_id = _seed(idx, n=1, title="A", file_path=str(a_md))
        b_id = _seed(idx, n=2, title="B", file_path=str(b_md))
        idx.index_backlinks(a_id)
        idx.index_backlinks(b_id)
        result = idx.get_subgraph(a_id)
    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{a_id}" in node_ids
    assert f"entry:{b_id}" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{a_id}", f"entry:{b_id}") in edge_pairs
    assert (f"entry:{b_id}", f"entry:{a_id}") in edge_pairs


def test_subgraph_unresolved_hashtag_creates_tag_node(tmp_path: Path) -> None:
    md = tmp_path / "src.md"
    md.write_text("Topic #machine_learning today.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        eid = _seed(idx, title="Source", file_path=str(md))
        idx.index_backlinks(eid)
        result = idx.get_subgraph(eid)
    assert result is not None
    nodes, edges = result
    tag_nodes = [n for n in nodes if n.kind == "tag"]
    assert len(tag_nodes) == 1
    assert tag_nodes[0].node_id == "tag:machine_learning"
    assert any(
        e.source == f"entry:{eid}" and e.target == "tag:machine_learning" for e in edges
    )


def test_subgraph_inbound_hashtag_resolving_to_focus_title_has_no_fanout(
    tmp_path: Path,
) -> None:
    """An inbound #FocusTitle keeps its own tag node/edge on the referencing
    entry (B), but must not fan out to unrelated entries sharing that tag
    elsewhere in the vault (C) — that vault-wide view is get_graph()'s job,
    not get_subgraph()'s. Also asserts no synthetic focus->tag edge appears.
    """
    focus_md = tmp_path / "focus.md"
    focus_md.write_text("Focus content, no tags of its own.", encoding="utf-8")
    b_md = tmp_path / "b.md"
    b_md.write_text("Mentions #Python here.", encoding="utf-8")
    c_md = tmp_path / "c.md"
    c_md.write_text("Unrelated content.", encoding="utf-8")

    with VaultIndex(tmp_path / "db.sqlite") as idx:
        focus_id = _seed(idx, n=1, title="Python", file_path=str(focus_md))
        b_id = _seed(idx, n=2, title="Entry B", file_path=str(b_md))
        c_id = _seed(idx, n=3, title="Entry C", file_path=str(c_md))
        idx.index_backlinks(b_id)
        idx.update_tags(c_id, ["python"])

        sub_result = idx.get_subgraph(focus_id)
        graph_nodes, graph_edges = idx.get_graph()

    assert sub_result is not None
    sub_nodes, sub_edges = sub_result
    sub_node_ids = {n.node_id for n in sub_nodes}
    assert "tag:python" in sub_node_ids
    sub_edge_pairs = {(e.source, e.target) for e in sub_edges}
    assert (f"entry:{b_id}", "tag:python") in sub_edge_pairs
    assert (f"entry:{focus_id}", "tag:python") not in sub_edge_pairs
    assert f"entry:{c_id}" not in sub_node_ids

    # get_graph()'s vault-wide view, by contrast, does fan tag:python out to
    # C — proving the asymmetry above is intentional, not accidentally
    # suppressed everywhere.
    graph_node_ids = {n.node_id for n in graph_nodes}
    assert f"entry:{c_id}" in graph_node_ids
    graph_edge_pairs = {(e.source, e.target) for e in graph_edges}
    assert (f"entry:{c_id}", "tag:python") in graph_edge_pairs


# ---------------------------------------------------------------------------
# API endpoint — GET /entries/{entry_id}/subgraph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_subgraph_404_unknown_entry(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/v1/entries/999/subgraph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_subgraph_isolated_entry(tmp_path: Path) -> None:
    md = tmp_path / "vault" / "pages" / "solo.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("No links.", encoding="utf-8")
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        index: VaultIndex = app.state.index
        eid = _seed(index, title="Solo", file_path=str(md))
        index.index_backlinks(eid)
        resp = client.get(f"/api/v1/entries/{eid}/subgraph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["focus_node_id"] == f"entry:{eid}"
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["node_id"] == f"entry:{eid}"
    assert data["edges"] == []


@pytest.mark.asyncio
async def test_api_subgraph_with_neighbors(tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "pages"
    vault.mkdir(parents=True, exist_ok=True)
    focus_md = vault / "focus.md"
    focus_md.write_text("See [[Neighbor]].", encoding="utf-8")
    nbr_md = vault / "nbr.md"
    nbr_md.write_text("Neighbor content.", encoding="utf-8")
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        index: VaultIndex = app.state.index
        focus_id = _seed(index, n=1, title="Focus", file_path=str(focus_md))
        nbr_id = _seed(index, n=2, title="Neighbor", file_path=str(nbr_md))
        index.index_backlinks(focus_id)
        resp = client.get(f"/api/v1/entries/{focus_id}/subgraph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["focus_node_id"] == f"entry:{focus_id}"
    node_ids = {n["node_id"] for n in data["nodes"]}
    assert f"entry:{focus_id}" in node_ids
    assert f"entry:{nbr_id}" in node_ids
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["source"] == f"entry:{focus_id}"
    assert edge["target"] == f"entry:{nbr_id}"


# ---------------------------------------------------------------------------
# Tag-hub topology — get_graph() via entry_tags
# ---------------------------------------------------------------------------


def test_entry_tag_creates_hub_edge(tmp_path: Path) -> None:
    md = tmp_path / "note.md"
    md.write_text("Content.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        eid = _seed(idx, title="Tagged Entry", file_path=str(md))
        idx.update_tags(eid, ["python"])
        nodes, edges = idx.get_graph()
    tag_nodes = [n for n in nodes if n.kind == "tag"]
    entry_nodes = [n for n in nodes if n.kind == "entry"]
    assert len(tag_nodes) == 1
    assert tag_nodes[0].node_id == "tag:python"
    assert len(entry_nodes) == 1
    assert entry_nodes[0].node_id == f"entry:{eid}"
    assert len(edges) == 1
    assert edges[0].source == f"entry:{eid}"
    assert edges[0].target == "tag:python"


def test_shared_tag_connects_entries_via_hub(tmp_path: Path) -> None:
    md_a = tmp_path / "a.md"
    md_a.write_text("A content.", encoding="utf-8")
    md_b = tmp_path / "b.md"
    md_b.write_text("B content.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        a_id = _seed(idx, n=1, title="Article A", file_path=str(md_a))
        b_id = _seed(idx, n=2, title="Article B", file_path=str(md_b))
        idx.update_tags(a_id, ["python"])
        idx.update_tags(b_id, ["python"])
        nodes, edges = idx.get_graph()
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{a_id}" in node_ids
    assert f"entry:{b_id}" in node_ids
    assert "tag:python" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{a_id}", "tag:python") in edge_pairs
    assert (f"entry:{b_id}", "tag:python") in edge_pairs


# ---------------------------------------------------------------------------
# Tag-hub topology — get_subgraph() via entry_tags
# ---------------------------------------------------------------------------


def test_subgraph_includes_own_structured_tags(tmp_path: Path) -> None:
    md = tmp_path / "focus.md"
    md.write_text("Content.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        eid = _seed(idx, title="Focus", file_path=str(md))
        idx.update_tags(eid, ["python", "ml"])
        result = idx.get_subgraph(eid)
    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert "tag:python" in node_ids
    assert "tag:ml" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{eid}", "tag:python") in edge_pairs
    assert (f"entry:{eid}", "tag:ml") in edge_pairs


def test_subgraph_tag_neighbors_included(tmp_path: Path) -> None:
    md_focus = tmp_path / "focus.md"
    md_focus.write_text("Focus content.", encoding="utf-8")
    md_nbr = tmp_path / "nbr.md"
    md_nbr.write_text("Neighbor content.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        focus_id = _seed(idx, n=1, title="Focus", file_path=str(md_focus))
        nbr_id = _seed(idx, n=2, title="Neighbor", file_path=str(md_nbr))
        idx.update_tags(focus_id, ["python"])
        idx.update_tags(nbr_id, ["python"])
        result = idx.get_subgraph(focus_id)
    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{focus_id}" in node_ids
    assert f"entry:{nbr_id}" in node_ids
    assert "tag:python" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{focus_id}", "tag:python") in edge_pairs
    assert (f"entry:{nbr_id}", "tag:python") in edge_pairs


# ---------------------------------------------------------------------------
# Tag identity unification — structural tags vs. content hashtags
# ---------------------------------------------------------------------------


def test_graph_unifies_structural_tag_and_content_hashtag(tmp_path: Path) -> None:
    struct_md = tmp_path / "struct.md"
    struct_md.write_text("Structural entry content.", encoding="utf-8")
    hashtag_md = tmp_path / "hashtag.md"
    hashtag_md.write_text("Body mentions #python here.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        struct_id = _seed(idx, n=1, title="Structural Entry", file_path=str(struct_md))
        hashtag_id = _seed(idx, n=2, title="Hashtag Entry", file_path=str(hashtag_md))
        idx.update_tags(struct_id, ["Python"])
        idx.index_backlinks(hashtag_id)
        nodes, edges = idx.get_graph()

    tag_nodes = [n for n in nodes if n.kind == "tag"]
    assert len(tag_nodes) == 1
    assert tag_nodes[0].node_id == "tag:python"
    assert tag_nodes[0].label == "#Python"  # structural casing wins
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{struct_id}", "tag:python") in edge_pairs
    assert (f"entry:{hashtag_id}", "tag:python") in edge_pairs


def test_subgraph_lists_hashtag_only_entry_as_neighbor_of_structural_tag(
    tmp_path: Path,
) -> None:
    struct_md = tmp_path / "struct.md"
    struct_md.write_text("Structural entry content.", encoding="utf-8")
    hashtag_md = tmp_path / "hashtag.md"
    hashtag_md.write_text("Body mentions #python here.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        struct_id = _seed(idx, n=1, title="Structural Entry", file_path=str(struct_md))
        hashtag_id = _seed(idx, n=2, title="Hashtag Entry", file_path=str(hashtag_md))
        idx.update_tags(struct_id, ["Python"])
        idx.index_backlinks(hashtag_id)
        result = idx.get_subgraph(struct_id)

    assert result is not None
    nodes, edges = result
    node_ids = {n.node_id for n in nodes}
    assert f"entry:{hashtag_id}" in node_ids
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{struct_id}", "tag:python") in edge_pairs
    assert (f"entry:{hashtag_id}", "tag:python") in edge_pairs


def test_symbol_bearing_tag_does_not_collide_with_stripped_hashtag(
    tmp_path: Path,
) -> None:
    struct_md = tmp_path / "struct.md"
    struct_md.write_text("Structural entry content.", encoding="utf-8")
    hashtag_md = tmp_path / "hashtag.md"
    hashtag_md.write_text("About #c programming.", encoding="utf-8")
    with VaultIndex(tmp_path / "db.sqlite") as idx:
        struct_id = _seed(idx, n=1, title="Structural Entry", file_path=str(struct_md))
        hashtag_id = _seed(idx, n=2, title="Hashtag Entry", file_path=str(hashtag_md))
        idx.update_tags(struct_id, ["C++"])
        idx.index_backlinks(hashtag_id)
        nodes, edges = idx.get_graph()
        subgraph = idx.get_subgraph(struct_id)

    tag_nodes = {n.node_id: n.label for n in nodes if n.kind == "tag"}
    assert tag_nodes == {"tag:c++": "#C++", "tag:c": "#c"}
    edge_pairs = {(e.source, e.target) for e in edges}
    assert (f"entry:{struct_id}", "tag:c++") in edge_pairs
    assert (f"entry:{hashtag_id}", "tag:c") in edge_pairs
    assert (f"entry:{struct_id}", "tag:c") not in edge_pairs
    assert (f"entry:{hashtag_id}", "tag:c++") not in edge_pairs

    assert subgraph is not None
    sub_nodes, _ = subgraph
    sub_node_ids = {n.node_id for n in sub_nodes}
    assert f"entry:{hashtag_id}" not in sub_node_ids
