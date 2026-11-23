"""Allow `python -m jvs ...` invocation cleanly."""

from jvs.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
