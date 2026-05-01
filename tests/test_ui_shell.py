import pytest
from PySide6.QtWidgets import QStackedWidget, QWidget

from analecta.config import AppConfig
from analecta.ui.fonts import FONT_FAMILY, font_path
from analecta.ui.main_window import MainWindow
from analecta.ui.theme import (
    ACCENT,
    BG,
    BG_DARK,
    BG_FLOAT,
    BG_HIGHLIGHT,
    BORDER,
    FG,
    FG_DIM,
    SELECTION,
    load_stylesheet,
)

# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------


def test_load_stylesheet_returns_string():
    ss = load_stylesheet()
    assert isinstance(ss, str)
    assert len(ss) > 0


def test_stylesheet_contains_bg():
    assert BG in load_stylesheet()


def test_stylesheet_contains_fg():
    assert FG in load_stylesheet()


def test_stylesheet_contains_accent():
    assert ACCENT in load_stylesheet()


def test_stylesheet_contains_selection():
    assert SELECTION in load_stylesheet()


def test_stylesheet_contains_border():
    assert BORDER in load_stylesheet()


def test_stylesheet_contains_font_family():
    assert FONT_FAMILY in load_stylesheet()


_ALL_COLORS = [
    BG, BG_DARK, BG_FLOAT, BG_HIGHLIGHT, FG, FG_DIM, ACCENT, BORDER, SELECTION
]


@pytest.mark.parametrize("color", _ALL_COLORS)
def test_palette_constants_are_hex(color):
    assert color.startswith("#")
    assert len(color) == 7


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------


def test_font_path_regular_is_ttf():
    path = font_path("regular")
    assert path.suffix == ".ttf"


def test_font_path_nerd_differs_from_regular():
    assert font_path("nerd") != font_path("regular")


def test_font_path_unknown_variant_falls_back_to_regular():
    assert font_path("unknown") == font_path("regular")


# ---------------------------------------------------------------------------
# MainWindow (requires offscreen display via conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return AppConfig()


def test_main_window_creates(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window is not None


def test_main_window_title(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window.windowTitle() == "Analecta"


def test_main_window_sidebar_is_qwidget(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert isinstance(window.sidebar, QWidget)


def test_main_window_content_is_stacked(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert isinstance(window.content, QStackedWidget)


def test_main_window_has_status_bar(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window.statusBar() is not None
    assert window.statusBar().currentMessage() == "Ready"


def test_main_window_default_size(qtbot, config):
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window.width() == 1280
    assert window.height() == 800
