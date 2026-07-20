from __future__ import annotations

import lzma
import stat
from pathlib import Path

from cmd2.history import History

from ancestryllm.console.app import AncestryConsole
from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.secrets import KeyringSecretStore


def _history_commands(console: AncestryConsole) -> list[str]:
    return [item.raw for item in console.history]


def _persisted_history(path: Path) -> History:
    payload = lzma.decompress(path.read_bytes()).decode("utf-8")
    return History.from_json(payload)


def test_repl_secret_entry_uses_hidden_confirmation_and_skips_history(
    app_context: AppContext, monkeypatch, capsys
) -> None:
    entered = iter(("console-secret-value", "console-secret-value"))
    prompts: list[tuple[str, bool]] = []

    def hidden_prompt(message: str, *, is_password: bool) -> str:
        prompts.append((message, is_password))
        return next(entered)

    monkeypatch.setattr("ancestryllm.console.app.prompt", hidden_prompt)
    console = AncestryConsole(app_context)

    console.onecmd_plus_hooks("secrets set openai.api_key")

    assert app_context.secrets.get("openai.api_key") == "console-secret-value"
    assert prompts == [
        ("Secret value for openai.api_key: ", True),
        ("Confirm secret value: ", True),
    ]
    assert _history_commands(console) == []
    output = capsys.readouterr()
    assert "console-secret-value" not in output.out + output.err
    assert "Stored secret reference: openai.api_key" in output.out


def test_repl_secret_entry_writes_directly_to_os_keyring(tmp_path: Path, monkeypatch) -> None:
    writes: list[tuple[str, str, str]] = []

    class RecordingKeyring:
        @staticmethod
        def set_password(service: str, name: str, value: str) -> None:
            writes.append((service, name, value))

    secret_store = KeyringSecretStore()
    monkeypatch.setattr(KeyringSecretStore, "_keyring", staticmethod(lambda: RecordingKeyring))
    monkeypatch.setattr(
        "ancestryllm.console.app.prompt",
        lambda _message, *, is_password: "keyring-only-value",
    )
    config = AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data")
    console = AncestryConsole(AppContext.build(config, secret_store))

    console.onecmd_plus_hooks("secrets set openai.api_key")

    assert writes == [("AncestryLLM", "openai.api_key", "keyring-only-value")]


def test_repl_secret_confirmation_failure_is_redacted_and_not_stored(
    app_context: AppContext, monkeypatch, capsys
) -> None:
    entered = iter(("first-secret-value", "different-secret-value"))
    monkeypatch.setattr(
        "ancestryllm.console.app.prompt", lambda _message, *, is_password: next(entered)
    )
    console = AncestryConsole(app_context)

    console.onecmd_plus_hooks("secrets set anthropic.api_key")

    assert app_context.secrets.get("anthropic.api_key") is None
    assert _history_commands(console) == []
    output = capsys.readouterr()
    assert "first-secret-value" not in output.out + output.err
    assert "different-secret-value" not in output.out + output.err
    assert "SECRET_CONFIRMATION_FAILED" in output.err


def test_history_excludes_secret_commands_rejected_options_and_multiline_text(
    app_context: AppContext,
) -> None:
    console = AncestryConsole(app_context)

    console.onecmd_plus_hooks("set action subtree")
    console.onecmd_plus_hooks("secrets status openai.api_key")
    console.onecmd_plus_hooks("set api-key command-line-secret")
    console.onecmd_plus_hooks('set notes "first line\ncredential material"')

    assert _history_commands(console) == ["set action subtree"]


def test_history_file_permissions_and_corruption_are_recovered(
    app_context: AppContext,
) -> None:
    history_path = app_context.config.data_dir / "console_history"
    history_path.parent.mkdir(parents=True)
    history_path.write_bytes(b"not compressed history")
    history_path.chmod(0o644)

    console = AncestryConsole(app_context)
    console.onecmd_plus_hooks("set action subtree")
    console.onecmd_plus_hooks("set password do-not-persist")
    console._persist_history()

    assert stat.S_IMODE(history_path.stat().st_mode) == 0o600
    persisted = _persisted_history(history_path)
    assert [item.raw for item in persisted] == ["set action subtree"]
    assert b"do-not-persist" not in history_path.read_bytes()


def test_registered_secrets_are_redacted_from_output_errors_and_progress(
    app_context: AppContext, monkeypatch, capsys
) -> None:
    secret = "registered-sensitive-value"
    app_context.secrets.set("openai.api_key", secret)
    console = AncestryConsole(app_context)
    console.active_module = "gedcom"
    console.debug = True

    def rendered_result(_context: AppContext, _tokens: list[str]) -> int:
        print(f"rendered result: {secret}")
        raise RuntimeError(f"provider exception included {secret}")

    monkeypatch.setattr("ancestryllm.console.app.run_tokens", rendered_result)
    console.onecmd_plus_hooks("run quality")
    console.add_alert(msg={"progress": f"processing {secret}"})
    alert = console._alert_queue[-1]

    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "rendered result: [REDACTED]" in output.out
    assert "provider exception included [REDACTED]" in output.err
    assert "Traceback" in output.err
    assert secret not in str(alert.msg)
    assert "[REDACTED]" in str(alert.msg)
