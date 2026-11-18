if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        from analecta.cli import run

        raise SystemExit(run(sys.argv[1:]))
    from analecta.server import main

    main()
