#!/usr/bin/env python3
"""Install uv only after verifying its archive, provenance, and binary digest."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import hmac
import http.client
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Protocol

POLICY_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
UV_VERSION = "0.12.1"
GH_VERSION = "2.97.0"
RELEASE_URL_TEMPLATE = "https://github.com/{repository}/releases/download/{tag}/{asset}"
UV_REPOSITORY = "astral-sh/uv"
UV_SOURCE_REPOSITORY = "https://github.com/astral-sh/uv"
UV_SOURCE_COMMIT = "329541a503de8a4d9bb021814f9c0875efe033c8"
UV_SOURCE_REF = "refs/heads/main"
UV_SIGNER_WORKFLOW = "https://github.com/astral-sh/uv/.github/workflows/release.yml@refs/heads/main"
UV_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
UV_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SETUP_UV_VERSION = "v9.0.0"
SETUP_UV_COMMIT = "c771a70e6277c0a99b617c7a806ffedaca235ff9"
POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "uv-bootstrap-policy.json"
DEFAULT_INSTALL_DIR = Path(__file__).resolve().parents[1] / ".tools" / "uv"
DEFAULT_RECEIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".tools" / "receipts" / "uv-bootstrap.json"
)

PLATFORM_KEYS = frozenset(
    {
        "linux-x86_64",
        "linux-arm64",
        "macos-x86_64",
        "macos-arm64",
        "windows-x86_64",
        "windows-arm64",
    }
)
UV_ASSET_SHAPE = {
    "linux-x86_64": (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "uv-x86_64-unknown-linux-gnu/uv",
    ),
    "linux-arm64": (
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "uv-aarch64-unknown-linux-gnu/uv",
    ),
    "macos-x86_64": (
        "uv-x86_64-apple-darwin.tar.gz",
        "uv-x86_64-apple-darwin/uv",
    ),
    "macos-arm64": (
        "uv-aarch64-apple-darwin.tar.gz",
        "uv-aarch64-apple-darwin/uv",
    ),
    "windows-x86_64": (
        "uv-x86_64-pc-windows-msvc.zip",
        "uv-x86_64-pc-windows-msvc/uv.exe",
    ),
    "windows-arm64": (
        "uv-aarch64-pc-windows-msvc.zip",
        "uv-aarch64-pc-windows-msvc/uv.exe",
    ),
}
UV_TARGET_TRIPLES = {
    "linux-x86_64": "x86_64-unknown-linux-gnu",
    "linux-arm64": "aarch64-unknown-linux-gnu",
    "macos-x86_64": "x86_64-apple-darwin",
    "macos-arm64": "aarch64-apple-darwin",
    "windows-x86_64": "x86_64-pc-windows-msvc",
    "windows-arm64": "aarch64-pc-windows-msvc",
}
GH_ASSET_SHAPE = {
    "linux-x86_64": (
        "gh_2.97.0_linux_amd64.tar.gz",
        "gh_2.97.0_linux_amd64/bin/gh",
    ),
    "linux-arm64": (
        "gh_2.97.0_linux_arm64.tar.gz",
        "gh_2.97.0_linux_arm64/bin/gh",
    ),
    "macos-x86_64": (
        "gh_2.97.0_macOS_amd64.zip",
        "gh_2.97.0_macOS_amd64/bin/gh",
    ),
    "macos-arm64": (
        "gh_2.97.0_macOS_arm64.zip",
        "gh_2.97.0_macOS_arm64/bin/gh",
    ),
    "windows-x86_64": (
        "gh_2.97.0_windows_amd64.zip",
        "bin/gh.exe",
    ),
    "windows-arm64": (
        "gh_2.97.0_windows_arm64.zip",
        "bin/gh.exe",
    ),
}
PYPI_ARTIFACT_URLS = {
    "pypi_attestations-0.0.30-py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/5f/24/e59078318b5e2bca59be5a21957d70e"
        "082e6eb4f478adffe373f3b74daf4/pypi_attestations-0.0.30-py3-none-any.whl"
    ),
    "pypi_attestations-0.0.30.tar.gz": (
        "https://files.pythonhosted.org/packages/97/1e/eb8a0233cdcaf01acb18076ac8b8a057"
        "714177b3717baf26b78a11b8d929/pypi_attestations-0.0.30.tar.gz"
    ),
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:")
DOWNLOAD_CONNECT_TIMEOUT_SECONDS = 10
DOWNLOAD_DEADLINE_SECONDS = 60
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
ATTESTATION_TIMEOUT_SECONDS = 60
POST_PREFLIGHT_FAILURE_CATEGORIES = frozenset(
    {
        "INSTALLED_UV_VERIFICATION_FAILED",
        "SETUP_UV_ACTION_FAILED",
    }
)
RECEIPT_TOP_LEVEL_KEYS = frozenset(
    {
        "failure_category",
        "policy",
        "provenance",
        "schema_version",
        "status",
        "tool",
        "verified_at",
        "verifier",
    }
)
RECEIPT_NESTED_KEYS = {
    "policy": frozenset({"schema_version", "sha256"}),
    "tool": frozenset(
        {
            "architecture",
            "asset_name",
            "asset_sha256",
            "binary_sha256",
            "name",
            "platform",
            "version",
        }
    ),
    "verifier": frozenset({"archive_name", "archive_sha256", "name", "version"}),
    "provenance": frozenset(
        {
            "oidc_issuer",
            "predicate_type",
            "signer_workflow_identity",
            "source_commit",
            "source_ref",
            "source_repository",
        }
    ),
}

Downloader = Callable[[str, Path, int], None]


class Runner(Protocol):
    """Execute a verified binary with an explicit environment and deadline."""

    def __call__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]: ...


class BootstrapError(RuntimeError):
    """A stable, fail-closed bootstrap failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> NoReturn:
    raise BootstrapError(code, message)


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail("POLICY_VALUE_INVALID", f"{label} must be an object")
    return value


def _expect_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    mapping = _expect_mapping(value, label)
    if set(mapping) != expected:
        _fail("POLICY_FIELDS_INVALID", f"{label} fields do not match schema v1")
    return mapping


def _expect_literal(value: Any, expected: str, label: str) -> None:
    if value != expected:
        _fail("POLICY_VALUE_INVALID", f"{label} is not the reviewed value")


def _expect_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        _fail("POLICY_VALUE_INVALID", f"{label} must be a lowercase SHA-256 digest")


def _expect_positive_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail("POLICY_VALUE_INVALID", f"{label} must be a positive integer")


def _validate_assets(
    value: Any,
    *,
    label: str,
    expected_shape: Mapping[str, tuple[str, str]],
    include_binary_digest: bool,
) -> None:
    assets = _expect_mapping(value, label)
    if set(assets) != PLATFORM_KEYS:
        _fail("POLICY_FIELDS_INVALID", f"{label} must contain every supported platform")
    fields = {"archive_name", "sha256", "size_bytes", "binary_path"}
    if include_binary_digest:
        fields.add("binary_sha256")
    for platform_key, expected in expected_shape.items():
        asset = _expect_keys(assets[platform_key], fields, f"{label}.{platform_key}")
        _expect_literal(asset["archive_name"], expected[0], f"{label}.{platform_key}.archive_name")
        _expect_literal(asset["binary_path"], expected[1], f"{label}.{platform_key}.binary_path")
        _expect_sha256(asset["sha256"], f"{label}.{platform_key}.sha256")
        _expect_positive_int(asset["size_bytes"], f"{label}.{platform_key}.size_bytes")
        if include_binary_digest:
            _expect_sha256(asset["binary_sha256"], f"{label}.{platform_key}.binary_sha256")


def _validate_policy(payload: Any) -> dict[str, Any]:
    policy = _expect_keys(
        payload,
        {
            "schema_version",
            "uv",
            "github_cli",
            "setup_uv_action",
            "python_verifiers",
        },
        "policy",
    )
    if policy["schema_version"] != POLICY_SCHEMA_VERSION:
        _fail("POLICY_SCHEMA_UNSUPPORTED", "only bootstrap policy schema v1 is supported")

    uv = _expect_keys(
        policy["uv"],
        {
            "version",
            "release_repository",
            "release_tag",
            "release_url_template",
            "source_repository",
            "source_commit",
            "source_ref",
            "oidc_issuer",
            "signer_workflow_identity",
            "predicate_type",
            "assets",
        },
        "uv",
    )
    reviewed_uv_values = {
        "version": UV_VERSION,
        "release_repository": UV_REPOSITORY,
        "release_tag": UV_VERSION,
        "release_url_template": RELEASE_URL_TEMPLATE,
        "source_repository": UV_SOURCE_REPOSITORY,
        "source_commit": UV_SOURCE_COMMIT,
        "source_ref": UV_SOURCE_REF,
        "oidc_issuer": UV_OIDC_ISSUER,
        "signer_workflow_identity": UV_SIGNER_WORKFLOW,
        "predicate_type": UV_PREDICATE_TYPE,
    }
    for field, expected in reviewed_uv_values.items():
        _expect_literal(uv[field], expected, f"uv.{field}")
    _validate_assets(
        uv["assets"],
        label="uv.assets",
        expected_shape=UV_ASSET_SHAPE,
        include_binary_digest=True,
    )

    github_cli = _expect_keys(
        policy["github_cli"],
        {
            "version",
            "release_repository",
            "release_tag",
            "release_url_template",
            "assets",
        },
        "github_cli",
    )
    reviewed_gh_values = {
        "version": GH_VERSION,
        "release_repository": "cli/cli",
        "release_tag": f"v{GH_VERSION}",
        "release_url_template": RELEASE_URL_TEMPLATE,
    }
    for field, expected in reviewed_gh_values.items():
        _expect_literal(github_cli[field], expected, f"github_cli.{field}")
    _validate_assets(
        github_cli["assets"],
        label="github_cli.assets",
        expected_shape=GH_ASSET_SHAPE,
        include_binary_digest=False,
    )

    setup_uv = _expect_keys(policy["setup_uv_action"], {"version", "commit"}, "setup_uv_action")
    _expect_literal(setup_uv["version"], SETUP_UV_VERSION, "setup_uv_action.version")
    _expect_literal(setup_uv["commit"], SETUP_UV_COMMIT, "setup_uv_action.commit")

    verifiers = _expect_keys(policy["python_verifiers"], {"pypi-attestations"}, "python_verifiers")
    verifier = _expect_keys(
        verifiers["pypi-attestations"],
        {
            "version",
            "project",
            "project_url",
            "source_repository",
            "reviewed_update_procedure",
            "artifacts",
        },
        "python_verifiers.pypi-attestations",
    )
    verifier_values = {
        "version": "0.0.30",
        "project": "pypi-attestations",
        "project_url": "https://pypi.org/project/pypi-attestations/",
        "source_repository": "https://github.com/pypi/pypi-attestations",
        "reviewed_update_procedure": (
            "docs/security/verified-uv-bootstrap.md#reviewed-policy-updates"
        ),
    }
    for field, expected in verifier_values.items():
        _expect_literal(verifier[field], expected, f"python_verifiers.pypi-attestations.{field}")
    artifacts = verifier["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(PYPI_ARTIFACT_URLS):
        _fail("POLICY_FIELDS_INVALID", "the verifier artifact allowlist is incomplete")
    seen_artifacts: set[str] = set()
    for index, raw_artifact in enumerate(artifacts):
        artifact = _expect_keys(
            raw_artifact,
            {"filename", "sha256", "url"},
            f"python_verifiers.pypi-attestations.artifacts[{index}]",
        )
        filename = artifact["filename"]
        if not isinstance(filename, str) or filename not in PYPI_ARTIFACT_URLS:
            _fail("POLICY_VALUE_INVALID", "an unreviewed verifier artifact was supplied")
        if filename in seen_artifacts:
            _fail("POLICY_VALUE_INVALID", "a duplicate verifier artifact was supplied")
        seen_artifacts.add(filename)
        _expect_literal(
            artifact["url"],
            PYPI_ARTIFACT_URLS[filename],
            f"python verifier artifact {filename} URL",
        )
        _expect_sha256(artifact["sha256"], f"python verifier artifact {filename}")
    if seen_artifacts != set(PYPI_ARTIFACT_URLS):
        _fail("POLICY_FIELDS_INVALID", "the verifier artifact allowlist is incomplete")
    return policy


def load_policy(path: Path) -> dict[str, Any]:
    """Load and strictly validate bootstrap policy schema v1."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("POLICY_READ_FAILED", "bootstrap policy is unreadable") from exc
    return _validate_policy(payload)


def _normalize_platform(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "linux": "linux",
        "darwin": "macos",
        "macos": "macos",
        "win32": "windows",
        "windows": "windows",
        "cygwin": "windows",
        "msys": "windows",
    }
    if normalized not in aliases:
        _fail("PLATFORM_UNSUPPORTED", "operating system is not in bootstrap policy")
    return aliases[normalized]


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    if normalized not in aliases:
        _fail("ARCHITECTURE_UNSUPPORTED", "architecture is not in bootstrap policy")
    return aliases[normalized]


def select_platform(
    policy: Mapping[str, Any],
    platform_id: tuple[str, str] | None = None,
) -> tuple[str, str, str, Mapping[str, Any], Mapping[str, Any]]:
    """Select the exact uv and GitHub CLI assets for one supported platform."""

    raw_platform, raw_architecture = platform_id or (sys.platform, platform.machine())
    operating_system = _normalize_platform(raw_platform)
    architecture = _normalize_architecture(raw_architecture)
    key = f"{operating_system}-{architecture}"
    if key not in PLATFORM_KEYS:
        _fail("PLATFORM_UNSUPPORTED", "platform and architecture pair is not supported")
    return (
        operating_system,
        architecture,
        key,
        policy["uv"]["assets"][key],
        policy["github_cli"]["assets"][key],
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BootstrapError("ARTIFACT_READ_FAILED", "artifact could not be hashed") from exc
    return digest.hexdigest()


def _digest_matches(path: Path, expected: str) -> bool:
    return hmac.compare_digest(_sha256_file(path), expected)


def _safe_output_path(destination: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or DRIVE_PATH_PATTERN.match(normalized)
    ):
        _fail("ARCHIVE_MEMBER_UNSAFE", "archive contains an absolute member path")
    pure_path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        _fail("ARCHIVE_MEMBER_UNSAFE", "archive contains path traversal")
    root = destination.resolve()
    output = root.joinpath(*pure_path.parts).resolve()
    if output != root and root not in output.parents:
        _fail("ARCHIVE_MEMBER_UNSAFE", "archive member escapes extraction root")
    return output


def _validate_extraction_root(destination: Path) -> None:
    if destination.is_symlink():
        _fail("ARCHIVE_MEMBER_UNSAFE", "extraction root cannot be a symbolic link")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        _fail("ARCHIVE_MEMBER_UNSAFE", "extraction root must be empty")


def _extract_tar(archive_path: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = archive.getmembers()
            outputs: set[Path] = set()
            for member in members:
                output = _safe_output_path(destination, member.name)
                if output in outputs or not (member.isdir() or member.isfile()):
                    _fail(
                        "ARCHIVE_MEMBER_UNSAFE",
                        "archive contains a duplicate, link, or special member",
                    )
                outputs.add(output)
            for member in members:
                output = _safe_output_path(destination, member.name)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    output.chmod(member.mode & 0o777)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    _fail("ARCHIVE_MEMBER_UNSAFE", "archive member has no payload")
                with extracted, output.open("xb") as target:
                    shutil.copyfileobj(extracted, target)
                output.chmod(member.mode & 0o777)
    except BootstrapError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise BootstrapError("ARCHIVE_READ_FAILED", "tar archive is invalid") from exc


def _extract_zip(archive_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            members = archive.infolist()
            outputs: set[Path] = set()
            for member in members:
                output = _safe_output_path(destination, member.filename)
                unix_mode = member.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                allowed_type = member.is_dir() or file_type in {0, stat.S_IFREG}
                if output in outputs or not allowed_type or bool(member.flag_bits & 0x1):
                    _fail(
                        "ARCHIVE_MEMBER_UNSAFE",
                        "ZIP contains a duplicate, encrypted, link, or special member",
                    )
                outputs.add(output)
            for member in members:
                output = _safe_output_path(destination, member.filename)
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, mode="r") as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target)
                unix_mode = member.external_attr >> 16
                output.chmod((unix_mode & 0o777) or 0o755)
    except BootstrapError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BootstrapError("ARCHIVE_READ_FAILED", "ZIP archive is invalid") from exc


def safe_extract_archive(archive_path: Path, destination: Path) -> None:
    """Extract a reviewed tar/ZIP without trusting member paths or member types."""

    _validate_extraction_root(destination)
    name = archive_path.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        _extract_tar(archive_path, destination)
    elif name.endswith(".zip"):
        _extract_zip(archive_path, destination)
    else:
        _fail("ARCHIVE_FORMAT_UNSUPPORTED", "archive format is not permitted")


def _discard_partial_download(destination: Path) -> None:
    try:
        destination.unlink(missing_ok=True)
    except OSError:
        pass


def _download_deadline_exceeded() -> NoReturn:
    _fail(
        "DOWNLOAD_DEADLINE_EXCEEDED",
        "release asset download exceeded its bounded deadline",
    )


def _close_download_response(response: Any) -> None:
    close_response = getattr(response, "close", None)
    if not callable(close_response):
        return
    try:
        close_response()
    except Exception:  # noqa: BLE001,S110 - preserve the coded failure
        pass


def _open_download_response(request: urllib.request.Request, deadline: float) -> Any:
    condition = threading.Condition()
    state: dict[str, Any] = {"cancelled": False}

    def open_response() -> None:
        response: Any | None = None
        failure: Exception | None = None
        try:
            response = urllib.request.urlopen(  # noqa: S310
                request,
                timeout=min(
                    float(DOWNLOAD_CONNECT_TIMEOUT_SECONDS),
                    float(DOWNLOAD_DEADLINE_SECONDS),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - preserve failures across thread boundary
            failure = exc

        close_after_cancellation = False
        with condition:
            if state["cancelled"]:
                close_after_cancellation = response is not None
            else:
                state["outcome"] = (response, failure)
                condition.notify_all()
        if close_after_cancellation:
            _close_download_response(response)

    worker = threading.Thread(
        target=open_response,
        name="uv-bootstrap-response-open",
        daemon=True,
    )
    worker.start()

    with condition:
        while "outcome" not in state:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                state["cancelled"] = True
                _download_deadline_exceeded()
            condition.wait(timeout=remaining_seconds)
        response, failure = state["outcome"]

    if time.monotonic() >= deadline:
        if response is not None:
            _close_download_response(response)
        _download_deadline_exceeded()
    if failure is not None:
        raise failure
    if response is None:
        _fail("DOWNLOAD_FAILED", "reviewed release asset response was unavailable")
    return response


def _set_response_read_timeout(response: Any, timeout: float) -> None:
    stream = response
    for _ in range(2):
        stream = getattr(stream, "fp", None)
        if stream is None:
            break
        raw_stream = getattr(stream, "raw", None)
        transport = getattr(raw_stream, "_sock", None)
        set_timeout = getattr(transport, "settimeout", None)
        if callable(set_timeout):
            set_timeout(timeout)
            return
    _fail(
        "DOWNLOAD_FAILED",
        "reviewed release asset transport cannot enforce bounded reads",
    )


def _read_download_chunk(response: Any, size: int, deadline: float) -> bytes:
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        _download_deadline_exceeded()
    deadline_limits_read = remaining_seconds <= DOWNLOAD_CONNECT_TIMEOUT_SECONDS
    _set_response_read_timeout(
        response,
        min(float(DOWNLOAD_CONNECT_TIMEOUT_SECONDS), remaining_seconds),
    )
    read_once = getattr(response, "read1", None)
    if not callable(read_once):
        _fail(
            "DOWNLOAD_FAILED",
            "reviewed release asset transport does not support bounded reads",
        )
    try:
        chunk = read_once(size)
    except TimeoutError as exc:
        if deadline_limits_read:
            raise BootstrapError(
                "DOWNLOAD_DEADLINE_EXCEEDED",
                "release asset download exceeded its bounded deadline",
            ) from exc
        raise
    if time.monotonic() >= deadline:
        _download_deadline_exceeded()
    return chunk


def _download(url: str, destination: Path, expected_size: int) -> None:
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        _fail("DOWNLOAD_SIZE_MISMATCH", "reviewed release asset size is invalid")
    request = urllib.request.Request(  # noqa: S310 - exact policy URL is validated
        url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "AncestryLLM verified uv bootstrap/1",
        },
    )
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    try:
        with _open_download_response(request, deadline) as response:
            content_length = response.headers.get("Content-Length")
            if (
                not isinstance(content_length, str)
                or not content_length.isdecimal()
                or int(content_length) != expected_size
            ):
                _fail(
                    "DOWNLOAD_SIZE_MISMATCH",
                    "release asset size does not match bootstrap policy",
                )

            bytes_written = 0
            with destination.open("xb") as target:
                while bytes_written < expected_size:
                    remaining = expected_size - bytes_written
                    chunk = _read_download_chunk(
                        response,
                        min(DOWNLOAD_CHUNK_SIZE, remaining + 1),
                        deadline,
                    )
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > expected_size:
                        _fail(
                            "DOWNLOAD_SIZE_MISMATCH",
                            "release asset exceeded its reviewed size",
                        )
                    target.write(chunk)
            if bytes_written != expected_size:
                _fail(
                    "DOWNLOAD_SIZE_MISMATCH",
                    "release asset ended before its reviewed size",
                )
    except BootstrapError:
        _discard_partial_download(destination)
        raise
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError) as exc:
        _discard_partial_download(destination)
        raise BootstrapError("DOWNLOAD_FAILED", "reviewed release asset download failed") from exc


def _create_temporary_workspace(
    temporary_parent: Path | None,
) -> tempfile.TemporaryDirectory[str]:
    try:
        if temporary_parent is not None:
            temporary_parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=temporary_parent)
    except OSError as exc:
        raise BootstrapError(
            "TEMPORARY_WORKSPACE_FAILED",
            "temporary bootstrap workspace could not be created",
        ) from exc


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float | None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - executable was hash-verified first
            list(command),
            check=False,
            capture_output=True,
            text=True,
            env=dict(env),
            timeout=timeout,
        )
    except OSError as exc:
        raise BootstrapError("VERIFIER_EXECUTION_FAILED", "verified executable failed") from exc


def _runner_environment(*, allow_github_credentials: bool) -> dict[str, str]:
    environment = dict(os.environ)
    if allow_github_credentials:
        return environment
    for name in tuple(environment):
        if name.upper() in {"GH_TOKEN", "GITHUB_TOKEN"}:
            del environment[name]
    return environment


def _run_checked(
    runner: Runner,
    command: Sequence[str],
    *,
    code: str,
    message: str,
) -> subprocess.CompletedProcess[str]:
    result = runner(
        command,
        env=_runner_environment(allow_github_credentials=False),
        timeout=None,
    )
    if result.returncode != 0:
        _fail(code, message)
    return result


def _verify_attestation(
    runner: Runner,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            command,
            env=_runner_environment(allow_github_credentials=True),
            timeout=ATTESTATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(
            "ATTESTATION_VERIFICATION_TIMEOUT",
            "GitHub attestation verification exceeded its bounded deadline",
        ) from exc
    if result.returncode == 4:
        _fail(
            "VERIFIER_AUTHENTICATION_FAILED",
            "GitHub attestation authentication is required; run "
            "gh auth login --hostname github.com locally or set GH_TOKEN "
            "from a secret manager for headless use",
        )
    if result.returncode != 0:
        _fail("ATTESTATION_VERIFICATION_FAILED", "GitHub CLI rejected uv provenance")
    return result


def _release_url(tool: Mapping[str, Any], asset: Mapping[str, Any]) -> str:
    return tool["release_url_template"].format(
        repository=tool["release_repository"],
        tag=tool["release_tag"],
        asset=asset["archive_name"],
    )


def _verify_attestation_payload(
    stdout: str,
    *,
    archive_name: str,
    archive_sha256: str,
) -> None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError(
            "ATTESTATION_OUTPUT_INVALID", "attestation verifier returned invalid JSON"
        ) from exc
    if not isinstance(payload, list) or not payload:
        _fail("ATTESTATION_OUTPUT_INVALID", "attestation verifier returned no result")

    expected_certificate = {
        "subjectAlternativeName": UV_SIGNER_WORKFLOW,
        "issuer": UV_OIDC_ISSUER,
        "githubWorkflowSHA": UV_SOURCE_COMMIT,
        "githubWorkflowRepository": UV_REPOSITORY,
        "githubWorkflowRef": UV_SOURCE_REF,
        "buildSignerURI": UV_SIGNER_WORKFLOW,
        "buildSignerDigest": UV_SOURCE_COMMIT,
        "sourceRepositoryURI": UV_SOURCE_REPOSITORY,
        "sourceRepositoryDigest": UV_SOURCE_COMMIT,
        "sourceRepositoryRef": UV_SOURCE_REF,
    }
    for item in payload:
        try:
            verification = item["verificationResult"]
            certificate = verification["signature"]["certificate"]
            statement = verification["statement"]
            subjects = statement["subject"]
        except (KeyError, TypeError):
            continue
        if not isinstance(certificate, dict) or any(
            certificate.get(field) != expected for field, expected in expected_certificate.items()
        ):
            continue
        if statement.get("predicateType") != UV_PREDICATE_TYPE:
            continue
        if not isinstance(subjects, list):
            continue
        matching_subjects = [
            subject
            for subject in subjects
            if isinstance(subject, dict) and subject.get("name") == archive_name
        ]
        if len(matching_subjects) != 1:
            continue
        subject = matching_subjects[0]
        digest = subject.get("digest")
        if isinstance(digest, dict) and digest.get("sha256") == archive_sha256:
            return
    _fail(
        "ATTESTATION_IDENTITY_MISMATCH",
        "verified attestation does not match every reviewed identity constraint",
    )


def _attestation_command(
    gh_path: Path,
    uv_archive_path: Path,
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    uv = policy["uv"]
    return (
        str(gh_path),
        "attestation",
        "verify",
        str(uv_archive_path),
        "--hostname",
        "github.com",
        "--repo",
        uv["release_repository"],
        "--source-digest",
        uv["source_commit"],
        "--source-ref",
        uv["source_ref"],
        "--cert-identity",
        uv["signer_workflow_identity"],
        "--cert-oidc-issuer",
        uv["oidc_issuer"],
        "--predicate-type",
        uv["predicate_type"],
        "--format",
        "json",
    )


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        _fail("CLOCK_INVALID", "verification clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _build_receipt(
    *,
    policy_sha256: str,
    policy: Mapping[str, Any],
    operating_system: str,
    architecture: str,
    uv_asset: Mapping[str, Any],
    gh_asset: Mapping[str, Any],
    verified_at: str,
    status: str = "success",
    failure_category: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "policy": {
            "schema_version": policy["schema_version"],
            "sha256": policy_sha256,
        },
        "tool": {
            "name": "uv",
            "version": policy["uv"]["version"],
            "platform": operating_system,
            "architecture": architecture,
            "asset_name": uv_asset["archive_name"],
            "asset_sha256": uv_asset["sha256"],
            "binary_sha256": uv_asset["binary_sha256"],
        },
        "verifier": {
            "name": "GitHub CLI",
            "version": policy["github_cli"]["version"],
            "archive_name": gh_asset["archive_name"],
            "archive_sha256": gh_asset["sha256"],
        },
        "provenance": {
            "source_repository": policy["uv"]["source_repository"],
            "source_commit": policy["uv"]["source_commit"],
            "source_ref": policy["uv"]["source_ref"],
            "signer_workflow_identity": policy["uv"]["signer_workflow_identity"],
            "oidc_issuer": policy["uv"]["oidc_issuer"],
            "predicate_type": policy["uv"]["predicate_type"],
        },
        "verified_at": verified_at,
        "status": status,
        "failure_category": failure_category,
    }


def _build_initialization_receipt() -> dict[str, Any]:
    """Build the minimal envelope used before verified identity is available."""

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "failure",
        "failure_category": None,
    }


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        path_status = os.lstat(path)
    except FileNotFoundError:
        return False
    windows_reparse_point = bool(getattr(path_status, "st_file_attributes", 0) & 0x400)
    return stat.S_ISLNK(path_status.st_mode) or windows_reparse_point


def _raise_unsafe_component_if_applicable(
    error: OSError,
    *,
    code: str,
    message: str,
) -> None:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        raise BootstrapError(code, message) from error


@contextmanager
def _open_posix_parent(
    destination: Path,
    *,
    code: str,
    message: str,
) -> Iterator[int]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    components = destination.parent.parts
    if not components or components[0] != os.sep:
        _fail(code, message)

    descriptor: int | None = None
    try:
        descriptor = os.open(components[0], directory_flags)
        for component in components[1:]:
            try:
                child_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
                try:
                    child_descriptor = os.open(
                        component,
                        directory_flags,
                        dir_fd=descriptor,
                    )
                except OSError as exc:
                    _raise_unsafe_component_if_applicable(exc, code=code, message=message)
                    raise
            except OSError as exc:
                _raise_unsafe_component_if_applicable(exc, code=code, message=message)
                raise
            os.close(descriptor)
            descriptor = child_descriptor
        yield descriptor
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def _lock_windows_parent(
    destination: Path,
    *,
    code: str,
    message: str,
) -> Iterator[None]:
    # These attributes exist only on Windows; deferred lookup keeps POSIX imports valid.
    loader: Any = getattr(ctypes, "WinDLL")  # noqa: B009
    kernel32: Any = loader("kernel32", use_last_error=True)
    create_file: Any = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    close_handle: Any = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    file_read_attributes = 0x80
    file_share_read = 0x1
    file_share_write = 0x2
    open_existing = 0x3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[int] = []
    try:
        parents = tuple(reversed((destination.parent, *destination.parent.parents)))
        for candidate in parents:
            if not os.path.lexists(candidate):
                try:
                    candidate.mkdir()
                except FileExistsError:
                    pass
            handle = create_file(
                str(candidate),
                file_read_attributes,
                file_share_read | file_share_write,
                None,
                open_existing,
                file_flag_backup_semantics | file_flag_open_reparse_point,
                None,
            )
            if handle is None or handle == invalid_handle:
                if _is_link_or_reparse_point(candidate):
                    _fail(code, message)
                last_error: Callable[[], int] = getattr(  # noqa: B009
                    ctypes,
                    "get_last_error",
                )
                raise OSError(last_error(), "could not lock destination directory")
            handle_value = int(handle)
            handles.append(handle_value)
            candidate_status = os.lstat(candidate)
            if _is_link_or_reparse_point(candidate) or not stat.S_ISDIR(candidate_status.st_mode):
                _fail(code, message)
        if _is_link_or_reparse_point(destination):
            _fail(code, message)
        yield
    finally:
        for handle in reversed(handles):
            close_handle(handle)


@contextmanager
def _anchored_parent(
    destination: Path,
    *,
    code: str,
    message: str,
) -> Iterator[tuple[Path, int | None]]:
    absolute_destination = Path(os.path.abspath(destination))
    if os.name == "nt":
        with _lock_windows_parent(absolute_destination, code=code, message=message):
            yield absolute_destination, None
        return

    with _open_posix_parent(absolute_destination, code=code, message=message) as parent_fd:
        try:
            destination_status = os.stat(
                absolute_destination.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(destination_status.st_mode):
                _fail(code, message)
        yield absolute_destination, parent_fd


def _create_temporary_file(destination: Path, parent_fd: int | None) -> tuple[int, str]:
    prefix = f".{destination.name}."
    if parent_fd is None:
        return tempfile.mkstemp(dir=destination.parent, prefix=prefix, suffix=".tmp")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(128):
        temporary_name = f"{prefix}{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                mode=0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise OSError(errno.EEXIST, "could not allocate a unique temporary file")


def _commit_temporary_file(
    temporary_name: str,
    destination: Path,
    parent_fd: int | None,
) -> None:
    if parent_fd is None:
        os.replace(temporary_name, destination)
        return
    os.replace(
        temporary_name,
        destination.name,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
    )


def _discard_partial_file(temporary_name: str | None, parent_fd: int | None) -> None:
    if temporary_name is None:
        return
    try:
        if parent_fd is None:
            Path(temporary_name).unlink(missing_ok=True)
        else:
            os.unlink(temporary_name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _write_receipt_to_parent(
    destination: Path,
    parent_fd: int | None,
    receipt: Mapping[str, Any],
) -> None:
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        rendered = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor, temporary_name = _create_temporary_file(destination, parent_fd)
        output_stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with output_stream as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        _commit_temporary_file(temporary_name, destination, parent_fd)
    finally:
        _discard_install_descriptor(descriptor)
        _discard_partial_file(temporary_name, parent_fd)


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    try:
        with _anchored_parent(
            path,
            code="RECEIPT_PATH_UNSAFE",
            message="verification receipt path contains a symbolic-link component",
        ) as (destination, parent_fd):
            _write_receipt_to_parent(destination, parent_fd, receipt)
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError(
            "RECEIPT_WRITE_FAILED", "verification receipt could not be written"
        ) from exc


def _write_failure_receipt(
    path: Path,
    receipt: Mapping[str, Any],
    failure: BootstrapError,
) -> None:
    if failure.code in {"RECEIPT_PATH_UNSAFE", "RECEIPT_WRITE_FAILED"}:
        raise failure
    failed_receipt = dict(receipt)
    failed_receipt["status"] = "failure"
    failed_receipt["failure_category"] = failure.code
    try:
        _write_receipt(path, failed_receipt)
    except BootstrapError as receipt_failure:
        raise receipt_failure from failure


def _read_receipt_from_parent(
    destination: Path,
    parent_fd: int | None,
) -> dict[str, Any]:
    flags = os.O_RDONLY
    if parent_fd is not None:
        flags |= os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        if parent_fd is None:
            descriptor = os.open(destination, flags)
        else:
            descriptor = os.open(destination.name, flags, dir_fd=parent_fd)
        input_stream = os.fdopen(descriptor, encoding="utf-8")
        descriptor = None
        with input_stream as stream:
            receipt = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(
            "RECEIPT_READ_FAILED",
            "verification receipt could not be read",
        ) from exc
    finally:
        _discard_install_descriptor(descriptor)
    if not isinstance(receipt, dict) or not all(isinstance(key, str) for key in receipt):
        _fail("RECEIPT_STATE_INVALID", "verification receipt must be an object")
    return receipt


def _validate_success_receipt(receipt: Mapping[str, Any]) -> None:
    def invalid() -> NoReturn:
        _fail(
            "RECEIPT_STATE_INVALID",
            "verification receipt is not a canonical schema-v1 success receipt",
        )

    if frozenset(receipt) != RECEIPT_TOP_LEVEL_KEYS:
        invalid()
    nested: dict[str, Mapping[str, Any]] = {}
    for field, expected_keys in RECEIPT_NESTED_KEYS.items():
        value = receipt[field]
        if not isinstance(value, dict) or frozenset(value) != expected_keys:
            invalid()
        nested[field] = value

    policy = nested["policy"]
    tool = nested["tool"]
    verifier = nested["verifier"]
    provenance = nested["provenance"]
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != RECEIPT_SCHEMA_VERSION
        or receipt["status"] != "success"
        or receipt["failure_category"] is not None
        or type(policy["schema_version"]) is not int
        or policy["schema_version"] != POLICY_SCHEMA_VERSION
        or not isinstance(policy["sha256"], str)
        or SHA256_PATTERN.fullmatch(policy["sha256"]) is None
        or tool["name"] != "uv"
        or tool["version"] != UV_VERSION
        or verifier["name"] != "GitHub CLI"
        or verifier["version"] != GH_VERSION
    ):
        invalid()

    if not isinstance(tool["platform"], str) or not isinstance(tool["architecture"], str):
        invalid()
    platform_key = f"{tool['platform']}-{tool['architecture']}"
    if platform_key not in PLATFORM_KEYS:
        invalid()
    if (
        tool["asset_name"] != UV_ASSET_SHAPE[platform_key][0]
        or verifier["archive_name"] != GH_ASSET_SHAPE[platform_key][0]
    ):
        invalid()
    for owner, field in (
        (tool, "asset_sha256"),
        (tool, "binary_sha256"),
        (verifier, "archive_sha256"),
    ):
        value = owner[field]
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            invalid()

    expected_provenance = {
        "source_repository": UV_SOURCE_REPOSITORY,
        "source_commit": UV_SOURCE_COMMIT,
        "source_ref": UV_SOURCE_REF,
        "signer_workflow_identity": UV_SIGNER_WORKFLOW,
        "oidc_issuer": UV_OIDC_ISSUER,
        "predicate_type": UV_PREDICATE_TYPE,
    }
    if provenance != expected_provenance:
        invalid()
    verified_at = receipt["verified_at"]
    if not isinstance(verified_at, str) or not verified_at.endswith("Z"):
        invalid()
    try:
        parsed_timestamp = datetime.fromisoformat(f"{verified_at[:-1]}+00:00")
    except ValueError:
        invalid()
    if parsed_timestamp.utcoffset() != UTC.utcoffset(parsed_timestamp):
        invalid()


def record_post_preflight_failure(path: Path, failure_category: str) -> dict[str, Any]:
    """Replace a preflight success receipt when a later action phase fails."""

    if failure_category not in POST_PREFLIGHT_FAILURE_CATEGORIES:
        _fail(
            "FAILURE_CATEGORY_INVALID",
            "post-preflight failure category is not permitted",
        )
    try:
        with _anchored_parent(
            path,
            code="RECEIPT_PATH_UNSAFE",
            message="verification receipt path contains a symbolic-link component",
        ) as (destination, parent_fd):
            receipt = _read_receipt_from_parent(destination, parent_fd)
            _validate_success_receipt(receipt)
            failed_receipt = {
                **receipt,
                "status": "failure",
                "failure_category": failure_category,
            }
            _write_receipt_to_parent(destination, parent_fd, failed_receipt)
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError(
            "RECEIPT_WRITE_FAILED",
            "verification receipt could not be written",
        ) from exc
    return failed_receipt


def _discard_install_descriptor(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _atomic_install(source: Path, destination: Path) -> None:
    try:
        with _anchored_parent(
            destination,
            code="INSTALL_PATH_UNSAFE",
            message="uv install path contains a symbolic-link component",
        ) as (anchored_destination, parent_fd):
            temporary_name: str | None = None
            descriptor: int | None = None
            try:
                descriptor, temporary_name = _create_temporary_file(
                    anchored_destination,
                    parent_fd,
                )
                output_stream = os.fdopen(descriptor, "wb")
                descriptor = None
                with output_stream as output, source.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output)
                    output.flush()
                    if parent_fd is not None:
                        os.fchmod(  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                            output.fileno(), 0o700
                        )
                    os.fsync(output.fileno())
                if parent_fd is None:
                    # The verified tool must be directly executable after installation.
                    os.chmod(  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                        temporary_name, 0o700
                    )
                _commit_temporary_file(temporary_name, anchored_destination, parent_fd)
            finally:
                _discard_install_descriptor(descriptor)
                _discard_partial_file(temporary_name, parent_fd)
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError(
            "INSTALL_WRITE_FAILED",
            "verified uv could not be installed atomically",
        ) from exc


def _assert_install_path_safe(destination: Path) -> None:
    _assert_path_without_symlinks(
        destination,
        code="INSTALL_PATH_UNSAFE",
        message="uv install path contains a symbolic-link component",
    )


def _assert_path_without_symlinks(
    destination: Path,
    *,
    code: str,
    message: str,
) -> None:
    absolute_destination = Path(os.path.abspath(destination))
    for candidate in (absolute_destination, *absolute_destination.parents):
        if _is_link_or_reparse_point(candidate):
            _fail(code, message)


def _assert_binary(path: Path, expected_sha256: str, error_code: str) -> None:
    if path.is_symlink() or not path.is_file():
        _fail(error_code, "expected executable is absent or not a regular file")
    if not _digest_matches(path, expected_sha256):
        _fail(error_code, "executable digest does not match bootstrap policy")


def _assert_uv_version(
    uv_path: Path,
    runner: Runner,
    *,
    expected_target: str,
    error_code: str = "UV_VERSION_MISMATCH",
) -> None:
    result = _run_checked(
        runner,
        (str(uv_path), "--version"),
        code=error_code,
        message="verified uv executable could not report its version",
    )
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    version_prefix = f"uv {UV_VERSION}"
    build_metadata_output = re.compile(
        rf"{re.escape(version_prefix)} "
        rf"\({re.escape(UV_SOURCE_COMMIT[:9])} "
        rf"\d{{4}}-\d{{2}}-\d{{2}} {re.escape(expected_target)}\)"
    )
    permitted_outputs = {
        version_prefix,
        f"{version_prefix} ({expected_target})",
    }
    if first_line not in permitted_outputs and build_metadata_output.fullmatch(first_line) is None:
        _fail(error_code, "uv executable reported an unexpected version")


def bootstrap_uv(
    *,
    policy_path: Path,
    install_dir: Path,
    receipt_path: Path,
    downloader: Downloader = _download,
    runner: Runner = _run,
    platform_id: tuple[str, str] | None = None,
    temporary_root: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Install and execute uv only after every reviewed trust check succeeds."""

    receipt = _build_initialization_receipt()
    try:
        policy = load_policy(policy_path)
        operating_system, architecture, platform_key, uv_asset, gh_asset = select_platform(
            policy, platform_id
        )
        expected_target = UV_TARGET_TRIPLES[platform_key]
        policy_sha256 = _sha256_file(policy_path)
        verified_at = _timestamp(now)
        binary_name = "uv.exe" if operating_system == "windows" else "uv"
        installed_uv = install_dir / binary_name
        receipt = _build_receipt(
            policy_sha256=policy_sha256,
            policy=policy,
            operating_system=operating_system,
            architecture=architecture,
            uv_asset=uv_asset,
            gh_asset=gh_asset,
            verified_at=verified_at,
        )

        _assert_install_path_safe(installed_uv)
        if installed_uv.exists() or installed_uv.is_symlink():
            _assert_binary(
                installed_uv,
                uv_asset["binary_sha256"],
                "CACHED_BINARY_DIGEST_MISMATCH",
            )
            _write_receipt(receipt_path, receipt)
            _assert_uv_version(
                installed_uv,
                runner,
                expected_target=expected_target,
            )
            return receipt

        with _create_temporary_workspace(temporary_root) as temporary_name:
            temporary = Path(temporary_name)
            gh_archive = temporary / gh_asset["archive_name"]
            downloader(
                _release_url(policy["github_cli"], gh_asset),
                gh_archive,
                gh_asset["size_bytes"],
            )
            if not _digest_matches(gh_archive, gh_asset["sha256"]):
                _fail(
                    "VERIFIER_ARCHIVE_DIGEST_MISMATCH",
                    "GitHub CLI archive digest does not match bootstrap policy",
                )
            gh_root = temporary / "github-cli"
            safe_extract_archive(gh_archive, gh_root)
            gh_path = gh_root / gh_asset["binary_path"]
            if gh_path.is_symlink() or not gh_path.is_file():
                _fail("VERIFIER_BINARY_MISSING", "verified GitHub CLI archive lacks gh")
            gh_result = _run_checked(
                runner,
                (str(gh_path), "--version"),
                code="VERIFIER_VERSION_MISMATCH",
                message="verified GitHub CLI did not execute",
            )
            first_line = (
                gh_result.stdout.strip().splitlines()[0] if gh_result.stdout.strip() else ""
            )
            if not first_line.startswith(f"gh version {GH_VERSION} "):
                _fail(
                    "VERIFIER_VERSION_MISMATCH",
                    "GitHub CLI reported an unexpected version",
                )

            uv_archive = temporary / uv_asset["archive_name"]
            downloader(
                _release_url(policy["uv"], uv_asset),
                uv_archive,
                uv_asset["size_bytes"],
            )
            if not _digest_matches(uv_archive, uv_asset["sha256"]):
                _fail(
                    "UV_ARCHIVE_DIGEST_MISMATCH",
                    "uv archive digest does not match bootstrap policy",
                )
            attestation = _verify_attestation(
                runner,
                _attestation_command(gh_path, uv_archive, policy),
            )
            _verify_attestation_payload(
                attestation.stdout,
                archive_name=uv_asset["archive_name"],
                archive_sha256=uv_asset["sha256"],
            )

            uv_root = temporary / "uv"
            safe_extract_archive(uv_archive, uv_root)
            extracted_uv = uv_root / uv_asset["binary_path"]
            _assert_binary(
                extracted_uv,
                uv_asset["binary_sha256"],
                "UV_BINARY_DIGEST_MISMATCH",
            )
            _write_receipt(receipt_path, receipt)
            _assert_uv_version(
                extracted_uv,
                runner,
                expected_target=expected_target,
            )
            _atomic_install(extracted_uv, installed_uv)
            _assert_binary(
                installed_uv,
                uv_asset["binary_sha256"],
                "INSTALLED_BINARY_DIGEST_MISMATCH",
            )
    except BootstrapError as exc:
        _write_failure_receipt(receipt_path, receipt, exc)
        raise
    return receipt


def verify_installed_uv(
    *,
    policy_path: Path,
    uv_path: Path,
    runner: Runner = _run,
    platform_id: tuple[str, str] | None = None,
) -> None:
    """Re-hash a setup-uv installation before its first execution."""

    policy = load_policy(policy_path)
    _, _, platform_key, uv_asset, _ = select_platform(policy, platform_id)
    _assert_binary(
        uv_path,
        uv_asset["binary_sha256"],
        "INSTALLED_BINARY_DIGEST_MISMATCH",
    )
    _assert_uv_version(
        uv_path,
        runner,
        expected_target=UV_TARGET_TRIPLES[platform_key],
        error_code="INSTALLED_VERSION_MISMATCH",
    )


def _platform_override(arguments: argparse.Namespace) -> tuple[str, str] | None:
    if arguments.platform is None and arguments.architecture is None:
        return None
    if arguments.platform is None or arguments.architecture is None:
        _fail(
            "PLATFORM_ARGUMENT_INVALID",
            "--platform and --architecture must be supplied together",
        )
    return arguments.platform, arguments.architecture


def _write_github_outputs(
    output_path: Path,
    *,
    receipt: Mapping[str, Any],
    receipt_path: Path,
) -> None:
    lines = (
        f"checksum={receipt['tool']['asset_sha256']}",
        f"binary_sha256={receipt['tool']['binary_sha256']}",
        f"platform={receipt['tool']['platform']}",
        f"architecture={receipt['tool']['architecture']}",
        f"receipt_path={receipt_path}",
    )
    try:
        with output_path.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    except OSError as exc:
        raise BootstrapError(
            "GITHUB_OUTPUT_WRITE_FAILED", "could not write action outputs"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="verify and install uv")
    bootstrap.add_argument("--policy", type=Path, default=POLICY_PATH)
    bootstrap.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    bootstrap.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT_PATH)
    bootstrap.add_argument("--platform")
    bootstrap.add_argument("--architecture")
    bootstrap.add_argument("--github-output", type=Path)

    verify = subparsers.add_parser("verify-installed", help="rehash setup-uv before execution")
    verify.add_argument("--policy", type=Path, default=POLICY_PATH)
    verify.add_argument("--uv-path", type=Path, required=True)
    verify.add_argument("--platform")
    verify.add_argument("--architecture")

    record_failure = subparsers.add_parser(
        "record-failure",
        help="replace a preflight success receipt after a later action failure",
    )
    record_failure.add_argument("--receipt", type=Path, required=True)
    record_failure.add_argument("--failure-category", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "record-failure":
            receipt = record_post_preflight_failure(
                arguments.receipt,
                arguments.failure_category,
            )
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
            return 0

        platform_id = _platform_override(arguments)
        if arguments.command == "bootstrap":
            receipt = bootstrap_uv(
                policy_path=arguments.policy,
                install_dir=arguments.install_dir,
                receipt_path=arguments.receipt,
                platform_id=platform_id,
            )
            if arguments.github_output is not None:
                _write_github_outputs(
                    arguments.github_output,
                    receipt=receipt,
                    receipt_path=arguments.receipt,
                )
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
            return 0

        verify_installed_uv(
            policy_path=arguments.policy,
            uv_path=arguments.uv_path,
            platform_id=platform_id,
        )
        return 0
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
