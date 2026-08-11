from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.build_sidecar import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA,
    executable_path,
    native_target,
    runtime_target,
    write_payload_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-x64"),
        ("Windows", "ARM64", "win32-arm64"),
        ("Windows", "AMD64", "win32-x64"),
        ("Linux", "x86_64", "linux-x64"),
    ],
)
def test_supported_native_targets(system: str, machine: str, expected: str) -> None:
    assert native_target(system, machine) == expected


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Linux", "aarch64"), ("FreeBSD", "x86_64")],
)
def test_unsupported_native_targets_fail_closed(system: str, machine: str) -> None:
    with pytest.raises(ValueError, match="unsupported native sidecar target"):
        native_target(system, machine)


def test_windows_arm64_host_requires_arm64_python_runtime() -> None:
    assert runtime_target("Windows", "ARM64", "win-arm64") == "win32-arm64"


def test_windows_arm64_host_uses_emulated_x64_python_runtime_target() -> None:
    assert runtime_target("Windows", "ARM64", "win-amd64") == "win32-x64"


def test_windows_runtime_rejects_unknown_python_platform() -> None:
    with pytest.raises(ValueError, match="unsupported Windows Python runtime platform"):
        runtime_target("Windows", "ARM64", "mingw-x86_64")


def test_executable_path_matches_electron_resource_layout(tmp_path: Path) -> None:
    assert executable_path(tmp_path, "darwin-arm64") == (
        tmp_path / "darwin-arm64" / "ancestryllm-sidecar" / "ancestryllm-sidecar"
    )
    assert executable_path(tmp_path, "win32-x64") == (
        tmp_path / "win32-x64" / "ancestryllm-sidecar" / "ancestryllm-sidecar.exe"
    )
    assert executable_path(tmp_path, "win32-arm64") == (
        tmp_path / "win32-arm64" / "ancestryllm-sidecar" / "ancestryllm-sidecar.exe"
    )


def test_payload_manifest_is_deterministic_and_covers_the_complete_payload(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "darwin-arm64"
    payload_root = target_root / "ancestryllm-sidecar"
    payload_root.mkdir(parents=True)
    executable = payload_root / "ancestryllm-sidecar"
    executable.write_bytes(b"native executable")
    library = payload_root / "z-library.bin"
    library.write_bytes(b"bundled library")

    manifest_path = write_payload_manifest(tmp_path, "darwin-arm64", "0.5.0")
    first = manifest_path.read_bytes()
    write_payload_manifest(tmp_path, "darwin-arm64", "0.5.0")

    assert manifest_path == target_root / MANIFEST_NAME
    assert manifest_path.read_bytes() == first
    manifest = json.loads(first)
    assert manifest == {
        "app_build": "0.5.0",
        "files": [
            {
                "bytes": 17,
                "path": "ancestryllm-sidecar/ancestryllm-sidecar",
                "sha256": "f45dbb0e6ec970ff0a898a36fe2955fbf66c2b6f3b32d4f1fc49d18bcd22c85b",
                "type": "file",
            },
            {
                "bytes": 15,
                "path": "ancestryllm-sidecar/z-library.bin",
                "sha256": "a2d8f4491400b76bc39cebfb69134d2b15164a0245ab62136267e690b838ae50",
                "type": "file",
            },
        ],
        "schema": MANIFEST_SCHEMA,
        "target": "darwin-arm64",
    }
    assert first.endswith(b"\n")


def test_payload_manifest_records_safe_relative_symlinks(tmp_path: Path) -> None:
    payload_root = tmp_path / "linux-x64" / "ancestryllm-sidecar"
    payload_root.mkdir(parents=True)
    (payload_root / "library.so.1").write_bytes(b"library")
    (payload_root / "library.so").symlink_to("library.so.1")

    manifest_path = write_payload_manifest(tmp_path, "linux-x64", "0.5.0")
    manifest = json.loads(manifest_path.read_bytes())

    assert manifest["files"][0] == {
        "path": "ancestryllm-sidecar/library.so",
        "target": "library.so.1",
        "type": "symlink",
    }


def test_payload_manifest_rejects_symlinks_that_escape_the_target(tmp_path: Path) -> None:
    payload_root = tmp_path / "linux-x64" / "ancestryllm-sidecar"
    payload_root.mkdir(parents=True)
    (payload_root / "escape").symlink_to("../../../outside")

    with pytest.raises(RuntimeError, match="escapes the sidecar target"):
        write_payload_manifest(tmp_path, "linux-x64", "0.5.0")
