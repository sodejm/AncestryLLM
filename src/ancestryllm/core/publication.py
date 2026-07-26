"""Alias-safe, rollback-capable publication of related output artifacts."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path


def paths_alias(left: str | Path, right: str | Path) -> bool:
    """Return whether two path spellings designate the same filesystem object."""

    first = Path(left).expanduser()
    second = Path(right).expanduser()
    try:
        if first.resolve(strict=False) == second.resolve(strict=False):
            return True
    except (OSError, RuntimeError):
        pass
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def staging_path(target: Path) -> Path:
    """Reserve a same-directory temporary name for one staged artifact."""

    descriptor, raw_path = tempfile.mkstemp(
        prefix=".ancestry-publish-",
        dir=target.parent,
    )
    os.close(descriptor)
    path = Path(raw_path)
    path.unlink()
    return path


def publish_staged_bundle(
    artifacts: Iterable[tuple[Path, Path]],
    *,
    replace: Callable[[str | Path, str | Path], None],
    validate_after: Callable[[], None] | None = None,
) -> None:
    """Publish staged files as one rollback-capable logical transaction.

    Filesystems cannot atomically rename multiple files, so existing targets are
    retained as same-directory backups until every rename and optional
    post-publication validation succeeds.
    """

    selected = [(source, target) for source, target in artifacts]
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _source, target in selected:
            if target.exists() or target.is_symlink():
                backup = staging_path(target)
                try:
                    os.link(target, backup, follow_symlinks=False)
                except OSError:
                    shutil.copy2(target, backup, follow_symlinks=False)
                backups[target] = backup
        for source, target in selected:
            replace(source, target)
            published.append(target)
        if validate_after is not None:
            validate_after()
    except Exception as publish_error:
        for target in reversed(published):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        rollback_error: OSError | None = None
        for target in reversed(published):
            rollback_backup = backups.get(target)
            if rollback_backup is None:
                continue
            try:
                replace(rollback_backup, target)
            except OSError as exc:
                rollback_error = rollback_error or exc
        for source, _target in selected:
            source.unlink(missing_ok=True)
        if rollback_error is None:
            for backup in backups.values():
                backup.unlink(missing_ok=True)
        if rollback_error is not None:
            raise rollback_error from publish_error
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    finally:
        for source, _target in selected:
            source.unlink(missing_ok=True)


__all__ = ["paths_alias", "publish_staged_bundle", "staging_path"]
