#!/usr/bin/env python3
"""Native entry point for private desktop control and isolated inspection."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the exact private worker flag before starting desktop control."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--inspection-worker" in arguments:
        if arguments != ["--inspection-worker"]:
            return 64
        from ancestryllm.gedcom.inspection_worker import main as worker_main

        return worker_main(["--child"])

    from ancestryllm.api.sidecar import main as sidecar_main

    return sidecar_main()


if __name__ == "__main__":
    raise SystemExit(main())
