__version__ = "0.1.0"


def main() -> None:
    """Entry point for the ``analecta`` console script."""
    from analecta import server

    server.main()
