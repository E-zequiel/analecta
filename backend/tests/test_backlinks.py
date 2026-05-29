"""Tests for the backlinks parser, storage layer, and API endpoint."""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from analecta.api.events import EventBus
from analecta.api.routes.entries import router as entries_router
from analecta.config import AppConfig
from analecta.markdown.backlinks import parse_refs
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
) -> int:
    return index.add_entry(
        EntryRecord(
            title=title or f"Article {n}",
            url=f"https://example.com/{n}",
            file_path=file_path or f"/vault/pages/article-{n}.md",
            source_type="article",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )


def _make_app(tmp_path: Path) -> FastAPI:
    config = AppConfig(vault_path=tmp_path / "vault")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        index = VaultIndex(config.vault_path / "analecta.db")
        app.state.config = config
        app.state.index = index
        app.state.event_bus = EventBus()
        yield
        index.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(entries_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# parse_refs — unit tests
# ---------------------------------------------------------------------------


class TestParseRefs:
    def test_wikilink_basic(self) -> None:
        refs = parse_refs("See also [[Python Tutorial]] for details.")
        assert len(refs) == 1
        r = refs[0]
        assert r.target_text == "python tutorial"
        assert r.is_hashtag is False
        assert r.highlight == "[[Python Tutorial]]"
        assert r.heading is None

    def test_wikilink_aliased(self) -> None:
        refs = parse_refs("See [[Python Tutorial|this guide]] for details.")
        assert len(refs) == 1
        assert refs[0].target_text == "python tutorial"
        assert refs[0].highlight == "[[Python Tutorial|this guide]]"

    def test_hashtag_basic(self) -> None:
        refs = parse_refs("Great article about #python and its ecosystem.")
        assert len(refs) == 1
        r = refs[0]
        assert r.target_text == "python"
        assert r.is_hashtag is True
        assert r.highlight == "#python"

    def test_hashtag_normalized(self) -> None:
        refs = parse_refs("Discussing #MachineLearning concepts.")
        assert len(refs) == 1
        assert refs[0].target_text == "machinelearning"

    def test_hashtag_snake_case(self) -> None:
        refs = parse_refs("Topic: #machine_learning is important.")
        assert len(refs) == 1
        assert refs[0].target_text == "machine_learning"

    def test_heading_context_tracked(self) -> None:
        md = "## Introduction\n\nSee [[Python Tutorial]] here.\n"
        refs = parse_refs(md)
        assert len(refs) == 1
        assert refs[0].heading == "Introduction"

    def test_heading_updates_on_new_section(self) -> None:
        md = (
            "## First Section\n\nSee [[Alpha]].\n\n## Second Section\n\nSee [[Beta]].\n"
        )
        refs = parse_refs(md)
        assert len(refs) == 2
        assert refs[0].heading == "First Section"
        assert refs[1].heading == "Second Section"

    def test_no_heading_is_none(self) -> None:
        refs = parse_refs("Intro text with [[Alpha]] before any heading.\n")
        assert refs[0].heading is None

    def test_heading_line_not_parsed(self) -> None:
        refs = parse_refs("## [[Not a backlink]] heading\n")
        assert refs == []

    def test_code_fence_skipped(self) -> None:
        md = (
            "Normal line with [[Alpha]].\n"
            "```\n[[Ignored]]\n#ignored\n```\nAfter fence.\n"
        )
        refs = parse_refs(md)
        assert len(refs) == 1
        assert refs[0].target_text == "alpha"

    def test_frontmatter_skipped(self) -> None:
        md = "---\ntitle: Test\nurl: https://example.com\n---\n\nBody with [[Alpha]].\n"
        refs = parse_refs(md)
        assert len(refs) == 1
        assert refs[0].target_text == "alpha"

    def test_context_snippet(self) -> None:
        md = "The quick brown fox jumps over [[Alpha]] and lands safely.\n"
        refs = parse_refs(md)
        assert refs[0].pre == "The quick brown fox jumps over"
        assert refs[0].post == "and lands safely."

    def test_multiple_refs_same_line(self) -> None:
        refs = parse_refs("See [[Alpha]] and also [[Beta]] today.\n")
        assert len(refs) == 2
        assert refs[0].target_text == "alpha"
        assert refs[1].target_text == "beta"

    def test_wikilink_and_hashtag_same_line(self) -> None:
        refs = parse_refs("Read [[Alpha]] for #python tips.\n")
        assert len(refs) == 2
        wikilinks = [r for r in refs if not r.is_hashtag]
        hashtags = [r for r in refs if r.is_hashtag]
        assert wikilinks[0].target_text == "alpha"
        assert hashtags[0].target_text == "python"

    def test_no_refs_empty_document(self) -> None:
        assert parse_refs("") == []

    def test_hashtag_not_matched_mid_word(self) -> None:
        refs = parse_refs("Visit https://example.com/path#section for more.\n")
        assert refs == []

    def test_multiple_occurrences_of_same_target(self) -> None:
        md = "## Intro\n\nSee [[Alpha]].\n\n## Details\n\nAlso [[Alpha]] here.\n"
        refs = parse_refs(md)
        assert len(refs) == 2
        assert refs[0].heading == "Intro"
        assert refs[1].heading == "Details"


# ---------------------------------------------------------------------------
# VaultIndex.index_backlinks / get_backlinks — integration tests
# ---------------------------------------------------------------------------


class TestVaultIndexBacklinks:
    def test_index_and_retrieve_wikilink(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="Python Tutorial")
        src_file = vault / "article-2.md"
        src_file.write_text("See [[Python Tutorial]] for details.\n", encoding="utf-8")
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_backlinks(target_id)

        assert len(results) == 1
        assert results[0].source_id == src_id
        assert results[0].source_title == "Article 2"
        assert results[0].highlight == "[[Python Tutorial]]"
        db.close()

    def test_index_and_retrieve_hashtag(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="python")
        src_file = vault / "article-2.md"
        src_file.write_text("Great article about #python.\n", encoding="utf-8")
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_backlinks(target_id)

        assert len(results) == 1
        assert results[0].source_id == src_id
        assert results[0].highlight == "#python"
        db.close()

    def test_hashtag_matches_titled_entry_via_normalize(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="Machine Learning")
        src_file = vault / "article-2.md"
        src_file.write_text("Topic: #machine_learning is key.\n", encoding="utf-8")
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_backlinks(target_id)

        assert len(results) == 1
        assert results[0].source_id == src_id
        db.close()

    def test_no_self_backlinks(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text(
            "This entry [[Article 1]] mentions itself.\n", encoding="utf-8"
        )
        src_id = _seed(db, n=1, title="Article 1", file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_backlinks(src_id)

        assert results == []
        db.close()

    def test_reindex_clears_stale_refs(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_a = _seed(db, n=1, title="Alpha")
        target_b = _seed(db, n=2, title="Beta")
        src_file = vault / "article-3.md"
        src_file.write_text("See [[Alpha]].\n", encoding="utf-8")
        src_id = _seed(db, n=3, file_path=str(src_file))

        db.index_backlinks(src_id)
        assert len(db.get_backlinks(target_a)) == 1

        # Edit the file — now links to Beta instead
        src_file.write_text("See [[Beta]].\n", encoding="utf-8")
        db.index_backlinks(src_id)

        assert db.get_backlinks(target_a) == []
        assert len(db.get_backlinks(target_b)) == 1
        db.close()

    def test_multiple_occurrences_per_source(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="Python Tutorial")
        src_file = vault / "article-2.md"
        src_file.write_text(
            "## Intro\n\nSee [[Python Tutorial]].\n\n"
            "## Details\n\nAlso [[Python Tutorial]] here.\n",
            encoding="utf-8",
        )
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_backlinks(target_id)

        assert len(results) == 2
        headings = {r.heading for r in results}
        assert headings == {"Intro", "Details"}
        db.close()

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_id = _seed(db, n=1, file_path="/nonexistent/path/article.md")
        db.index_backlinks(src_id)  # must not raise
        assert db.get_backlinks(src_id) == []
        db.close()

    def test_unresolved_wikilink_not_returned(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text("See [[Future Note]] someday.\n", encoding="utf-8")
        src_id = _seed(db, n=1, file_path=str(src_file))

        db.index_backlinks(src_id)
        # No entry titled "Future Note" exists — backlinks of src itself are empty
        assert db.get_backlinks(src_id) == []
        db.close()

    def test_forward_ref_resolves_after_entry_created(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text("See [[Future Note]] for details.\n", encoding="utf-8")
        src_id = _seed(db, n=1, file_path=str(src_file))
        db.index_backlinks(src_id)

        # Now create the target entry
        target_id = _seed(db, n=2, title="Future Note")
        results = db.get_backlinks(target_id)

        assert len(results) == 1
        assert results[0].source_id == src_id
        db.close()


# ---------------------------------------------------------------------------
# API endpoint — GET /entries/{id}/backlinks
# ---------------------------------------------------------------------------


class TestBacklinksEndpoint:
    @pytest.fixture
    def client(self, tmp_path: Path) -> Generator[TestClient]:
        with TestClient(_make_app(tmp_path)) as c:
            yield c

    def test_404_unknown_entry(self, client: TestClient) -> None:
        resp = client.get("/api/v1/entries/9999/backlinks")
        assert resp.status_code == 404

    def test_empty_backlinks(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            _seed(index, n=1, title="Lonely Entry")
        with TestClient(app) as c:
            resp = c.get("/api/v1/entries/1/backlinks")
        assert resp.status_code == 200
        assert resp.json() == {"linked": []}

    def test_backlinks_populated(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            target_id = _seed(index, n=1, title="Python Tutorial")
            src_file = vault / "article-2.md"
            src_file.write_text(
                "## Intro\n\nRead [[Python Tutorial]] first.\n", encoding="utf-8"
            )
            src_id = _seed(index, n=2, file_path=str(src_file))
            index.index_backlinks(src_id)

        with TestClient(app) as c:
            resp = c.get(f"/api/v1/entries/{target_id}/backlinks")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["linked"]) == 1
        item = data["linked"][0]
        assert item["id"] == src_id
        assert item["name"] == "Article 2"
        assert item["context"]["heading"] == "Intro"
        assert item["context"]["highlight"] == "[[Python Tutorial]]"

    def test_multiple_occurrences_returned(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            target_id = _seed(index, n=1, title="Alpha")
            src_file = vault / "article-2.md"
            src_file.write_text(
                "## Intro\n\nSee [[Alpha]].\n\n## Details\n\n[[Alpha]] again.\n",
                encoding="utf-8",
            )
            src_id = _seed(index, n=2, file_path=str(src_file))
            index.index_backlinks(src_id)

        with TestClient(app) as c:
            resp = c.get(f"/api/v1/entries/{target_id}/backlinks")

        assert resp.status_code == 200
        linked = resp.json()["linked"]
        assert len(linked) == 2
        assert all(item["id"] == src_id for item in linked)
        headings = {item["context"]["heading"] for item in linked}
        assert headings == {"Intro", "Details"}
