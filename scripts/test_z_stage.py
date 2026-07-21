"""Launch diagnostics for the Z stage."""

from __future__ import annotations

import sys

from acq_staging.diagnostics.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["--component", "z", *sys.argv[1:]]))
