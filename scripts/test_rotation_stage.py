"""Launch diagnostics for the rotation stage."""

from __future__ import annotations

import sys

from acq_staging.diagnostics.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["--component", "rotation", *sys.argv[1:]]))
