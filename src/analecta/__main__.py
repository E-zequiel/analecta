import argparse
import logging
import signal
from pathlib import Path


def run() -> None:
    """CLI entry point for `python -m analecta`."""
    parser = argparse.ArgumentParser(prog="analecta", description="PKM vault manager")
    parser.add_argument("--dev", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--vault", type=Path, metavar="PATH", help="Override vault path"
    )
    args = parser.parse_args()

    from analecta.config import CONFIG_PATH, load_config, save_config, setup_logging

    setup_logging(dev=args.dev)

    log = logging.getLogger(__name__)

    import asyncio
    import sys

    import qasync
    from PySide6.QtWidgets import QApplication

    from analecta.storage.index import VaultIndex
    from analecta.ui.dashboard import DashboardWidget
    from analecta.ui.editor import ArticleEditor
    from analecta.ui.fonts import load_font
    from analecta.ui.main_window import MainWindow
    from analecta.ui.settings import SettingsPanel
    from analecta.ui.theme import load_stylesheet
    from analecta.ui.tray import SystemTray
    from analecta.ui.viewer import ArticleViewer

    app = QApplication(sys.argv)
    app.setApplicationName("Analecta")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(load_stylesheet())

    if not CONFIG_PATH.exists() and not args.vault:
        from analecta.ui.first_run import FirstRunDialog

        dlg = FirstRunDialog()
        dlg.exec()
        config = dlg.result_config
        save_config(config)
    else:
        config = load_config()

    if args.vault:
        config = config.model_copy(update={"vault_path": args.vault})

    load_font(config.font_variant)
    log.info("analecta started (vault=%s, dev=%s)", config.vault_path, args.dev)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    index = VaultIndex(config.vault_path / "analecta.db")
    app.aboutToQuit.connect(index.close)

    window = MainWindow(config)
    dashboard = DashboardWidget(index, window)

    viewer = ArticleViewer(config, index)
    window.content.addWidget(viewer)

    def _show_viewer(entry) -> None:
        if entry is None:
            return
        viewer.load_entry(entry)
        window.content.setCurrentWidget(viewer)

    dashboard.page.entry_selected.connect(_show_viewer)
    viewer.back_requested.connect(
        lambda: window.content.setCurrentWidget(dashboard.page)
    )
    viewer.status_changed.connect(lambda _id, _status: dashboard.refresh())

    editor = ArticleEditor(config, index)
    window.content.addWidget(editor)

    def _show_editor(entry) -> None:
        if entry is None:
            return
        editor.load_entry(entry)
        window.content.setCurrentWidget(editor)

    viewer.entry_unlocked.connect(_show_editor)
    editor.close_requested.connect(
        lambda: window.content.setCurrentWidget(viewer)
    )
    editor.saved.connect(lambda _entry: dashboard.refresh())

    settings = SettingsPanel(config)
    window.content.addWidget(settings)
    settings.cancelled.connect(
        lambda: window.content.setCurrentWidget(dashboard.page)
    )
    settings.config_saved.connect(
        lambda new_cfg: window.content.setCurrentWidget(dashboard.page)
    )

    tray = SystemTray(config, app)
    tray.open_requested.connect(window.show)
    tray.open_requested.connect(window.raise_)
    tray.open_requested.connect(window.activateWindow)
    tray.quit_requested.connect(app.quit)
    app.aboutToQuit.connect(tray.hide)

    # SIGINT (Ctrl+C) and SIGHUP (terminal close) do not emit aboutToQuit —
    # route them through app.quit() so the tray is always hidden on exit.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGHUP, lambda *_: app.quit())

    async def _process_url(url: str) -> None:
        import sqlite3

        from analecta.extraction.assets import AssetDownloader
        from analecta.extraction.core import ExtractionError, extract
        from analecta.markdown.converter import MarkdownConverter
        from analecta.storage.index import EntryRecord
        from analecta.storage.vault import VaultManager

        vault = VaultManager(config.vault_path)
        vault.ensure_dirs()

        from datetime import datetime, timezone

        created_dt = datetime.now(tz=timezone.utc)
        created_at = created_dt.isoformat()

        try:
            content = await extract(url)
        except NotImplementedError as exc:
            tray.notify_error("Analecta", str(exc))
            return
        except ExtractionError as exc:
            tray.notify_error("Analecta", f"Extraction failed: {exc}")
            return

        page_path = vault.page_path(content.title, created_dt)
        slug = page_path.stem
        content.html = await AssetDownloader().process(
            content.html, slug, config.vault_path
        )

        markdown = MarkdownConverter().convert(content, created_at)
        file_path = vault.write_page(markdown, content.title, created_dt)

        entry = EntryRecord(
            title=content.title,
            url=url,
            file_path=str(file_path),
            source_type=content.source_type,
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            entry_id = index.add_entry(entry)
        except sqlite3.IntegrityError:
            tray.notify_error("Analecta", f"Already in vault: {content.title}")
            return

        index.update_fts_content(entry_id, content.title, markdown)
        dashboard.refresh()
        tray.notify_success("Analecta", f"Saved: {content.title}")
        log.info("saved entry %d: %s", entry_id, url)

    def _on_add_url(url: str) -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        asyncio.ensure_future(_process_url(url))

    tray.add_url_requested.connect(_on_add_url)

    window.show()

    from analecta.updater.checker import check_and_notify

    async def _check_updates() -> None:
        await check_and_notify(config, window)

    with loop:
        asyncio.ensure_future(_check_updates())
        loop.run_forever()


if __name__ == "__main__":
    run()
