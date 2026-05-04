import argparse
import logging
from pathlib import Path


def run() -> None:
    """CLI entry point for `python -m analecta`."""
    parser = argparse.ArgumentParser(prog="analecta", description="PKM vault manager")
    parser.add_argument("--dev", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--vault", type=Path, metavar="PATH", help="Override vault path"
    )
    args = parser.parse_args()

    from analecta.config import load_config, setup_logging

    setup_logging(dev=args.dev)
    config = load_config()
    if args.vault:
        config = config.model_copy(update={"vault_path": args.vault})

    log = logging.getLogger(__name__)
    log.info("analecta started (vault=%s, dev=%s)", config.vault_path, args.dev)

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
    load_font(config.font_variant)

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

    def _on_add_url(url: str) -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        log.info("add URL from tray: %s", url)

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
