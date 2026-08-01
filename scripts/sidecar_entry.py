#!/usr/bin/env python3
"""Native executable entry point for the private desktop sidecar."""

from ancestryllm.api.sidecar import main

if __name__ == "__main__":
    raise SystemExit(main())
