"""Signal-aware dormant worker reserved for explicitly profiled background work."""

from __future__ import annotations

import json
import signal
import sys
import threading

from ancestryllm import __version__
from ancestryllm.container_runtime import (
    WORKER_READY_PATH,
    ContainerRuntimeError,
    publish_private_runtime_file,
    remove_private_runtime_file,
)


def run() -> None:
    """Remain idle until stopped; no application work is enabled by issue #349."""

    stopped = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    marker = json.dumps(
        {"build": __version__, "schema_version": 1, "status": "ready"},
        separators=(",", ":"),
        sort_keys=True,
    )
    publish_private_runtime_file(WORKER_READY_PATH, marker)
    try:
        stopped.wait()
    finally:
        remove_private_runtime_file(WORKER_READY_PATH)


def main() -> int:
    try:
        run()
    except ContainerRuntimeError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by container lifecycle tests
    raise SystemExit(main())


__all__ = ["main", "run"]
