"""Settings revision and write-only secret-management contracts."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.application.secret_management import (
    SUPPORTED_SECRET_REFERENCES,
    SecretManagementService,
)
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import AppConfig
from ancestryllm.core.errors import ConfigurationError, StorageError
from ancestryllm.core.secrets import (
    ENVIRONMENT_NAMES,
    KeyringSecretStore,
    MemorySecretStore,
    SecretSourceMode,
)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        config_path=tmp_path / "config" / "config.toml",
        data_dir=tmp_path / "data",
    )


def test_secret_reference_contract_matches_the_keyring_environment_map() -> None:
    assert frozenset(ENVIRONMENT_NAMES) == SUPPORTED_SECRET_REFERENCES


def test_settings_snapshot_exposes_only_reviewed_non_secret_fields(tmp_path: Path) -> None:
    snapshot = SettingsService(_config(tmp_path)).snapshot()

    assert snapshot.schema_version == 1
    assert snapshot.revision == 0
    assert {field.key for field in snapshot.fields} == {
        "providers.default",
        "limits.max_query_rows",
        "limits.max_output_chars",
        "limits.query_timeout_seconds",
        "limits.provider_timeout_seconds",
    }
    assert all(not field.sensitive for field in snapshot.fields)
    assert all(field.label and field.help for field in snapshot.fields)
    assert all(
        field.value is not None and field.default_value is not None for field in snapshot.fields
    )
    rendered = repr(snapshot)
    assert str(tmp_path) not in rendered
    assert "api_key" not in rendered


def test_settings_patch_is_revision_checked_atomic_and_owner_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = SettingsService(config)

    updated = service.patch(
        schema_version=1,
        expected_revision=0,
        changes={
            "providers.default": "ollama",
            "limits.max_query_rows": 250,
            "limits.query_timeout_seconds": 12.5,
        },
    )

    assert updated.revision == 1
    reloaded = AppConfig.load(config.config_path)
    assert reloaded.schema_version == 1
    assert reloaded.revision == 1
    assert reloaded.default_provider == "ollama"
    assert reloaded.max_query_rows == 250
    assert reloaded.query_timeout_seconds == 12.5
    assert config.config_path.stat().st_mode & 0o077 == 0

    with pytest.raises(ConfigurationError) as stale:
        service.patch(
            schema_version=1,
            expected_revision=0,
            changes={"limits.max_query_rows": 251},
        )
    assert stale.value.code == "SETTINGS_REVISION_CONFLICT"
    assert AppConfig.load(config.config_path).max_query_rows == 250


def test_config_save_finishes_fallible_permission_work_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    published = False
    permission_hardened = False
    original_replace = Path.replace
    original_fchmod = os.fchmod

    def tracked_replace(source: Path, target: Path) -> Path:
        nonlocal published
        result = original_replace(source, target)
        published = True
        return result

    def permission_before_publication(descriptor: int, mode: int) -> None:
        nonlocal permission_hardened
        assert not published
        permission_hardened = True
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(Path, "replace", tracked_replace)
    monkeypatch.setattr(os, "fchmod", permission_before_publication)

    config.save()

    assert published is True
    assert permission_hardened is True


@pytest.mark.parametrize(
    ("schema_version", "changes", "expected_code"),
    [
        (2, {"limits.max_query_rows": 10}, "SETTINGS_SCHEMA_UNSUPPORTED"),
        (1, {"storage.data_dir": "/private/path"}, "SETTINGS_FIELD_UNKNOWN"),
        (1, {"openai.api_key": "fictional-secret"}, "SETTINGS_FIELD_UNKNOWN"),
        (1, {"limits.max_query_rows": True}, "SETTINGS_VALUE_INVALID"),
        (1, {"limits.max_query_rows": 0}, "SETTINGS_VALUE_INVALID"),
        (1, {"providers.default": "unreviewed"}, "SETTINGS_VALUE_INVALID"),
    ],
)
def test_settings_patch_rejects_unknown_sensitive_or_invalid_values(
    tmp_path: Path,
    schema_version: int,
    changes: dict[str, object],
    expected_code: str,
) -> None:
    service = SettingsService(_config(tmp_path))

    with pytest.raises(ConfigurationError) as raised:
        service.patch(
            schema_version=schema_version,
            expected_revision=0,
            changes=changes,
        )

    assert raised.value.code == expected_code
    assert "fictional-secret" not in raised.value.render()
    assert not service.config.config_path.exists()


class FakeKeyring:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.delete_calls = 0
        self.read_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.persist_after_delete = False

    def get_password(self, service_name: str, name: str) -> str | None:
        del service_name, name
        if self.read_error is not None:
            raise self.read_error
        return self.value

    def set_password(self, service_name: str, name: str, value: str) -> None:
        del service_name, name
        self.value = value

    def delete_password(self, service_name: str, name: str) -> None:
        del service_name, name
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
        if not self.persist_after_delete:
            self.value = None


@pytest.fixture(autouse=True)
def _clear_secret_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ENVIRONMENT_NAMES.values():
        monkeypatch.delenv(variable, raising=False)


def _keyring_store(monkeypatch: pytest.MonkeyPatch, fake: FakeKeyring) -> KeyringSecretStore:
    monkeypatch.setattr(KeyringSecretStore, "_keyring", staticmethod(lambda: fake))
    return KeyringSecretStore()


def test_keyring_store_does_not_load_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=fictional-dotenv-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _keyring_store(monkeypatch, FakeKeyring()).get("openai.api_key") is None
    assert dotenv.read_text(encoding="utf-8") == "OPENAI_API_KEY=fictional-dotenv-secret\n"


def test_packaged_keyring_only_mode_never_reads_or_protects_environment_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring()
    monkeypatch.setattr(KeyringSecretStore, "_keyring", staticmethod(lambda: fake))
    monkeypatch.setenv("OPENAI_API_KEY", "fictional-environment-secret")
    store = KeyringSecretStore(source_mode=SecretSourceMode.KEYRING_ONLY)

    assert store.get("openai.api_key") is None

    store.set("openai.api_key", "fictional-keyring-secret")
    assert fake.value == "fictional-keyring-secret"


def test_packaged_keyring_only_mode_fails_closed_when_keyring_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring()
    fake.read_error = RuntimeError("PRIVATE-BACKEND-DETAIL")
    monkeypatch.setattr(KeyringSecretStore, "_keyring", staticmethod(lambda: fake))
    monkeypatch.setenv("OPENAI_API_KEY", "fictional-environment-secret")
    store = KeyringSecretStore(source_mode=SecretSourceMode.KEYRING_ONLY)

    with pytest.raises(StorageError) as raised:
        store.get("openai.api_key")

    assert raised.value.code == "KEYRING_READ_FAILED"
    assert "PRIVATE-BACKEND-DETAIL" not in raised.value.render()
    assert "fictional-environment-secret" not in raised.value.render()


def test_keyring_delete_reports_success_only_after_verified_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = FakeKeyring()
    _keyring_store(monkeypatch, missing).delete("openai.api_key")
    assert missing.delete_calls == 0

    present = FakeKeyring("fictional-delete-value")
    _keyring_store(monkeypatch, present).delete("openai.api_key")
    assert present.delete_calls == 1
    assert present.value is None


def test_keyring_delete_fails_closed_when_absence_cannot_be_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring("fictional-delete-value")
    fake.persist_after_delete = True

    with pytest.raises(StorageError) as raised:
        _keyring_store(monkeypatch, fake).delete("openai.api_key")

    assert raised.value.code == "KEYRING_DELETE_UNVERIFIED"
    rendered = raised.value.render()
    assert "fictional-delete-value" not in rendered
    assert "openai.api_key" not in rendered


def test_keyring_delete_error_is_accepted_only_when_absence_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring("fictional-delete-value")

    class DeleteError(RuntimeError):
        pass

    def delete_and_remove(*_arguments: object) -> None:
        fake.value = None
        raise DeleteError("PRIVATE-BACKEND-DETAIL")

    fake.delete_password = delete_and_remove  # type: ignore[method-assign]
    _keyring_store(monkeypatch, fake).delete("openai.api_key")


@pytest.mark.parametrize("error_name", ["KeyringLocked", "KeyringError", "RuntimeError"])
def test_keyring_read_failures_are_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    error_name: str,
) -> None:
    error_type = type(error_name, (RuntimeError,), {})
    fake = FakeKeyring()
    fake.read_error = error_type("PRIVATE-BACKEND-DETAIL")

    with pytest.raises(StorageError) as raised:
        _keyring_store(monkeypatch, fake).delete("openai.api_key")

    assert raised.value.code == "KEYRING_READ_FAILED"
    rendered = raised.value.render()
    assert "PRIVATE-BACKEND-DETAIL" not in rendered
    assert "openai.api_key" not in rendered
    assert error_name not in rendered


def test_keyring_rejects_unknown_and_environment_managed_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKeyring()
    store = _keyring_store(monkeypatch, fake)

    with pytest.raises(StorageError) as unknown:
        store.set("unreviewed.secret", "fictional-secret")
    assert unknown.value.code == "SECRET_REFERENCE_UNKNOWN"

    monkeypatch.setenv("OPENAI_API_KEY", "fictional-environment-secret")
    for operation in (
        lambda: store.set("openai.api_key", "fictional-replacement"),
        lambda: store.delete("openai.api_key"),
    ):
        with pytest.raises(StorageError) as managed:
            operation()
        assert managed.value.code == "SECRET_ENVIRONMENT_MANAGED"


def test_secret_management_is_write_only_and_reports_bounded_status() -> None:
    store = MemorySecretStore({})
    service = SecretManagementService(store)

    assert service.status("openai.api_key").status == "missing"
    assert service.set("openai.api_key", "fictional-secret-value").status == "present"
    assert service.status("openai.api_key").status == "present"
    assert service.delete("openai.api_key").status == "missing"
    assert "fictional-secret-value" not in repr(service.status("openai.api_key"))

    unavailable = SimpleNamespace(
        present=lambda _name: (_ for _ in ()).throw(
            StorageError("KEYRING_READ_FAILED", "Credential storage is unavailable.")
        )
    )
    assert SecretManagementService(unavailable).status("openai.api_key").status == "unavailable"
