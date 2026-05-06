"""Tests for M9 dashboard panel."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt

from analecta.config import AppConfig
from analecta.storage.index import EntryRecord, VaultIndex
from analecta.ui.dashboard import (
    _ENTRY_ROLE,
    DashboardPage,
    DashboardSidebar,
    DashboardWidget,
    EntryListModel,
    PreviewCard,
)
from analecta.ui.main_window import MainWindow


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _entry(**kwargs) -> EntryRecord:
    defaults = dict(
        title="Test Entry",
        url="https://example.com/test",
        file_path="/vault/test.md",
        source_type="article",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return EntryRecord(**defaults)


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture
def index(tmp_path: Path):
    db = VaultIndex(tmp_path / "vault.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# VaultIndex.list_tags
# ---------------------------------------------------------------------------


def test_list_tags_empty(index):
    assert index.list_tags() == []


def test_list_tags_returns_name_count_pairs(index):
    eid = index.add_entry(_entry())
    index.update_tags(eid, ["python", "ai"])
    tags = dict(index.list_tags())
    assert tags["python"] == 1
    assert tags["ai"] == 1


def test_list_tags_sorted_by_count_desc(index):
    id1 = index.add_entry(_entry(url="https://a.com/1"))
    id2 = index.add_entry(_entry(url="https://a.com/2"))
    index.update_tags(id1, ["python", "ai"])
    index.update_tags(id2, ["python"])
    names = [name for name, _ in index.list_tags()]
    assert names[0] == "python"  # count=2


# ---------------------------------------------------------------------------
# EntryListModel
# ---------------------------------------------------------------------------


def test_model_initial_row_count(qtbot):
    model = EntryListModel()
    assert model.rowCount() == 0


def test_model_refresh_updates_row_count(qtbot):
    model = EntryListModel()
    model.refresh([_entry(), _entry(url="https://b.com")])
    assert model.rowCount() == 2


def test_model_data_display_role(qtbot):
    model = EntryListModel()
    model.refresh([_entry(title="Hello")])
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "Hello"


def test_model_data_entry_role(qtbot):
    model = EntryListModel()
    model.refresh([_entry(title="Hello")])
    assert model.data(model.index(0, 0), _ENTRY_ROLE).title == "Hello"


def test_model_data_invalid_index_returns_none(qtbot):
    model = EntryListModel()
    assert model.data(QModelIndex()) is None


def test_model_entry_at_valid(qtbot):
    model = EntryListModel()
    model.refresh([_entry(title="First")])
    assert model.entry_at(0).title == "First"


def test_model_entry_at_out_of_range(qtbot):
    model = EntryListModel()
    assert model.entry_at(0) is None


# ---------------------------------------------------------------------------
# DashboardSidebar
# ---------------------------------------------------------------------------


def test_sidebar_creates(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    assert sidebar is not None


def test_sidebar_default_filter_is_all(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    assert sidebar.current_filter == "all"


def test_sidebar_filter_signal_unread(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    signals = []
    sidebar.filter_changed.connect(signals.append)
    sidebar._filter_buttons["unread"].click()
    assert signals == ["unread"]


def test_sidebar_filter_signal_sequence(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    signals = []
    sidebar.filter_changed.connect(signals.append)
    sidebar._filter_buttons["favorite"].click()
    sidebar._filter_buttons["all"].click()
    assert signals == ["favorite", "all"]


def test_sidebar_refresh_tags_populates_tree(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    sidebar.refresh_tags([("python", 3), ("ai", 1)])
    assert sidebar._tag_tree.topLevelItemCount() == 2


def test_sidebar_refresh_tags_clears_previous(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    sidebar.refresh_tags([("python", 3)])
    sidebar.refresh_tags([("rust", 1)])
    assert sidebar._tag_tree.topLevelItemCount() == 1
    assert sidebar._tag_tree.topLevelItem(0).text(0).startswith("rust")


def test_sidebar_tag_selected_signal(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    sidebar.refresh_tags([("python", 3)])
    signals = []
    sidebar.tag_selected.connect(signals.append)
    item = sidebar._tag_tree.topLevelItem(0)
    sidebar._tag_tree.itemClicked.emit(item, 0)
    assert signals == ["python"]


def test_sidebar_search_signal(qtbot):
    sidebar = DashboardSidebar()
    qtbot.addWidget(sidebar)
    sidebar._search_timer.setInterval(50)
    signals = []
    sidebar.search_changed.connect(signals.append)
    sidebar._search.setText("python")
    qtbot.waitUntil(lambda: len(signals) > 0, timeout=500)
    assert signals[-1] == "python"


# ---------------------------------------------------------------------------
# PreviewCard
# ---------------------------------------------------------------------------


def test_preview_card_creates(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    assert card is not None


def test_preview_card_placeholder_on_init(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    assert card._title.text() == "—"


def test_preview_card_update_entry(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    card.update_entry(_entry(title="My Article"))
    assert card._title.text() == "My Article"


def test_preview_card_clear(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    card.update_entry(_entry())
    card.update_entry(None)
    assert card._title.text() == "—"
    assert card._meta.text() == ""


def test_preview_card_shows_tags(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    card.update_entry(_entry(tags_json=json.dumps(["python", "ai"])))
    assert "#python" in card._tags.text()
    assert "#ai" in card._tags.text()


def test_preview_card_shows_meta(qtbot):
    card = PreviewCard()
    qtbot.addWidget(card)
    card.update_entry(_entry(source_type="youtube", status="read"))
    assert "youtube" in card._meta.text()
    assert "read" in card._meta.text()


# ---------------------------------------------------------------------------
# DashboardPage
# ---------------------------------------------------------------------------


def test_page_creates(qtbot):
    page = DashboardPage()
    qtbot.addWidget(page)
    assert page is not None


def test_page_has_entry_list_model(qtbot):
    page = DashboardPage()
    qtbot.addWidget(page)
    assert isinstance(page.model, EntryListModel)


def test_page_list_view_uses_model(qtbot):
    page = DashboardPage()
    qtbot.addWidget(page)
    assert page.list_view.model() is page.model


def test_page_selection_updates_preview(qtbot):
    page = DashboardPage()
    qtbot.addWidget(page)
    page.model.refresh([_entry(title="Selected Article")])
    page.list_view.setCurrentIndex(page.model.index(0, 0))
    assert page._preview._title.text() == "Selected Article"


def test_page_entry_selected_signal(qtbot):
    page = DashboardPage()
    qtbot.addWidget(page)
    page.model.refresh([_entry(title="Signal Test")])
    signals = []
    page.entry_selected.connect(signals.append)
    page.list_view.setCurrentIndex(page.model.index(0, 0))
    assert len(signals) == 1
    assert signals[0].title == "Signal Test"


# ---------------------------------------------------------------------------
# DashboardWidget (integration)
# ---------------------------------------------------------------------------


def test_dashboard_widget_injects_page(qtbot, config, index):
    window = MainWindow(config)
    qtbot.addWidget(window)
    DashboardWidget(index, window)
    assert window.content.count() == 1


def test_dashboard_widget_injects_sidebar(qtbot, config, index):
    window = MainWindow(config)
    qtbot.addWidget(window)
    DashboardWidget(index, window)
    assert window.sidebar.layout() is not None


def test_dashboard_widget_loads_entries_on_init(qtbot, config, index):
    index.add_entry(_entry())
    window = MainWindow(config)
    qtbot.addWidget(window)
    d = DashboardWidget(index, window)
    assert d.page.model.rowCount() == 1


def test_dashboard_widget_filter_unread(qtbot, config, index):
    index.add_entry(_entry(url="https://a.com/1"))
    id2 = index.add_entry(_entry(url="https://a.com/2"))
    index.update_status(id2, "read")
    window = MainWindow(config)
    qtbot.addWidget(window)
    d = DashboardWidget(index, window)
    d.sidebar.filter_changed.emit("unread")
    assert d.page.model.rowCount() == 1


def test_dashboard_widget_tag_filter(qtbot, config, index):
    id1 = index.add_entry(_entry(url="https://a.com/1"))
    index.add_entry(_entry(url="https://a.com/2"))
    index.update_tags(id1, ["python"])
    window = MainWindow(config)
    qtbot.addWidget(window)
    d = DashboardWidget(index, window)
    d.sidebar.tag_selected.emit("python")
    assert d.page.model.rowCount() == 1


def test_dashboard_widget_tag_toggle(qtbot, config, index):
    id1 = index.add_entry(_entry(url="https://a.com/1"))
    index.add_entry(_entry(url="https://a.com/2"))
    index.update_tags(id1, ["python"])
    window = MainWindow(config)
    qtbot.addWidget(window)
    d = DashboardWidget(index, window)
    d.sidebar.tag_selected.emit("python")
    assert d.page.model.rowCount() == 1
    d.sidebar.tag_selected.emit("python")  # toggle off
    assert d.page.model.rowCount() == 2


def test_dashboard_widget_refreshes_tag_tree(qtbot, config, index):
    eid = index.add_entry(_entry())
    index.update_tags(eid, ["rust"])
    window = MainWindow(config)
    qtbot.addWidget(window)
    d = DashboardWidget(index, window)
    assert d.sidebar._tag_tree.topLevelItemCount() == 1
