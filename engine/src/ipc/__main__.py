"""Module entrypoint for python -m ipc."""

from __future__ import annotations

import sys

from ipc.stdio import main

if __name__ == "__main__":
    sys.exit(main())
