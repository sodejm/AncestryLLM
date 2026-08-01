#!/usr/bin/env python3
"""Exercise a packaged sidecar without relying on a system Python runtime."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import NoReturn, Sequence

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import SIDECAR_BUILD

TIMEOUT_SECONDS = 10.0


def _fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def _minimal_environment() -> dict[str, str]:
    allowed = (
        ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if os.name == "nt"
        else (
            "LANG",
            "LC_ALL",
            "TMPDIR",
        )
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _get_json(port: int, path: str, token: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Ancestry-API-Version": API_CONTRACT,
            "X-Ancestry-App-Build": SIDECAR_BUILD,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            _fail("packaged sidecar control probe failed")
        value = json.load(response)
    if not isinstance(value, dict):
        _fail("packaged sidecar returned invalid JSON")
    return value


def smoke(executable: Path) -> None:
    """Launch, authenticate, inspect, and terminate one native sidecar."""

    token = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    launch_frame = json.dumps(
        {
            "contract": API_CONTRACT,
            "app_build": SIDECAR_BUILD,
            "bearer_token": token,
        },
        separators=(",", ":"),
    )
    with tempfile.TemporaryDirectory(prefix="ancestryllm-sidecar-smoke-") as working_directory:
        process = subprocess.Popen(  # noqa: S603 - explicit artifact under test, no shell
            [str(executable.resolve())],
            cwd=working_directory,
            env=_minimal_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if process.stdin is None or process.stdout is None:
                _fail("packaged sidecar pipes were not created")
            process.stdin.write((launch_frame + "\n").encode())
            process.stdin.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                line = executor.submit(process.stdout.readline).result(timeout=TIMEOUT_SECONDS)
            if len(line) > 1024:
                _fail("packaged sidecar readiness frame exceeded its limit")
            ready = json.loads(line)
            if not isinstance(ready, dict) or set(ready) != {
                "contract",
                "sidecar_build",
                "port",
            }:
                _fail("packaged sidecar readiness frame was invalid")
            if ready["contract"] != API_CONTRACT or ready["sidecar_build"] != SIDECAR_BUILD:
                _fail("packaged sidecar build or contract mismatch")
            port = ready["port"]
            if not isinstance(port, int) or not 0 < port < 65536:
                _fail("packaged sidecar returned an invalid port")

            health = _get_json(port, "/api/v1/health", token)
            expected_proof = hmac.new(
                token.encode(),
                f"{API_CONTRACT}\n{SIDECAR_BUILD}\n{SIDECAR_BUILD}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if health.get("status") != "ready" or not hmac.compare_digest(
                str(health.get("readiness_proof", "")), expected_proof
            ):
                _fail("packaged sidecar readiness proof was invalid")

            capabilities = _get_json(port, "/api/v1/capabilities", token)
            if capabilities.get("modules") != []:
                _fail("packaged control sidecar unexpectedly exposed domain capabilities")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    if not arguments.executable.is_file():
        raise FileNotFoundError(arguments.executable)
    smoke(arguments.executable)
    print("packaged sidecar smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
