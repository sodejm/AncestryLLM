from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_HELPER = REPOSITORY_ROOT / "scripts" / "snapshot_credential_file.py"


def _load_snapshot_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("snapshot_credential_file", SNAPSHOT_HELPER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_file(path: Path, contents: bytes = b"fictional credential\n") -> Path:
    path.write_bytes(contents)
    path.chmod(0o600)
    return path


def _private_destination_directory(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def _run_snapshot(source: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/usr/bin/python3",
            str(SNAPSHOT_HELPER),
            "--source",
            str(source),
            "--destination",
            str(destination),
            "--repository-root",
            str(REPOSITORY_ROOT),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_snapshot_copies_from_the_open_descriptor_when_path_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_snapshot_helper()
    source = _private_file(tmp_path / "credential", b"original bytes\n")
    replacement = _private_file(tmp_path / "replacement", b"replacement bytes\n")
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"

    original_open = module.os.open
    source_open_count = 0

    def racing_open(
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal source_open_count
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if (
            path == source.name
            and dir_fd is not None
            and flags & os.O_NOFOLLOW
            and not flags & os.O_DIRECTORY
        ):
            source_open_count += 1
            if source_open_count == 1:
                os.replace(replacement, source)
        return descriptor

    monkeypatch.setattr(module.os, "open", racing_open)

    module.snapshot_credential_file(source, destination, REPOSITORY_ROOT)

    assert source_open_count == 1
    assert source.read_bytes() == b"replacement bytes\n"
    assert destination.read_bytes() == b"original bytes\n"


def test_snapshot_aborts_if_the_open_source_changes_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_snapshot_helper()
    source = _private_file(tmp_path / "credential", b"original bytes\n")
    original_mtime_ns = source.stat().st_mtime_ns
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"
    original_copy = module._copy_descriptor

    def mutating_copy(source_descriptor: int, destination_descriptor: int) -> int:
        source.write_bytes(b"modified bytes\n")
        source.chmod(0o600)
        os.utime(
            source,
            ns=(source.stat().st_atime_ns, original_mtime_ns + 1_000_000_000),
        )
        return original_copy(source_descriptor, destination_descriptor)

    monkeypatch.setattr(module, "_copy_descriptor", mutating_copy)

    with pytest.raises(module.SnapshotError, match="changed while it was being copied"):
        module.snapshot_credential_file(source, destination, REPOSITORY_ROOT)

    assert not destination.exists()


def test_snapshot_rejects_a_symbolic_link_source(tmp_path: Path) -> None:
    target = _private_file(tmp_path / "target")
    source = tmp_path / "credential-link"
    source.symlink_to(target)
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"

    result = _run_snapshot(source, destination)

    assert result.returncode != 0
    assert "must not be a symbolic link" in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize("permission_mode", [0o644, 0o660, 0o700])
def test_snapshot_rejects_credential_permissions_that_are_not_private(
    tmp_path: Path, permission_mode: int
) -> None:
    source = _private_file(tmp_path / "credential")
    source.chmod(permission_mode)
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"

    result = _run_snapshot(source, destination)

    assert result.returncode != 0
    assert "permissions must be 0400 or 0600" in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize("source_kind", ["empty", "directory", "fifo"])
def test_snapshot_rejects_invalid_source_type_or_size(tmp_path: Path, source_kind: str) -> None:
    source = tmp_path / "credential"
    if source_kind == "empty":
        _private_file(source, b"")
        expected_error = "must not be empty"
    elif source_kind == "directory":
        source.mkdir()
        source.chmod(0o700)
        expected_error = "must be a regular file"
    else:
        os.mkfifo(source, 0o600)
        expected_error = "must be a regular file"
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"

    result = _run_snapshot(source, destination)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert not destination.exists()


def test_snapshot_rejects_an_untrusted_source_owner() -> None:
    module = _load_snapshot_helper()
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_size=1,
        st_uid=os.geteuid() + 1,
    )

    with pytest.raises(module.SnapshotError, match="has an untrusted owner"):
        module.validate_source_metadata(
            metadata,
            Path("/private/credential"),
            current_uid=os.geteuid(),
        )


def test_snapshot_creates_one_private_copy(tmp_path: Path) -> None:
    source = _private_file(tmp_path / "credential", b"bound credential bytes\n")
    destination_directory = _private_destination_directory(tmp_path / "snapshots")
    destination = destination_directory / "credential.snapshot"

    result = _run_snapshot(source, destination)

    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == b"bound credential bytes\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
