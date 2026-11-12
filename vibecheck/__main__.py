"""Module entry point for `python -m vibecheck`."""

import sys

from vibecheck.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
