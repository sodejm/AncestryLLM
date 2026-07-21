"""Regression coverage for the prompt_toolkit REPL's secure history boundary."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ancestryllm.console.history import SecureHistory
from ancestryllm.console.security import history_is_sensitive


def _history(path: Path, *, limit: int = 1_000) -> SecureHistory:
    return SecureHistory(path, is_sensitive=history_is_sensitive, limit=limit)


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


def test_secure_history_recovers_from_malformed_records_and_obeys_load_limit(tmp_path: Path) -> None:
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
    os.chmod(history_path, 0o600)

    history = _history(history_path, limit=3)

    assert history.persistent is True
    assert list(history.load_history_strings()) == ["exit", "help"]


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
