from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.build_sidecar import executable_path, native_target, runtime_target

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
