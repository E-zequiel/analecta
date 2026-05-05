"""Tokyo Night (dark) QSS theme — M8 UI shell.

Palette sourced from tcmmichaelb139/obsidian-tokyonight theme.css,
.theme-dark selector.
"""

# ---------------------------------------------------------------------------
# Palette — Tokyo Night dark
# ---------------------------------------------------------------------------

# Backgrounds (darkest → lightest)
BG_DARK2 = "#121218"       # --bg_dark2
BG_DARK = "#16161e"        # --bg_dark
BG = "#1a1b26"             # --bg  (main window background)
BG_FLOAT = "#24283b"       # --bg_highlight_dark  (panels, inputs)
BG_HIGHLIGHT = "#292e42"   # --bg_highlight  (hover, secondary panels)

# Foreground
FG = "#c0caf5"             # --fg
FG_DARK = "#a9b1d6"        # --fg_dark
FG_DIM = "#565f89"         # --comment  (dim text, status bar)
BORDER = "#414868"         # --terminal_black

# Accent / semantic
ACCENT = "#7aa2f7"         # --blue     (primary accent)
SELECTION = "#3d59a1"      # --blue0    (selected items)
CYAN = "#7dcfff"           # --cyan
MAGENTA = "#bb9af7"        # --magenta
GREEN = "#9ece6a"          # --green
ORANGE = "#ff9e64"         # --orange
YELLOW = "#e0af68"         # --yellow
RED = "#ff757f"            # --red


def load_stylesheet() -> str:
    """Return the full Tokyo Night dark QSS stylesheet.

    All colours reference the module-level palette constants so the theme
    can be adjusted by changing those values without editing the template.

    Returns:
        Multi-line QSS string for ``QApplication.setStyleSheet()``.
    """
    return f"""
/* ── Base ────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG};
    color: {FG};
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
}}

/* ── Splitter ─────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
    height: 1px;
}}

/* ── Scrollbar ────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BG};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Buttons ──────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_FLOAT};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover {{
    background-color: {BG_HIGHLIGHT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {BG};
}}
QPushButton:disabled {{
    color: {FG_DIM};
    border-color: {BG_FLOAT};
}}

/* ── Line edit ────────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_FLOAT};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: {SELECTION};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}

/* ── List / tree ──────────────────────────────────────────────────────── */
QListView,
QTreeWidget,
QTreeView {{
    background-color: {BG_DARK};
    color: {FG};
    border: none;
    outline: none;
}}
QListView::item:selected,
QTreeWidget::item:selected,
QTreeView::item:selected {{
    background-color: {SELECTION};
    color: {FG};
}}
QListView::item:hover,
QTreeWidget::item:hover,
QTreeView::item:hover {{
    background-color: {BG_FLOAT};
}}

/* ── Status bar ───────────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {BG_DARK};
    color: {FG_DIM};
    font-size: 11px;
}}
QStatusBar::item {{
    border: none;
}}

/* ── Menu ─────────────────────────────────────────────────────────────── */
QMenuBar {{
    background-color: {BG_DARK};
    color: {FG};
}}
QMenuBar::item:selected {{
    background-color: {BG_FLOAT};
}}
QMenu {{
    background-color: {BG_FLOAT};
    color: {FG};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background-color: {SELECTION};
    color: {FG};
}}
QMenu::separator {{
    background-color: {BORDER};
    height: 1px;
}}

/* ── Tooltip ──────────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {BG_FLOAT};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 4px;
}}
"""
