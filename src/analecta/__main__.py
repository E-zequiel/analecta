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

    from analecta.ui.fonts import load_font
    from analecta.ui.main_window import MainWindow
    from analecta.ui.theme import load_stylesheet

    app = QApplication(sys.argv)
    app.setApplicationName("Analecta")
    app.setStyleSheet(load_stylesheet())
    load_font(config.font_variant)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow(config)
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    run()
