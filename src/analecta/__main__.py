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

    # M8: launch PySide6 QApplication here


if __name__ == "__main__":
    run()
