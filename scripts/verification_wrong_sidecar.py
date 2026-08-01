#!/usr/bin/env python3
"""Emit an intentionally incompatible readiness frame for package verification."""

from __future__ import annotations

import json
import sys
import threading
from typing import NoReturn

API_CONTRACT = "ancestryllm.internal-api/1"
MAX_LAUNCH_FRAME_BYTES = 4096


def read_launch_frame() -> dict[str, str]:
    """Read the real private launch protocol without accepting alternate inputs."""

    raw = sys.stdin.buffer.read(MAX_LAUNCH_FRAME_BYTES + 1)
    if not raw or len(raw) > MAX_LAUNCH_FRAME_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid verification launch frame")
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != {
        "contract",
        "app_build",
        "bearer_token",
    }:
        raise ValueError("invalid verification launch frame")
    if (
        document["contract"] != API_CONTRACT
        or not isinstance(document["app_build"], str)
        or not document["app_build"]
        or not isinstance(document["bearer_token"], str)
        or not document["bearer_token"]
    ):
        raise ValueError("invalid verification launch frame")
    return document


def main() -> NoReturn:
    launch = read_launch_frame()
    readiness = {
        "contract": API_CONTRACT,
        "sidecar_build": f"{launch['app_build']}-verification-mismatch",
        "port": 1,
    }
    sys.stdout.write(json.dumps(readiness, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    threading.Event().wait()
    raise AssertionError("unreachable")


if __name__ == "__main__":
    main()
