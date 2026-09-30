#!/usr/bin/env python3
"""Exercise a packaged sidecar without relying on a system Python runtime."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import contextlib
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol
from uuid import uuid4

from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.sidecar import SIDECAR_BUILD
from ancestryllm.rootsmagic.source import RootsMagicReader

if TYPE_CHECKING:
    from collections.abc import Sequence

TIMEOUT_SECONDS = 10.0
_SCHEMA_DIAGNOSTIC = re.compile(
    r"(?:ROOTSMAGIC_SCHEMA_PARSE_FAILED: "
    r"(?:Exception|AttributeError|ImportError|ModuleNotFoundError|TypeError|ValueError|"
    r"KeyError|IndexError|RuntimeError|RecursionError|OSError)"
    r"(?: module=sqlglot(?:\.[a-z_]+)+)?|"
    r"ROOTSMAGIC_SCHEMA_VALIDATION_FAILED: "
    r"stage=(?:inspection|metadata|people_table|people_columns|family_records|event_records) "
    r"error_type=(?:AttributeError|TypeError|ValueError|none))$"
)


class _ReadableStream(Protocol):
    def readline(self) -> bytes: ...


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


def _get_json(
    port: int,
    path: str,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Ancestry-API-Version": API_CONTRACT,
            "X-Ancestry-App-Build": SIDECAR_BUILD,
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            _fail("packaged sidecar control probe failed")
        value = json.load(response)
    if not isinstance(value, dict):
        _fail("packaged sidecar returned invalid JSON")
    return value


def _readline_with_timeout(
    stream: _ReadableStream,
    *,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> bytes:
    """Read one line without waiting for a blocked reader during shutdown."""

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(stream.readline).result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _build_launch_frame(
    token: str,
    diagnostic_run_id: str,
    diagnostic_directory: str,
    intake_directory: str | None = None,
) -> str:
    """Build the exact private frame required by the packaged sidecar."""

    frame = {
        "contract": API_CONTRACT,
        "app_build": SIDECAR_BUILD,
        "bearer_token": token,
        "diagnostic_run_id": diagnostic_run_id,
        "diagnostic_directory": diagnostic_directory,
    }
    if intake_directory is not None:
        frame["gedcom_intake_directory"] = intake_directory
    return json.dumps(
        frame,
        separators=(",", ":"),
    )


def _job_result(port: int, token: str, job: dict[str, object]) -> dict[str, object]:
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        _fail("packaged RootsMagic probe did not return a job")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = _get_json(port, f"/api/v1/jobs/{job_id}", token)
        state = snapshot.get("state")
        if state == "completed":
            response = _get_json(port, f"/api/v1/rootsmagic/jobs/{job_id}/result", token)
            result = response.get("result")
            if not isinstance(result, dict):
                _fail("packaged RootsMagic probe returned an invalid result")
            return result
        if state in {"failed", "cancelled"}:
            code = snapshot.get("error_code")
            if (
                isinstance(code, str)
                and code.startswith("ROOTSMAGIC_")
                and len(code) <= 80
                and all(character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for character in code)
            ):
                _fail(f"packaged RootsMagic probe failed: {code}")
            _fail("packaged RootsMagic probe failed")
        time.sleep(0.05)
    _fail("packaged RootsMagic probe timed out")


def _probe_rootsmagic(port: int, token: str, root: Path, intake: Path) -> None:
    source = root / "fictional.rmtree"
    with contextlib.closing(sqlite3.connect(source)) as connection, connection:
        connection.executescript(
            "CREATE TABLE PersonTable (PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);"
            "CREATE TABLE NameTable (NameID INTEGER PRIMARY KEY, OwnerID INTEGER, "
            "Given TEXT, Surname TEXT, IsPrimary INTEGER);"
            "INSERT INTO PersonTable VALUES (1, 0, 0);"
            "INSERT INTO NameTable VALUES (1, 1, 'Fictional', 'Example', 1);"
        )
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    info = source.stat()
    identity = (info.st_dev, info.st_ino)
    if os.name == "nt":
        handle = RootsMagicReader._windows_open_directory_handle(source)
        try:
            identity = RootsMagicReader._windows_handle_identity(handle)
        finally:
            RootsMagicReader._windows_close_handle(handle)
    capability = os.urandom(32).hex()
    manifest = intake / f"{capability}.rootsmagic-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "path": str(source),
                "dev": str(identity[0]),
                "ino": str(identity[1]),
                "size_bytes": info.st_size,
                "sha256": fingerprint,
                "friendly_name": source.name,
                "wal": None,
                "shm": None,
            }
        ),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    inspection = _job_result(
        port,
        token,
        _get_json(
            port,
            "/api/v1/rootsmagic/sources",
            token,
            {"schema_version": 1, "source_capability": capability},
        ),
    )
    source_ref = inspection.get("source_ref")
    if not isinstance(source_ref, str) or len(source_ref) != 64:
        _fail("packaged RootsMagic probe did not return a source")
    try:
        query = _job_result(
            port,
            token,
            _get_json(
                port,
                "/api/v1/rootsmagic/queries",
                token,
                {
                    "schema_version": 1,
                    "source_ref": source_ref,
                    "query_id": "people",
                    "offset": 0,
                    "page_size": 25,
                },
            ),
        )
        if query.get("returned_rows") != 1 or query.get("next_offset") is not None:
            _fail("packaged RootsMagic query returned unexpected fictional data")
    finally:
        disposal = _get_json(
            port,
            f"/api/v1/rootsmagic/sources/{source_ref}/discard",
            token,
            {"schema_version": 1},
        )
        if disposal != {"schema_version": 1}:
            _fail("packaged RootsMagic probe did not dispose its source")
    if hashlib.sha256(source.read_bytes()).hexdigest() != fingerprint:
        _fail("packaged RootsMagic probe changed its source")


def smoke(executable: Path) -> None:
    """Launch, authenticate, inspect, and terminate one native sidecar."""

    token = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    with (
        tempfile.TemporaryDirectory(prefix="ancestryllm-sidecar-smoke-") as working_directory,
        tempfile.TemporaryFile() as stderr,
    ):
        root = Path(working_directory).resolve()
        diagnostic_directory = str(root / "diagnostics")
        intake = root / "intake"
        intake.mkdir(mode=0o700)
        launch_frame = _build_launch_frame(
            token,
            str(uuid4()),
            diagnostic_directory,
            str(intake),
        )
        environment = _minimal_environment()
        environment["ANCESTRYLLM_NATIVE_VERIFICATION_EPHEMERAL_WORKSPACE"] = "1"
        process = subprocess.Popen(  # noqa: S603 - explicit artifact under test, no shell
            [str(executable.resolve())],
            cwd=working_directory,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        try:
            if process.stdin is None or process.stdout is None:
                _fail("packaged sidecar pipes were not created")
            process.stdin.write((launch_frame + "\n").encode())
            process.stdin.close()
            try:
                line = _readline_with_timeout(process.stdout)
            except TimeoutError:
                _fail("packaged sidecar readiness timed out")
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
            _probe_rootsmagic(port, token, root, intake)
        except RuntimeError as exc:
            stderr.seek(0)
            # Only forward the structural marker, never raw subprocess output.
            diagnostics = {
                match.group(0)
                for line in stderr.read(65_536).decode("utf-8", errors="replace").splitlines()
                if (match := _SCHEMA_DIAGNOSTIC.search(line))
            }
            if diagnostics:
                raise RuntimeError(f"{exc}; {'; '.join(sorted(diagnostics)[:4])}") from exc
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the smoke sidecar workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the smoke sidecar command and return its exit status."""
    arguments = parse_args(argv)
    if not arguments.executable.is_file():
        raise FileNotFoundError(arguments.executable)
    smoke(arguments.executable)
    print("packaged sidecar smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
