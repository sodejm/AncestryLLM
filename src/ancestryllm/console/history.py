"""Owner-only prompt_toolkit history with sensitive-command exclusion."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit.history import History


class SecureHistory(History):
    """Append-only JSON history that refuses symlinks and sensitive lines."""

    def __init__(
        self,
        path: Path,
        *,
        is_sensitive: Callable[[str], bool],
        limit: int = 1_000,
    ) -> None:
        self.path = path
        self.is_sensitive = is_sensitive
        self.limit = limit
        self.persistent = self._prepare()
        super().__init__()

    def _prepare(self) -> bool:
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            if self.path.is_symlink():
                return False
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
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
        if not self.is_sensitive(string):
            super().append_string(string)

    def load_history_strings(self) -> Iterable[str]:
        if not self.persistent:
            return ()
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return ()
            with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as handle:
                descriptor = None
                lines = handle.readlines()
        except OSError:
            return ()
        finally:
            if descriptor is not None:
                os.close(descriptor)
        loaded: list[str] = []
        for line in lines[-self.limit :]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, str) and not self.is_sensitive(value):
                loaded.append(value)
        return reversed(loaded)

    def store_string(self, string: str) -> None:
        if not self.persistent or self.is_sensitive(string):
            return
        descriptor: int | None = None
        try:
            flags = os.O_APPEND | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                self.persistent = False
                return
            payload = (json.dumps(string, ensure_ascii=False) + "\n").encode("utf-8")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("history write did not make progress")
                remaining = remaining[written:]
            os.fchmod(descriptor, 0o600)
        except OSError:
            self.persistent = False
        finally:
            if descriptor is not None:
                os.close(descriptor)
