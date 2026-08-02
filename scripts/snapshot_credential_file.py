#!/usr/bin/python3
"""Create a private credential snapshot from one securely opened descriptor."""

from __future__ import annotations

import argparse
import errno
import os
import stat
import sys
from pathlib import Path
from typing import Optional, Sequence


class SnapshotError(RuntimeError):
    """Raised when a credential cannot be snapshotted safely."""


def validate_source_metadata(
    metadata: os.stat_result,
    source_path: Path,
    *,
    current_uid: Optional[int] = None,
) -> None:
    """Validate security properties on metadata from the open source descriptor."""

    effective_uid = os.geteuid() if current_uid is None else current_uid
    if not stat.S_ISREG(metadata.st_mode):
        raise SnapshotError(f"Credential source must be a regular file: {source_path}")
    if metadata.st_size <= 0:
        raise SnapshotError(f"Credential source must not be empty: {source_path}")
    if metadata.st_uid not in (0, effective_uid):
        raise SnapshotError(f"Credential source has an untrusted owner: {source_path}")

    permission_mode = stat.S_IMODE(metadata.st_mode)
    if permission_mode not in (0o400, 0o600):
        raise SnapshotError(
            "Credential source permissions must be 0400 or 0600: "
            f"{source_path} has {permission_mode:04o}"
        )


def _canonical_leaf_path(path: Path, description: str) -> Path:
    absolute_path = os.path.abspath(os.fspath(path))
    leaf_name = os.path.basename(absolute_path)
    if not leaf_name:
        raise SnapshotError(f"{description} must name a file")

    canonical_parent = os.path.realpath(os.path.dirname(absolute_path))
    return Path(canonical_parent) / leaf_name


def _is_within(path: Path, directory: Path) -> bool:
    try:
        return os.path.commonpath((os.fspath(path), os.fspath(directory))) == os.fspath(directory)
    except ValueError:
        return False


def _directory_open_flags() -> int:
    required_flags = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    missing = [name for name in required_flags if not hasattr(os, name)]
    if missing:
        raise SnapshotError(
            "Secure credential snapshots are unsupported because Python is missing: "
            + ", ".join(missing)
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_descriptor(directory: Path) -> int:
    flags = _directory_open_flags()
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in directory.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _source_open_flags() -> int:
    required_flags = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
    missing = [name for name in required_flags if not hasattr(os, name)]
    if missing:
        raise SnapshotError(
            "Secure credential snapshots are unsupported because Python is missing: "
            + ", ".join(missing)
        )
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def _destination_open_flags() -> int:
    required_flags = ("O_CLOEXEC", "O_NOFOLLOW")
    missing = [name for name in required_flags if not hasattr(os, name)]
    if missing:
        raise SnapshotError(
            "Secure credential snapshots are unsupported because Python is missing: "
            + ", ".join(missing)
        )
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def _stable_source_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_destination_directory(metadata: os.stat_result, path: Path) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise SnapshotError(f"Snapshot destination parent is not a directory: {path}")
    if metadata.st_uid != os.geteuid():
        raise SnapshotError(f"Snapshot destination directory has an untrusted owner: {path}")
    permission_mode = stat.S_IMODE(metadata.st_mode)
    if permission_mode != 0o700:
        raise SnapshotError(
            "Snapshot destination directory permissions must be 0700: "
            f"{path} has {permission_mode:04o}"
        )


def _copy_descriptor(source_descriptor: int, destination_descriptor: int) -> int:
    copied_byte_count = 0
    while True:
        chunk = os.read(source_descriptor, 1024 * 1024)
        if not chunk:
            break
        copied_byte_count += len(chunk)
        remaining = memoryview(chunk)
        while remaining:
            written = os.write(destination_descriptor, remaining)
            if written <= 0:
                raise SnapshotError("Could not finish writing the credential snapshot")
            remaining = remaining[written:]
    return copied_byte_count


def snapshot_credential_file(
    source_path: Path,
    destination_path: Path,
    repository_root: Path,
) -> None:
    """Copy one credential from a securely bound source descriptor."""

    canonical_source = _canonical_leaf_path(source_path, "Credential source")
    canonical_repository = Path(os.path.realpath(os.fspath(repository_root)))
    if _is_within(canonical_source, canonical_repository):
        raise SnapshotError("Credential source files must be stored outside the repository")

    canonical_destination = _canonical_leaf_path(destination_path, "Snapshot destination")
    if canonical_source == canonical_destination:
        raise SnapshotError("Credential source and snapshot destination must differ")

    source_parent_descriptor: Optional[int] = None
    source_descriptor: Optional[int] = None
    destination_parent_descriptor: Optional[int] = None
    destination_descriptor: Optional[int] = None
    destination_created = False
    snapshot_complete = False

    try:
        source_parent_descriptor = _open_directory_descriptor(canonical_source.parent)
        try:
            source_descriptor = os.open(
                canonical_source.name,
                _source_open_flags(),
                dir_fd=source_parent_descriptor,
            )
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise SnapshotError(
                    f"Credential source must not be a symbolic link: {source_path}"
                ) from error
            raise SnapshotError(
                f"Could not open credential source securely: {source_path}: {error.strerror}"
            ) from error

        initial_metadata = os.fstat(source_descriptor)
        validate_source_metadata(initial_metadata, canonical_source)

        destination_parent_descriptor = _open_directory_descriptor(canonical_destination.parent)
        _validate_destination_directory(
            os.fstat(destination_parent_descriptor), canonical_destination.parent
        )
        try:
            destination_descriptor = os.open(
                canonical_destination.name,
                _destination_open_flags(),
                0o600,
                dir_fd=destination_parent_descriptor,
            )
            destination_created = True
        except OSError as error:
            raise SnapshotError(
                "Could not create a new credential snapshot securely: "
                f"{destination_path}: {error.strerror}"
            ) from error

        os.fchmod(destination_descriptor, 0o600)
        copied_byte_count = _copy_descriptor(source_descriptor, destination_descriptor)
        final_metadata = os.fstat(source_descriptor)
        if _stable_source_fingerprint(initial_metadata) != _stable_source_fingerprint(
            final_metadata
        ):
            raise SnapshotError("Credential source changed while it was being copied")
        if copied_byte_count != initial_metadata.st_size:
            raise SnapshotError("Credential source size changed while it was being copied")
        os.fsync(destination_descriptor)
        snapshot_complete = True
    except OSError as error:
        raise SnapshotError(f"Credential snapshot failed securely: {error.strerror}") from error
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if destination_created and not snapshot_complete:
            try:
                if destination_parent_descriptor is not None:
                    os.unlink(
                        canonical_destination.name,
                        dir_fd=destination_parent_descriptor,
                    )
            except FileNotFoundError:
                pass
        if destination_parent_descriptor is not None:
            os.close(destination_parent_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
        if source_parent_descriptor is not None:
            os.close(source_parent_descriptor)


def _parse_args(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one race-safe private credential snapshot."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    return parser.parse_args(arguments)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    options = _parse_args(arguments)
    try:
        snapshot_credential_file(
            options.source,
            options.destination,
            options.repository_root,
        )
    except SnapshotError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
