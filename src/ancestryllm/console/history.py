"""Owner-only prompt_toolkit history with sensitive-command exclusion."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.history import History

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class SecureHistory(History):
    """Bounded JSON history that refuses symlinks and sensitive lines."""

    def __init__(
        self,
        path: Path,
        *,
        is_sensitive: Callable[[str], bool],
        limit: int = 1_000,
        max_bytes: int = 1_048_576,
        max_entry_bytes: int = 16_384,
    ) -> None:
        if limit <= 0 or max_bytes <= 0 or max_entry_bytes <= 0:
            raise ValueError("history bounds must be positive")
        self.path = path
        self.is_sensitive = is_sensitive
        self.limit = limit
        self.max_bytes = max_bytes
        self.max_entry_bytes = min(max_entry_bytes, max_bytes)
        self.persistent = self._prepare()
        super().__init__()

    @staticmethod
    def _open_flags(flags: int) -> int:
        return flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)

    def _prepare(self) -> bool:
        descriptor: int | None = None
        try:
            if self.path.parent.is_symlink():
                return False
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            if self.path.is_symlink():
                return False
            descriptor = os.open(
                self.path, self._open_flags(os.O_CREAT | os.O_APPEND | os.O_WRONLY), 0o600
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return False
            os.fchmod(descriptor, 0o600)
            return stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def append_string(self, string: str) -> None:
        """Retain a command within the same privacy and size bounds as disk history."""
        if self._encode(string) is not None:
            super().append_string(string)
            self._loaded_strings = [entry for entry, _ in self._retain(self._loaded_strings)]

    def _encode(self, string: str) -> bytes | None:
        if len(string) > self.max_entry_bytes or self.is_sensitive(string):
            return None
        try:
            payload = (json.dumps(string, ensure_ascii=False) + "\n").encode("utf-8")
        except UnicodeError:
            return None
        return payload if len(payload) <= self.max_entry_bytes else None

    def _retain(self, strings: Iterable[str]) -> list[tuple[str, bytes]]:
        retained: list[tuple[str, bytes]] = []
        size = 0
        for string in strings:
            payload = self._encode(string)
            if payload is None:
                continue
            if len(retained) == self.limit or size + len(payload) > self.max_bytes:
                break
            retained.append((string, payload))
            size += len(payload)
        return retained

    def _decode(self, data: bytes) -> Iterable[str]:
        for line in reversed(data.split(b"\n")):
            if len(line) + 1 > self.max_entry_bytes:
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (ValueError, RecursionError):
                continue
            if isinstance(value, str):
                yield value

    def _read(self) -> tuple[list[tuple[str, bytes]], os.stat_result, bool] | None:
        descriptor: int | None = None
        try:
            if self.path.parent.is_symlink() or self.path.is_symlink():
                raise OSError("history path is a symlink")
            descriptor = os.open(self.path, self._open_flags(os.O_RDONLY))
            original = os.fstat(descriptor)
            if not stat.S_ISREG(original.st_mode):
                raise OSError("history path is not a regular file")
            start = max(0, original.st_size - self.max_bytes)
            offset = max(0, start - 1)
            os.lseek(descriptor, offset, os.SEEK_SET)
            remaining = original.st_size - offset
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if start:
                data = data[1:] if data.startswith(b"\n") else data.partition(b"\n")[2]
            entries = self._retain(self._decode(data))
            canonical = b"".join(payload for _, payload in reversed(entries))
            return entries, original, original.st_size > self.max_bytes or data != canonical
        except OSError:
            self.persistent = False
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _replace(self, entries: list[tuple[str, bytes]], original: os.stat_result) -> None:
        descriptor: int | None = None
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            for _, payload in reversed(entries):
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("history write did not make progress")
                    remaining = remaining[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            current = self.path.lstat()
            if (
                self.path.parent.is_symlink()
                or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns)
            ):
                raise OSError("history path changed during compaction")
            temporary.replace(self.path)
            temporary = None
        except OSError:
            self.persistent = False
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def load_history_strings(self) -> Iterable[str]:
        """Load persisted history entries into the interactive console."""
        if not self.persistent:
            return ()
        result = self._read()
        if result is None:
            return ()
        entries, original, needs_compaction = result
        if needs_compaction:
            self._replace(entries, original)
        return [string for string, _ in entries]

    def store_string(self, string: str) -> None:
        """Persist a history entry only after secret redaction."""
        if not self.persistent or self._encode(string) is None:
            return
        result = self._read()
        if result is not None:
            entries, original, _ = result
            retained = self._retain([string, *(entry for entry, _ in entries)])
            self._replace(retained, original)
