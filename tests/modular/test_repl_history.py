"""Regression coverage for the prompt_toolkit REPL's secure history boundary."""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

import pytest

from ancestryllm.console.history import SecureHistory
from ancestryllm.console.security import history_is_sensitive

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def _history(
    path: Path,
    *,
    limit: int = 1_000,
    max_bytes: int = 1_048_576,
    max_entry_bytes: int = 16_384,
) -> SecureHistory:
    return SecureHistory(
        path,
        is_sensitive=history_is_sensitive,
        limit=limit,
        max_bytes=max_bytes,
        max_entry_bytes=max_entry_bytes,
    )


def test_secure_history_uses_owner_only_file_and_directory_permissions(tmp_path: Path) -> None:
    history_path = tmp_path / "fictional-state" / "repl_history"

    history = _history(history_path)
    history.store_string("modules")

    assert history.persistent is True
    assert stat.S_IMODE(history_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600
    assert list(history.load_history_strings()) == ["modules"]


def test_secure_history_refuses_a_symlink_path(tmp_path: Path) -> None:
    target = tmp_path / "fictional-target"
    target.write_text('"modules"\n', encoding="utf-8")
    history_path = tmp_path / "state" / "repl_history"
    history_path.parent.mkdir()
    history_path.symlink_to(target)

    history = _history(history_path)
    history.store_string("help")

    assert history.persistent is False
    assert target.read_text(encoding="utf-8") == '"modules"\n'


def test_secure_history_recovers_from_malformed_records_and_obeys_load_limit(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history_path.parent.mkdir()
    history_path.write_text(
        "not-json\n"
        + json.dumps("modules")
        + "\n"
        + json.dumps({"unexpected": "record"})
        + "\n"
        + json.dumps("help")
        + "\n"
        + json.dumps("exit")
        + "\n",
        encoding="utf-8",
    )
    history_path.chmod(0o600)

    history = _history(history_path, limit=3)

    assert history.persistent is True
    assert list(history.load_history_strings()) == ["exit", "help", "modules"]


def test_secure_history_excludes_secret_sensitive_and_multiline_commands(tmp_path: Path) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path)
    fictional_secret = "fictional-secret-value"
    excluded = (
        f"secrets set openai.api_key {fictional_secret}",
        f"set api-key {fictional_secret}",
        "gedcom merge fictional.ged --output result.ged --root-person Ada",
        "help\nmodules",
    )

    for command in excluded:
        history.append_string(command)
    history.append_string("modules")

    assert list(history.load_history_strings()) == ["modules"]
    assert fictional_secret not in history_path.read_text(encoding="utf-8")


def test_secure_history_reads_only_a_bounded_tail_and_compacts_a_large_legacy_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history_path.parent.mkdir()
    with history_path.open("wb") as handle:
        handle.seek(128 * 1_024 * 1_024)
        handle.write(b'\n"help"\n"modules"\n')
    history = _history(history_path, max_bytes=256, max_entry_bytes=64)
    real_read = os.read
    requested: list[int] = []
    received: list[int] = []

    def observed_read(descriptor: int, count: int) -> bytes:
        requested.append(count)
        data = real_read(descriptor, count)
        received.append(len(data))
        return data

    monkeypatch.setattr(os, "read", observed_read)

    assert list(history.load_history_strings()) == ["modules", "help"]
    assert requested and max(requested) <= 257
    assert sum(received) <= 257
    assert history_path.read_bytes() == b'"help"\n"modules"\n'
    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600


def test_secure_history_skips_long_malformed_and_sensitive_legacy_records(tmp_path: Path) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history_path.parent.mkdir()
    history_path.write_bytes(
        b'"old"\n'
        + b"x" * 4_096
        + b"\n"
        + json.dumps("x" * 100).encode()
        + b'\n"help"\nnot-json\n{"unexpected": "record"}\n'
        + json.dumps("secrets set openai.api_key fictional-secret-value").encode()
        + b'\n\xff\n"modules"\n'
    )
    history = _history(history_path, max_bytes=512, max_entry_bytes=64)

    assert list(history.load_history_strings()) == ["modules", "help"]
    assert history_path.read_bytes() == b'"help"\n"modules"\n'


def test_secure_history_skips_records_that_exceed_json_parser_limits(tmp_path: Path) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history_path.parent.mkdir()
    history_path.write_bytes(b"9" * 5_000 + b'\n"help"\n')
    history = _history(history_path)

    assert list(history.load_history_strings()) == ["help"]
    assert history_path.read_bytes() == b'"help"\n'


@pytest.mark.parametrize(("limit", "max_bytes", "retained"), [(3, 100, 3), (10, 40, 2)])
def test_secure_history_bounds_repeated_writes_and_in_memory_entries(
    tmp_path: Path, limit: int, max_bytes: int, retained: int
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path, limit=limit, max_bytes=max_bytes, max_entry_bytes=32)
    commands = [f"help topic-{index:03}" for index in range(50)]

    for index, command in enumerate(commands):
        history.append_string(command)
        assert history_path.stat().st_size <= max_bytes
        assert len(history.get_strings()) <= limit
        assert len(history_path.read_text(encoding="utf-8").splitlines()) <= limit
        assert history.get_strings()[-1] == commands[index]

    assert history.get_strings() == commands[-retained:]
    assert list(history.load_history_strings()) == list(reversed(commands[-retained:]))
    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600


def test_secure_history_bounds_serialized_entry_bytes(tmp_path: Path) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path, max_bytes=64, max_entry_bytes=32)

    for rejected in ("x" * 33, "é" * 16, "\x00" * 8, "\ud800"):
        history.append_string(rejected)
    history.append_string("help")

    assert history.get_strings() == ["help"]
    assert list(history.load_history_strings()) == ["help"]


@pytest.mark.parametrize("failure", ["write", "sync", "replace"])
def test_secure_history_interrupted_compaction_preserves_the_original(
    tmp_path: Path, monkeypatch: MonkeyPatch, failure: str
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path, limit=2)
    history.store_string("modules")
    original = history_path.read_bytes()
    real_write = os.write
    wrote_fragment = False

    def interrupted_write(descriptor: int, data: bytes | memoryview) -> int:
        nonlocal wrote_fragment
        if not wrote_fragment:
            wrote_fragment = True
            return real_write(descriptor, data[:2])
        raise OSError("fictional interrupted compaction")

    def interrupted_operation(*args: object, **kwargs: object) -> None:
        raise OSError("fictional interrupted compaction")

    monkeypatch.setattr(
        os,
        {"write": "write", "sync": "fsync", "replace": "replace"}[failure],
        interrupted_write if failure == "write" else interrupted_operation,
    )
    history.store_string("help")

    assert history.persistent is False
    assert history_path.read_bytes() == original
    assert list(history_path.parent.iterdir()) == [history_path]


def test_secure_history_cleanup_failure_keeps_history_available_in_memory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path)
    history.append_string("modules")
    original = history_path.read_bytes()

    def denied_operation(*args: object, **kwargs: object) -> None:
        raise PermissionError("fictional unavailable history directory")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", denied_operation)
        patch.setattr(type(history_path), "unlink", denied_operation)
        history.append_string("help")

    assert history.persistent is False
    assert history.get_strings() == ["modules", "help"]
    assert history_path.read_bytes() == original
    temporary_files = list(history_path.parent.glob(".repl_history.*.tmp"))
    assert len(temporary_files) == 1
    assert stat.S_IMODE(temporary_files[0].stat().st_mode) == 0o600
    assert temporary_files[0].stat().st_size <= history.max_bytes


def test_secure_history_refuses_a_symlink_replacement_during_compaction(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path)
    history.store_string("modules")
    target = tmp_path / "fictional-target"
    target.write_text("fictional content", encoding="utf-8")
    real_fsync = os.fsync

    def replace_history_with_symlink(descriptor: int) -> None:
        real_fsync(descriptor)
        history_path.unlink()
        history_path.symlink_to(target)

    monkeypatch.setattr(os, "fsync", replace_history_with_symlink)
    history.store_string("help")

    assert history.persistent is False
    assert history_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "fictional content"
    assert list(history_path.parent.iterdir()) == [history_path]


def test_secure_history_refuses_a_symlink_replacement_before_loading(tmp_path: Path) -> None:
    history_path = tmp_path / "state" / "repl_history"
    history = _history(history_path)
    target = tmp_path / "fictional-target"
    target.write_text('"help"\n', encoding="utf-8")
    history_path.unlink()
    history_path.symlink_to(target)

    assert list(history.load_history_strings()) == []
    assert history.persistent is False
    assert target.read_text(encoding="utf-8") == '"help"\n'


@pytest.mark.parametrize("option", ["limit", "max_bytes", "max_entry_bytes"])
def test_secure_history_rejects_nonpositive_bounds(tmp_path: Path, option: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        _history(tmp_path / "state" / "repl_history", **{option: 0})
