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

    def test_wikilink_in_inline_code_skipped(self) -> None:
        refs = parse_refs("Use `[[Not A Link]]` as a literal example.\n")
        assert refs == []

    def test_hashtag_in_inline_code_skipped(self) -> None:
        refs = parse_refs("Try `echo #not_a_tag` in your shell.\n")
        assert refs == []

    def test_hashtag_flush_against_backtick_still_skipped(self) -> None:
        # Already worked before the fix (via the hashtag lookbehind);
        # must still hold now that masking is the mechanism.
        refs = parse_refs("Run `#not_a_tag` literally.\n")
        assert refs == []

    def test_wikilink_outside_inline_code_still_matched(self) -> None:
        refs = parse_refs("See [[Alpha]] and also `[[Not A Link]]` here.\n")
        assert len(refs) == 1
        assert refs[0].target_text == "alpha"

    def test_dangling_backtick_does_not_suppress_real_refs(self) -> None:
        refs = parse_refs("It's not `code but has [[Alpha]] after.\n")
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
# VaultIndex.get_outgoing_links — integration tests
# ---------------------------------------------------------------------------


class TestVaultIndexOutgoingLinks:
    def test_wikilink_resolves_to_target(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="Python Tutorial")
        src_file = vault / "article-2.md"
        src_file.write_text("See [[Python Tutorial]] for details.\n", encoding="utf-8")
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_outgoing_links(src_id)

        assert len(results) == 1
        assert results[0].target_id == target_id
        assert results[0].target_title == "Python Tutorial"
        assert results[0].highlight == "[[Python Tutorial]]"
        db.close()

    def test_hashtag_resolves_to_titled_entry(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_id = _seed(db, n=1, title="Machine Learning")
        src_file = vault / "article-2.md"
        src_file.write_text("Topic: #machine_learning is key.\n", encoding="utf-8")
        src_id = _seed(db, n=2, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_outgoing_links(src_id)

        assert len(results) == 1
        assert results[0].target_id == target_id
        db.close()

    def test_unresolved_wikilink_skipped(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text("See [[Future Note]] someday.\n", encoding="utf-8")
        src_id = _seed(db, n=1, file_path=str(src_file))

        db.index_backlinks(src_id)
        assert db.get_outgoing_links(src_id) == []
        db.close()

    def test_unresolved_hashtag_skipped(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text("Filed under #nonexistent.\n", encoding="utf-8")
        src_id = _seed(db, n=1, file_path=str(src_file))

        db.index_backlinks(src_id)
        assert db.get_outgoing_links(src_id) == []
        db.close()

    def test_no_self_link(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        src_file = vault / "article-1.md"
        src_file.write_text(
            "This entry [[Article 1]] mentions itself.\n", encoding="utf-8"
        )
        src_id = _seed(db, n=1, title="Article 1", file_path=str(src_file))

        db.index_backlinks(src_id)
        assert db.get_outgoing_links(src_id) == []
        db.close()

    def test_multiple_targets_ordered_by_title(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        beta_id = _seed(db, n=1, title="Beta")
        alpha_id = _seed(db, n=2, title="Alpha")
        src_file = vault / "article-3.md"
        src_file.write_text("See [[Beta]] and [[Alpha]].\n", encoding="utf-8")
        src_id = _seed(db, n=3, file_path=str(src_file))

        db.index_backlinks(src_id)
        results = db.get_outgoing_links(src_id)

        assert [r.target_id for r in results] == [alpha_id, beta_id]
        db.close()

    def test_reindex_clears_stale_outgoing_links(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        target_a = _seed(db, n=1, title="Alpha")
        target_b = _seed(db, n=2, title="Beta")
        src_file = vault / "article-3.md"
        src_file.write_text("See [[Alpha]].\n", encoding="utf-8")
        src_id = _seed(db, n=3, file_path=str(src_file))

        db.index_backlinks(src_id)
        assert [r.target_id for r in db.get_outgoing_links(src_id)] == [target_a]

        src_file.write_text("See [[Beta]].\n", encoding="utf-8")
        db.index_backlinks(src_id)

        assert [r.target_id for r in db.get_outgoing_links(src_id)] == [target_b]
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


# ---------------------------------------------------------------------------
# API endpoint — GET /entries/{id}/outgoing-links
# ---------------------------------------------------------------------------


class TestOutgoingLinksEndpoint:
    @pytest.fixture
    def client(self, tmp_path: Path) -> Generator[TestClient]:
        with TestClient(_make_app(tmp_path)) as c:
            yield c

    def test_404_unknown_entry(self, client: TestClient) -> None:
        resp = client.get("/api/v1/entries/9999/outgoing-links")
        assert resp.status_code == 404

    def test_empty_outgoing_links(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            _seed(index, n=1, title="Lonely Entry")
        with TestClient(app) as c:
            resp = c.get("/api/v1/entries/1/outgoing-links")
        assert resp.status_code == 200
        assert resp.json() == {"linked": []}

    def test_outgoing_links_populated(self, tmp_path: Path) -> None:
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
            resp = c.get(f"/api/v1/entries/{src_id}/outgoing-links")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["linked"]) == 1
        item = data["linked"][0]
        assert item["id"] == target_id
        assert item["name"] == "Python Tutorial"
        assert item["context"]["heading"] == "Intro"
        assert item["context"]["highlight"] == "[[Python Tutorial]]"


# ---------------------------------------------------------------------------
# parse_refs — frontmatter linked: field
# ---------------------------------------------------------------------------


class TestParseRefsLinked:
    def test_linked_frontmatter_parsed(self) -> None:
        md = "---\ntitle: A\nlinked:\n- Beta\n- Gamma\n---\n\nBody.\n"
        refs = parse_refs(md)
        linked_refs = [r for r in refs if r.target_text in ("beta", "gamma")]
        assert len(linked_refs) == 2

    def test_linked_refs_come_first(self) -> None:
        md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nSee [[Alpha]].\n"
        refs = parse_refs(md)
        assert refs[0].target_text == "beta"
        assert refs[1].target_text == "alpha"

    def test_linked_refs_not_hashtags(self) -> None:
        md = "---\ntitle: A\nlinked:\n- Beta\n---\n\nBody.\n"
        refs = parse_refs(md)
        linked = [r for r in refs if r.target_text == "beta"]
        assert linked[0].is_hashtag is False

    def test_linked_highlight_is_wikilink_format(self) -> None:
        md = "---\ntitle: A\nlinked:\n- Beta Article\n---\n\nBody.\n"
        refs = parse_refs(md)
        linked = [r for r in refs if r.target_text == "beta article"]
        assert linked[0].highlight == "[[Beta Article]]"

    def test_no_linked_field_no_extra_refs(self) -> None:
        md = "---\ntitle: A\nurl: https://x.com\n---\n\nSee [[Alpha]].\n"
        refs = parse_refs(md)
        assert len(refs) == 1
        assert refs[0].target_text == "alpha"

    def test_empty_linked_field_no_extra_refs(self) -> None:
        md = "---\ntitle: A\nlinked: []\n---\n\nSee [[Alpha]].\n"
        refs = parse_refs(md)
        assert len(refs) == 1


# ---------------------------------------------------------------------------
# VaultIndex.get_linked_entries / add_link / remove_link
# ---------------------------------------------------------------------------


def _make_file(vault: Path, n: int, linked: list[str] | None = None) -> Path:
    parts = ["---\n", f"title: Article {n}\n", "url: https://example.com\n"]
    if linked:
        parts.append("linked:\n")
        for t in linked:
            parts.append(f"- {t}\n")
    parts.append("---\n\nBody.\n")
    fp = vault / f"article-{n}.md"
    fp.write_text("".join(parts), encoding="utf-8")
    return fp


class TestLinkedStorage:
    def test_get_linked_entries_basic(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        id_b = _seed(db, n=2, title="Article 2")
        fp_a = _make_file(vault, 1, linked=["Article 2"])
        id_a = _seed(db, n=1, file_path=str(fp_a))

        result = db.get_linked_entries(id_a)
        assert len(result) == 1
        assert result[0].id == id_b
        db.close()

    def test_get_linked_entries_empty(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1)
        id_a = _seed(db, n=1, file_path=str(fp_a))

        assert db.get_linked_entries(id_a) == []
        db.close()

    def test_get_linked_entries_unknown_title_skipped(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1, linked=["Nonexistent Title"])
        id_a = _seed(db, n=1, file_path=str(fp_a))

        assert db.get_linked_entries(id_a) == []
        db.close()

    def test_add_link_bidirectional(self, tmp_path: Path) -> None:
        import yaml as _yaml

        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1)
        fp_b = _make_file(vault, 2)
        id_a = _seed(db, n=1, title="Article 1", file_path=str(fp_a))
        id_b = _seed(db, n=2, title="Article 2", file_path=str(fp_b))

        db.add_link(id_a, id_b)

        # Both files should list each other
        fm_a = _yaml.safe_load(fp_a.read_text().split("---\n", 2)[1])
        fm_b = _yaml.safe_load(fp_b.read_text().split("---\n", 2)[1])
        assert "Article 2" in fm_a.get("linked", [])
        assert "Article 1" in fm_b.get("linked", [])
        db.close()

    def test_add_link_idempotent(self, tmp_path: Path) -> None:
        import yaml as _yaml

        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1)
        fp_b = _make_file(vault, 2)
        id_a = _seed(db, n=1, title="Article 1", file_path=str(fp_a))
        id_b = _seed(db, n=2, title="Article 2", file_path=str(fp_b))

        db.add_link(id_a, id_b)
        db.add_link(id_a, id_b)

        fm_a = _yaml.safe_load(fp_a.read_text().split("---\n", 2)[1])
        assert fm_a["linked"].count("Article 2") == 1
        db.close()

    def test_remove_link_bidirectional(self, tmp_path: Path) -> None:
        import yaml as _yaml

        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1, linked=["Article 2"])
        fp_b = _make_file(vault, 2, linked=["Article 1"])
        id_a = _seed(db, n=1, title="Article 1", file_path=str(fp_a))
        id_b = _seed(db, n=2, title="Article 2", file_path=str(fp_b))

        db.remove_link(id_a, id_b)

        fm_a = _yaml.safe_load(fp_a.read_text().split("---\n", 2)[1])
        fm_b = _yaml.safe_load(fp_b.read_text().split("---\n", 2)[1])
        assert "linked" not in fm_a
        assert "linked" not in fm_b
        db.close()

    def test_add_link_reindexes_backlinks(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        fp_a = _make_file(vault, 1)
        fp_b = _make_file(vault, 2)
        id_a = _seed(db, n=1, title="Article 1", file_path=str(fp_a))
        id_b = _seed(db, n=2, title="Article 2", file_path=str(fp_b))

        db.add_link(id_a, id_b)

        # Article 2 should appear in backlinks of Article 1 (via frontmatter linked)
        bl = db.get_backlinks(id_a)
        source_ids = {r.source_id for r in bl}
        assert id_b in source_ids
        db.close()


# ---------------------------------------------------------------------------
# API endpoints — linked
# ---------------------------------------------------------------------------


class TestLinkedEndpoints:
    @pytest.fixture
    def client(self, tmp_path: Path) -> Generator[TestClient]:
        with TestClient(_make_app(tmp_path)) as c:
            yield c

    def test_get_linked_404_unknown(self, client: TestClient) -> None:
        resp = client.get("/api/v1/entries/9999/linked")
        assert resp.status_code == 404

    def test_get_linked_empty(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            fp = _make_file(vault, 1)
            id_a = _seed(index, n=1, file_path=str(fp))
        with TestClient(app) as c:
            resp = c.get(f"/api/v1/entries/{id_a}/linked")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_post_link_creates_connection(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            fp_a = _make_file(vault, 1)
            fp_b = _make_file(vault, 2)
            id_a = _seed(index, n=1, title="Article 1", file_path=str(fp_a))
            id_b = _seed(index, n=2, title="Article 2", file_path=str(fp_b))
        with TestClient(app) as c:
            resp = c.post(f"/api/v1/entries/{id_a}/link/{id_b}")
            assert resp.status_code == 204
            linked = c.get(f"/api/v1/entries/{id_a}/linked").json()
        assert any(e["id"] == id_b for e in linked)

    def test_post_link_404_source(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            fp_b = _make_file(vault, 2)
            id_b = _seed(index, n=2, title="Article 2", file_path=str(fp_b))
        with TestClient(app) as c:
            resp = c.post(f"/api/v1/entries/9999/link/{id_b}")
        assert resp.status_code == 404

    def test_post_link_404_target(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            fp_a = _make_file(vault, 1)
            id_a = _seed(index, n=1, title="Article 1", file_path=str(fp_a))
        with TestClient(app) as c:
            resp = c.post(f"/api/v1/entries/{id_a}/link/9999")
        assert resp.status_code == 404

    def test_delete_link_removes_connection(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            fp_a = _make_file(vault, 1, linked=["Article 2"])
            fp_b = _make_file(vault, 2, linked=["Article 1"])
            id_a = _seed(index, n=1, title="Article 1", file_path=str(fp_a))
            id_b = _seed(index, n=2, title="Article 2", file_path=str(fp_b))
        with TestClient(app) as c:
            resp = c.delete(f"/api/v1/entries/{id_a}/link/{id_b}")
            assert resp.status_code == 204
            linked = c.get(f"/api/v1/entries/{id_a}/linked").json()
        assert linked == []


# ---------------------------------------------------------------------------
# VaultIndex.get_hashtag_connections — integration tests
# ---------------------------------------------------------------------------


class TestBacklinksBootstrap:
    def test_bootstrap_indexes_unindexed_entries(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db_path = tmp_path / "vault" / "analecta.db"

        # First open: entries added but index_backlinks never called
        db = VaultIndex(db_path)
        f1 = vault / "a1.md"
        f1.write_text("About #python.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))
        f2 = vault / "a2.md"
        f2.write_text("Also #python.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))
        db.close()

        # Remove the bootstrap marker to simulate a fresh re-open where
        # the one-time migration has not run for these entries yet.
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = 'py:008_backlinks_bootstrap'"
        )
        conn.commit()
        conn.close()

        # Second open: bootstrap runs, finds the two entries with empty
        # backlink_refs, indexes them.
        db2 = VaultIndex(db_path)
        groups = db2.get_hashtag_connections(id1)
        db2.close()

        assert len(groups) == 1
        assert groups[0].hashtag == "python"
        assert len(groups[0].entries) == 1
        assert groups[0].entries[0].id == id2

    def test_bootstrap_marker_prevents_rerun(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)

        db_path = tmp_path / "vault" / "analecta.db"

        db = VaultIndex(db_path)
        f1 = vault / "a1.md"
        f1.write_text("About #python.\n", encoding="utf-8")
        _seed(db, n=1, file_path=str(f1))
        db.close()

        # Remove marker to force bootstrap on re-open
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = 'py:008_backlinks_bootstrap'"
        )
        conn.commit()
        conn.close()

        db2 = VaultIndex(db_path)
        db2.close()

        # Marker should now be present — third open won't run bootstrap again
        conn2 = _sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT version FROM schema_migrations "
            "WHERE version = 'py:008_backlinks_bootstrap'"
        ).fetchone()
        conn2.close()

        assert row is not None


class TestHashtagConnections:
    # --- content hashtags via backlink_refs ---

    def test_shared_hashtag_groups_peer_entry(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("About #python and its ecosystem.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        f2 = vault / "a2.md"
        f2.write_text("Using #python for data science.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))

        db.index_backlinks(id1)
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert groups[0].hashtag == "python"
        assert len(groups[0].entries) == 1
        assert groups[0].entries[0].id == id2
        db.close()

    def test_no_shared_tags_returns_empty(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("About #rust only.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        f2 = vault / "a2.md"
        f2.write_text("About #python only.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))

        db.index_backlinks(id1)
        db.index_backlinks(id2)

        assert db.get_hashtag_connections(id1) == []
        db.close()

    def test_self_excluded_from_group(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("Talking about #python.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        db.index_backlinks(id1)

        assert db.get_hashtag_connections(id1) == []
        db.close()

    def test_multiple_content_hashtag_groups(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("Topics: #python and #ml.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        f2 = vault / "a2.md"
        f2.write_text("Using #python for #ml projects.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))

        db.index_backlinks(id1)
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 2
        hashtags = {g.hashtag for g in groups}
        assert hashtags == {"ml", "python"}
        for g in groups:
            assert len(g.entries) == 1
            assert g.entries[0].id == id2
        db.close()

    def test_each_entry_appears_once_per_group(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("About #python.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        f2 = vault / "a2.md"
        f2.write_text("Using #python here and #python there.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))

        db.index_backlinks(id1)
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert len(groups[0].entries) == 1
        assert groups[0].entries[0].id == id2
        db.close()

    def test_no_tags_no_backlink_refs_returns_empty(self, tmp_path: Path) -> None:
        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        id1 = _seed(db, n=1)
        assert db.get_hashtag_connections(id1) == []
        db.close()

    # --- structural tags via entry_tags ---

    def test_structural_tag_finds_peer(self, tmp_path: Path) -> None:
        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        id1 = _seed(db, n=1)
        id2 = _seed(db, n=2)
        db.update_tags(id1, ["security"])
        db.update_tags(id2, ["security"])

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert groups[0].hashtag == "security"
        assert groups[0].entries[0].id == id2
        db.close()

    def test_structural_tag_case_insensitive(self, tmp_path: Path) -> None:
        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        id1 = _seed(db, n=1)
        id2 = _seed(db, n=2)
        # "Python" (capital) vs "python" (lowercase) — treated as same tag
        db.update_tags(id1, ["Python"])
        db.update_tags(id2, ["python"])

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert groups[0].hashtag == "Python"  # display name from source entry
        assert groups[0].entries[0].id == id2
        db.close()

    def test_structural_tag_no_peers_excluded(self, tmp_path: Path) -> None:
        db = VaultIndex(tmp_path / "vault" / "analecta.db")
        id1 = _seed(db, n=1)
        db.update_tags(id1, ["unique_tag"])

        assert db.get_hashtag_connections(id1) == []
        db.close()

    def test_structural_and_content_hashtag_no_duplication(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("About #python.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))

        f2 = vault / "a2.md"
        f2.write_text("Also #python.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))

        db.update_tags(id1, ["python"])
        db.update_tags(id2, ["python"])
        db.index_backlinks(id1)
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id1)

        # "python" appears in both sources — should produce exactly one group
        assert len(groups) == 1
        assert groups[0].hashtag == "python"
        assert len(groups[0].entries) == 1
        assert groups[0].entries[0].id == id2
        db.close()

    # --- cross-mechanism union (2026-07-07 fix — was previously missed) ---

    def test_structural_only_finds_content_only_peer(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        id1 = _seed(db, n=1)  # structural tag only, no body text
        db.update_tags(id1, ["python"])

        f2 = vault / "a2.md"
        f2.write_text("Discussing #python today.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))  # content hashtag only
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert groups[0].hashtag == "python"
        assert groups[0].entries[0].id == id2
        db.close()

    def test_content_only_finds_structural_only_peer(self, tmp_path: Path) -> None:
        # Same setup as above, queried from the other side — the gap was
        # symmetric, so both directions must be verified independently.
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        id1 = _seed(db, n=1)
        db.update_tags(id1, ["python"])

        f2 = vault / "a2.md"
        f2.write_text("Discussing #python today.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))
        db.index_backlinks(id2)

        groups = db.get_hashtag_connections(id2)

        assert len(groups) == 1
        assert groups[0].hashtag == "python"
        assert groups[0].entries[0].id == id1
        db.close()

    def test_content_only_group_shows_canonical_structural_casing(
        self, tmp_path: Path
    ) -> None:
        # Focus entry only has the tag as a lowercase content hashtag, but a
        # *third* entry elsewhere in the vault has it as a structural tag —
        # the group's display name should resolve to that canonical casing,
        # matching get_content_hashtags_for_entries/get_graph's convention,
        # not the raw lowercase form.
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        db = VaultIndex(tmp_path / "vault" / "analecta.db")

        f1 = vault / "a1.md"
        f1.write_text("Discussing #python today.\n", encoding="utf-8")
        id1 = _seed(db, n=1, file_path=str(f1))
        db.index_backlinks(id1)

        f2 = vault / "a2.md"
        f2.write_text("Also #python here.\n", encoding="utf-8")
        id2 = _seed(db, n=2, file_path=str(f2))
        db.index_backlinks(id2)

        id3 = _seed(db, n=3)
        db.update_tags(id3, ["Python"])  # structural, elsewhere in the vault

        groups = db.get_hashtag_connections(id1)

        assert len(groups) == 1
        assert groups[0].hashtag == "Python"  # canonical casing, not "python"
        peer_ids = {e.id for e in groups[0].entries}
        assert peer_ids == {id2, id3}
        db.close()


# ---------------------------------------------------------------------------
# API endpoint — GET /entries/{id}/hashtag-connections
# ---------------------------------------------------------------------------


class TestHashtagConnectionsEndpoint:
    @pytest.fixture
    def client(self, tmp_path: Path) -> Generator[TestClient]:
        with TestClient(_make_app(tmp_path)) as c:
            yield c

    def test_404_unknown_entry(self, client: TestClient) -> None:
        resp = client.get("/api/v1/entries/9999/hashtag-connections")
        assert resp.status_code == 404

    def test_empty_when_no_shared_hashtags(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            id1 = _seed(index, n=1)
        with TestClient(app) as c:
            resp = c.get(f"/api/v1/entries/{id1}/hashtag-connections")
        assert resp.status_code == 200
        assert resp.json() == {"groups": []}

    def test_shared_hashtag_returned(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault" / "pages"
        vault.mkdir(parents=True)
        app = _make_app(tmp_path)
        config = AppConfig(vault_path=tmp_path / "vault")
        with VaultIndex(config.vault_path / "analecta.db") as index:
            f1 = vault / "a1.md"
            f1.write_text("About #python.\n", encoding="utf-8")
            id1 = _seed(index, n=1, file_path=str(f1))
            f2 = vault / "a2.md"
            f2.write_text("Also about #python.\n", encoding="utf-8")
            id2 = _seed(index, n=2, file_path=str(f2))
            index.index_backlinks(id1)
            index.index_backlinks(id2)
        with TestClient(app) as c:
            resp = c.get(f"/api/v1/entries/{id1}/hashtag-connections")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["groups"]) == 1
        group = data["groups"][0]
        assert group["hashtag"] == "python"
        assert len(group["entries"]) == 1
        assert group["entries"][0]["id"] == id2
