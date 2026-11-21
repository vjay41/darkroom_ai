"""`python -m darkroom_ai ...` runs the same command line as the `darkroom` script."""

from __future__ import annotations

import sys

from darkroom_ai.cli import main

if __name__ == "__main__":
    sys.exit(main())
