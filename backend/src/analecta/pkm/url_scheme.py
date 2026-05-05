"""analecta:// URL scheme handler and registration — M6 PKM layer."""

import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Analecta
Exec={exec} %u
MimeType=x-scheme-handler/analecta;
NoDisplay=true
StartupNotify=false
"""


def make_url(entry_id: int) -> str:
    """Generate an ``analecta://`` URL for *entry_id*.

    Args:
        entry_id: Database row id of the target entry.

    Returns:
        URL string of the form ``analecta://open?id={entry_id}``.
    """
    return f"analecta://open?id={entry_id}"


def parse_url(url: str) -> int | None:
    """Parse and validate an ``analecta://`` URL, returning the entry id.

    The ``id`` parameter is treated as untrusted input: it must be a positive
    integer. Any other value returns ``None``.

    Args:
        url: Candidate URL string.

    Returns:
        Positive integer entry id, or ``None`` if the URL is invalid.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme != "analecta" or parsed.netloc != "open":
        return None

    id_values = parse_qs(parsed.query).get("id", [])
    if not id_values:
        return None

    try:
        entry_id = int(id_values[0])
    except ValueError:
        return None

    return entry_id if entry_id > 0 else None


def is_scheme_registered(desktop_dir: Path | None = None) -> bool:
    """Return True if the analecta:// handler desktop file already exists.

    Args:
        desktop_dir: Directory to check. Defaults to
            ``~/.local/share/applications/``.

    Returns:
        ``True`` when ``analecta.desktop`` is present in *desktop_dir*.
    """
    if desktop_dir is None:
        desktop_dir = Path.home() / ".local" / "share" / "applications"
    return (desktop_dir / "analecta.desktop").exists()


def register_scheme(app_exec: str, desktop_dir: Path | None = None) -> None:
    """Register the ``analecta://`` URL scheme for the current user.

    Writes a ``.desktop`` file and calls ``xdg-mime`` to associate it with
    the ``x-scheme-handler/analecta`` MIME type.

    Args:
        app_exec: Absolute path to the ``analecta`` executable.
        desktop_dir: Directory to write the ``.desktop`` file. Defaults to
            ``~/.local/share/applications/``.

    Raises:
        subprocess.CalledProcessError: If ``xdg-mime`` exits with a non-zero
            status.
        FileNotFoundError: If ``xdg-mime`` is not installed.
    """
    if desktop_dir is None:
        desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = desktop_dir / "analecta.desktop"
    desktop_file.write_text(
        _DESKTOP_ENTRY.format(exec=app_exec), encoding="utf-8"
    )

    subprocess.run(
        [
            "xdg-mime",
            "default",
            "analecta.desktop",
            "x-scheme-handler/analecta",
        ],
        check=True,
    )
