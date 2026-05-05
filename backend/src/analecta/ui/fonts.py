"""JetBrains Mono font loader — M8 UI shell."""

import importlib.resources
from pathlib import Path

# All 4 styles are required so Qt doesn't synthesize bold/italic artificially.
_FONT_FILES: dict[str, list[str]] = {
    "regular": [
        "JetBrainsMono-Regular.ttf",
        "JetBrainsMono-Bold.ttf",
        "JetBrainsMono-Italic.ttf",
        "JetBrainsMono-BoldItalic.ttf",
    ],
    "nerd": [
        "JetBrainsMonoNerdFont-Regular.ttf",
        "JetBrainsMonoNerdFont-Bold.ttf",
        "JetBrainsMonoNerdFont-Italic.ttf",
        "JetBrainsMonoNerdFont-BoldItalic.ttf",
    ],
}

FONT_FAMILY = "JetBrains Mono"


def _fonts_dir() -> Path:
    return Path(str(importlib.resources.files("analecta.ui") / "fonts"))


def font_path(variant: str = "regular") -> Path:
    """Return the path to the Regular weight for *variant*.

    Useful for display or testing; use :func:`load_font` to load the full
    family (all four styles) into Qt's font database.

    Args:
        variant: ``"regular"`` or ``"nerd"``. Unknown values fall back to
            ``"regular"``.

    Returns:
        Absolute ``Path`` to the Regular ``.ttf`` file for the variant.
    """
    files = _FONT_FILES.get(variant, _FONT_FILES["regular"])
    return _fonts_dir() / files[0]


def load_font(variant: str = "regular") -> int:
    """Load all four JetBrains Mono styles for *variant* into Qt's font DB.

    Must be called after ``QApplication`` has been created. Missing files
    are silently skipped — Qt falls back to synthetic bold/italic for any
    style that could not be loaded.

    Args:
        variant: ``"regular"`` or ``"nerd"``.

    Returns:
        The font id of the last successfully loaded file (≥ 0), or ``-1``
        if no file could be loaded.
    """
    from PySide6.QtGui import QFontDatabase

    files = _FONT_FILES.get(variant, _FONT_FILES["regular"])
    fonts_dir = _fonts_dir()
    last_id = -1
    for filename in files:
        path = fonts_dir / filename
        if path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                last_id = font_id
    return last_id
