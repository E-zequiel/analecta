"""Dashboard panel — M9 UI."""

import json

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from analecta.storage.index import EntryRecord, VaultIndex

_ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1

_STATUS_LABELS: dict[str, str] = {
    "all": "All",
    "unread": "Unread",
    "read": "Read",
    "favorite": "Favorite",
    "deleted": "Deleted",
    "to_recommend": "Recommend",
}


class EntryListModel(QAbstractListModel):
    """Read-only list model backed by a list of EntryRecords.

    Args:
        parent: Parent QObject.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[EntryRecord] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of rows under *parent*."""
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """Return data for *index* and *role*."""
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.title
        if role == _ENTRY_ROLE:
            return entry
        return None

    def refresh(self, entries: list[EntryRecord]) -> None:
        """Replace all entries and notify attached views.

        Args:
            entries: New entry list.
        """
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> EntryRecord | None:
        """Return the entry at *row*, or None if out of range.

        Args:
            row: Zero-based row index.

        Returns:
            EntryRecord or None.
        """
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class DashboardSidebar(QWidget):
    """Left sidebar: search bar, status filters, and tag tree.

    Signals:
        filter_changed: Status key emitted when a filter button is clicked.
        search_changed: FTS query string emitted after a 300 ms debounce.
        tag_selected: Tag name emitted when a tag tree item is clicked.

    Args:
        parent: Parent QWidget.
    """

    filter_changed = Signal(str)
    search_changed = Signal(str)
    tag_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        layout.addWidget(self._search)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(
            lambda: self.search_changed.emit(self._search.text())
        )
        self._search.textChanged.connect(lambda _: self._search_timer.start())

        filter_frame = QFrame()
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(2)

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_buttons: dict[str, QPushButton] = {}

        for key, label in _STATUS_LABELS.items():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._filter_group.addButton(btn)
            self._filter_buttons[key] = btn
            btn.clicked.connect(lambda checked, k=key: self.filter_changed.emit(k))
            filter_layout.addWidget(btn)

        self._filter_buttons["all"].setChecked(True)
        layout.addWidget(filter_frame)

        tag_label = QLabel("Tags")
        tag_label.setObjectName("dimLabel")
        layout.addWidget(tag_label)

        self._tag_tree = QTreeWidget()
        self._tag_tree.setHeaderHidden(True)
        self._tag_tree.setRootIsDecorated(False)
        self._tag_tree.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._tag_tree)

    def _on_tag_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        tag = item.data(0, Qt.ItemDataRole.UserRole)
        if tag:
            self.tag_selected.emit(tag)

    def refresh_tags(self, tags: list[tuple[str, int]]) -> None:
        """Repopulate the tag tree.

        Args:
            tags: List of ``(name, count)`` tuples.
        """
        self._tag_tree.clear()
        for name, count in tags:
            item = QTreeWidgetItem([f"{name}  ({count})"])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self._tag_tree.addTopLevelItem(item)

    @property
    def current_filter(self) -> str:
        """Currently selected status filter key."""
        for key, btn in self._filter_buttons.items():
            if btn.isChecked():
                return key
        return "all"


class PreviewCard(QFrame):
    """Entry detail panel shown below the entry list on selection.

    Args:
        parent: Parent QWidget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(130)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        self._title = QLabel("—")
        self._title.setStyleSheet("font-weight: bold;")
        self._title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._meta = QLabel("")
        self._meta.setObjectName("dimLabel")

        self._tags = QLabel("")
        self._tags.setWordWrap(True)

        layout.addWidget(self._title)
        layout.addWidget(self._meta)
        layout.addWidget(self._tags)
        layout.addStretch()

    def update_entry(self, entry: EntryRecord | None) -> None:
        """Populate the card, or clear it if *entry* is None.

        Args:
            entry: Entry to display, or ``None`` to show the placeholder.
        """
        if entry is None:
            self._title.setText("—")
            self._meta.setText("")
            self._tags.setText("")
            return
        self._title.setText(entry.title)
        date = entry.created_at[:10]
        self._meta.setText(f"{date}  ·  {entry.source_type}  ·  {entry.status}")
        tags = json.loads(entry.tags_json)
        self._tags.setText("  ".join(f"#{t}" for t in tags) if tags else "")


class DashboardPage(QWidget):
    """Content area: scrollable entry list with a preview card below.

    Signals:
        entry_selected: Emits the selected ``EntryRecord`` or ``None`` on deselect.

    Args:
        parent: Parent QWidget.
    """

    entry_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = EntryListModel(self)
        self._build_ui()

    @property
    def model(self) -> EntryListModel:
        """The list model driving the QListView."""
        return self._model

    @property
    def list_view(self) -> QListView:
        """The entry QListView."""
        return self._list_view

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list_view = QListView()
        self._list_view.setModel(self._model)
        self._list_view.setUniformItemSizes(True)

        self._preview = PreviewCard(self)

        layout.addWidget(self._list_view, stretch=1)
        layout.addWidget(self._preview, stretch=0)

        self._list_view.selectionModel().currentChanged.connect(
            self._on_selection_changed
        )

    def _on_selection_changed(
        self, current: QModelIndex, previous: QModelIndex
    ) -> None:
        if not current.isValid():
            self._preview.update_entry(None)
            self.entry_selected.emit(None)
            return
        entry = self._model.data(current, _ENTRY_ROLE)
        self._preview.update_entry(entry)
        self.entry_selected.emit(entry)


class DashboardWidget:
    """Orchestrates DashboardSidebar and DashboardPage inside MainWindow.

    Queries VaultIndex on every filter/search/tag change. All reads happen on
    the main thread — SQLite local reads are fast enough for a PKM vault.

    Args:
        index: Open VaultIndex instance.
        window: The application MainWindow.
    """

    def __init__(self, index: VaultIndex, window) -> None:
        self._index = index
        self._filter = "all"
        self._query = ""
        self._tag: str | None = None

        self._sidebar = DashboardSidebar()
        self._page = DashboardPage()

        sidebar_layout = QVBoxLayout(window.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        sidebar_layout.addWidget(self._sidebar)

        window.content.addWidget(self._page)
        window.content.setCurrentWidget(self._page)

        self._sidebar.filter_changed.connect(self._on_filter_changed)
        self._sidebar.search_changed.connect(self._on_search_changed)
        self._sidebar.tag_selected.connect(self._on_tag_selected)
        self._page.entry_selected.connect(
            lambda e: window.statusBar().showMessage(e.title if e else "Ready")
        )

        self._refresh()

    def _on_filter_changed(self, status: str) -> None:
        self._filter = status
        self._tag = None
        self._refresh()

    def _on_search_changed(self, query: str) -> None:
        self._query = query.strip()
        self._refresh()

    def _on_tag_selected(self, tag: str) -> None:
        self._tag = tag if self._tag != tag else None
        self._refresh()

    def _refresh(self) -> None:
        if self._query:
            try:
                entries = self._index.search(self._query)
            except Exception:
                entries = []
            if self._tag:
                entries = [e for e in entries if self._tag in json.loads(e.tags_json)]
        elif self._tag:
            status = None if self._filter == "all" else self._filter
            all_entries = self._index.list_entries(status)
            entries = [e for e in all_entries if self._tag in json.loads(e.tags_json)]
        else:
            status = None if self._filter == "all" else self._filter
            entries = self._index.list_entries(status)

        self._page.model.refresh(entries)
        self._sidebar.refresh_tags(self._index.list_tags())

    def refresh(self) -> None:
        """Re-query the index and update the entry list and tag tree."""
        self._refresh()

    @property
    def page(self) -> DashboardPage:
        """The dashboard content page."""
        return self._page

    @property
    def sidebar(self) -> DashboardSidebar:
        """The dashboard sidebar widget."""
        return self._sidebar
