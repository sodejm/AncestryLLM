"""Characterize the fail-closed deployment-profile control plane."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest

from ancestryllm.application.deployment import (
    DeploymentObservation,
    DeploymentService,
)
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import AppConfig
from ancestryllm.core.deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
    DeploymentMode,
    DeploymentProfile,
    DeploymentTopology,
)
from ancestryllm.core.errors import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

REMOTE_IDENTITY = "1" * 64
EMBEDDED_CREDENTIALS_ORIGIN = "".join(("https://", "user", ":", "pass", "@remote.example.test"))
OTHER_IDENTITY = "2" * 64


def _config(tmp_path: Path, *, provider: str = "none") -> AppConfig:
    return AppConfig(
        config_path=tmp_path / "config.toml",
        data_dir=tmp_path / "data",
        default_provider=provider,
    )


def _remote_profile(
    *,
    origin: str = "https://remote.example.test",
    identity: str = REMOTE_IDENTITY,
) -> DeploymentProfile:
    return DeploymentProfile.connect_remote(
        endpoint_origin=origin,
        endpoint_identity_sha256=identity,
    )


def _assert_code(exc_info: pytest.ExceptionInfo[ConfigurationError], code: str) -> None:
    assert exc_info.value.code == code
    assert exc_info.value.exit_code == 2


def test_absent_profile_migrates_to_recommended_local_default_and_round_trips(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 1\nrevision = 4\n", encoding="utf-8")

    config = AppConfig.load(path)
    modes = DeploymentService(config).modes()

    assert config.deployment == DeploymentProfile.local()
    assert config.revision == 4
    assert tuple(item.mode for item in modes) == tuple(DeploymentMode)
    local = next(item for item in modes if item.mode is DeploymentMode.LOCAL_DESKTOP)
    assert local.default is True
    assert local.recommended is True
    assert local.advanced is False
    assert "fewest" in local.summary.lower()
    assert all(item.consequences and item.prerequisites for item in modes)

    config.save()
    reloaded = AppConfig.load(path)
    assert reloaded.deployment == DeploymentProfile.local()
    assert reloaded.deployment.schema_version == DEPLOYMENT_SCHEMA_VERSION
    assert "[deployment]" in path.read_text(encoding="utf-8")


def test_mode_copy_explains_storage_backup_support_network_and_prerequisites(
    tmp_path: Path,
) -> None:
    modes = DeploymentService(_config(tmp_path)).modes()

    for mode in modes:
        copy = " ".join((mode.summary, *mode.consequences, *mode.prerequisites)).lower()
        assert "storage" in copy or "stored" in copy
        assert "backup" in copy
        assert "support" in copy
        assert "network" in copy or "loopback" in copy
        assert mode.prerequisites

    local, connect, host = modes
    assert local.default is True and local.recommended is True and local.advanced is False
    assert connect.advanced is True and host.advanced is True
    assert "enrollment" in " ".join(connect.prerequisites).lower()
    assert "bootstrap" in " ".join(host.prerequisites).lower()


def test_remote_profile_is_canonicalized_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
schema_version = 1
revision = 2

[deployment]
schema_version = 1
mode = "connect-remote"
topology = "remote-client"
endpoint_origin = "https://REMOTE.example.test:443/"
endpoint_identity_sha256 = "1111111111111111111111111111111111111111111111111111111111111111"
""".lstrip(),
        encoding="utf-8",
    )

    config = AppConfig.load(path)

    assert config.deployment == _remote_profile()
    config.save()
    assert AppConfig.load(path).deployment == _remote_profile()
    assert "REMOTE.example.test" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "deployment",
    (
        "schema_version = 0\nmode = 'local-desktop'\ntopology = 'local-only'",
        "schema_version = 2\nmode = 'local-desktop'\ntopology = 'local-only'",
        "schema_version = 1\nmode = 'unknown'\ntopology = 'local-only'",
        "schema_version = 1\nmode = 'local-desktop'\ntopology = 'remote-client'",
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            "\nendpoint_origin = 'http://remote.example.test'"
            f"\nendpoint_identity_sha256 = '{REMOTE_IDENTITY}'"
        ),
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            f"\nendpoint_origin = '{EMBEDDED_CREDENTIALS_ORIGIN}'"
            f"\nendpoint_identity_sha256 = '{REMOTE_IDENTITY}'"
        ),
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            "\nendpoint_origin = 'https://remote.example.test/api'"
            f"\nendpoint_identity_sha256 = '{REMOTE_IDENTITY}'"
        ),
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            "\nendpoint_origin = 'https://remote.example.test\\\\path'"
            f"\nendpoint_identity_sha256 = '{REMOTE_IDENTITY}'"
        ),
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            "\nendpoint_origin = 'https://remote.\\texample.test'"
            f"\nendpoint_identity_sha256 = '{REMOTE_IDENTITY}'"
        ),
        (
            "schema_version = 1\nmode = 'connect-remote'\ntopology = 'remote-client'"
            "\nendpoint_origin = 'https://remote.example.test'"
            "\nendpoint_identity_sha256 = 'not-a-digest'"
        ),
        (
            "schema_version = 1\nmode = 'local-desktop'\ntopology = 'local-only'"
            "\nendpoint_origin = 'https://remote.example.test'"
        ),
        "schema_version = 1\nmode = 'local-desktop'\ntopology = 'local-only'\nunknown = true",
    ),
)
def test_malformed_stale_or_downgraded_profiles_fail_closed(
    tmp_path: Path,
    deployment: str,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        f"schema_version = 1\nrevision = 0\n\n[deployment]\n{deployment}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as raised:
        AppConfig.load(path)

    _assert_code(raised, "DEPLOYMENT_PROFILE_INVALID")


def test_environment_cannot_smuggle_a_deployment_mode_or_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANCESTRYLLM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANCESTRYLLM_DEPLOYMENT_MODE", "connect-remote")
    monkeypatch.setenv("ANCESTRYLLM_REMOTE_URL", "https://attacker.example.test")
    monkeypatch.setenv("ANCESTRYLLM_HOST", "0.0.0.0")  # noqa: S104 - hostile fixture

    config = AppConfig.load()

    assert config.deployment == DeploymentProfile.local()


def test_preview_and_switch_are_bound_to_exact_target_and_revision(tmp_path: Path) -> None:
    config = _config(tmp_path, provider="openai")
    service = DeploymentService(config)
    first = _remote_profile()
    substituted = _remote_profile(origin="https://other.example.test")
    preview = service.preview(first, schema_version=1, expected_revision=0)

    with pytest.raises(ConfigurationError) as raised:
        service.switch(
            substituted,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=True,
        )
    _assert_code(raised, "DEPLOYMENT_CONFIRMATION_INVALID")
    assert config.deployment == DeploymentProfile.local()
    assert not config.config_path.exists()

    with pytest.raises(ConfigurationError) as raised:
        service.preview(first, schema_version=1, expected_revision=1)
    _assert_code(raised, "DEPLOYMENT_REVISION_CONFLICT")


def test_remote_and_host_activation_require_their_future_authorities(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = DeploymentService(config)
    remote = _remote_profile()
    preview = service.preview(remote, schema_version=1, expected_revision=0)

    with pytest.raises(ConfigurationError) as raised:
        service.switch(
            remote,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=True,
        )
    _assert_code(raised, "DEPLOYMENT_PROVIDER_CONFLICT")

    config.default_provider = "openai"
    with pytest.raises(ConfigurationError) as raised:
        service.switch(
            remote,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=True,
        )
    _assert_code(raised, "DEPLOYMENT_ENROLLMENT_REQUIRED")

    host = DeploymentProfile.host_remote_server()
    host_preview = service.preview(host, schema_version=1, expected_revision=0)
    with pytest.raises(ConfigurationError) as raised:
        service.switch(
            host,
            schema_version=1,
            expected_revision=0,
            confirmation=host_preview.confirmation,
            unattended=True,
        )
    _assert_code(raised, "DEPLOYMENT_HOST_SETUP_REQUIRED")


def test_unattended_switch_requires_an_explicit_flag_and_exact_confirmation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, provider="openai")
    config.deployment = DeploymentProfile.host_remote_server()
    service = DeploymentService(config)
    local = DeploymentProfile.local()
    preview = service.preview(local, schema_version=1, expected_revision=0)

    with pytest.raises(ConfigurationError) as raised:
        service.switch(
            local,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=False,
        )
    _assert_code(raised, "DEPLOYMENT_EXPLICIT_CONFIRMATION_REQUIRED")

    snapshot = service.switch(
        local,
        schema_version=1,
        expected_revision=0,
        confirmation=preview.confirmation,
        unattended=True,
    )
    assert snapshot.profile == local
    assert snapshot.revision == 1
    assert AppConfig.load(config.config_path).deployment == local


def test_interrupted_persistence_rolls_back_memory_and_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, provider="openai")
    config.deployment = DeploymentProfile.host_remote_server()
    config.save()
    before = config.config_path.read_bytes()
    service = DeploymentService(config)
    local = DeploymentProfile.local()
    preview = service.preview(local, schema_version=1, expected_revision=0)

    def interrupted_save(_candidate: AppConfig, *, expected_revision: int | None = None) -> bool:
        assert expected_revision == 0
        raise KeyboardInterrupt

    monkeypatch.setattr(AppConfig, "save", interrupted_save)
    with pytest.raises(KeyboardInterrupt):
        service.switch(
            local,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=True,
        )

    assert config.deployment == DeploymentProfile.host_remote_server()
    assert config.revision == 0
    assert config.config_path.read_bytes() == before


def test_stale_deployment_switch_cannot_overwrite_a_settings_process(
    tmp_path: Path,
) -> None:
    initial = _config(tmp_path, provider="openai")
    initial.deployment = DeploymentProfile.host_remote_server()
    initial.save()
    stale_deployment = AppConfig.load(initial.config_path)
    current_settings = AppConfig.load(initial.config_path)
    deployment = DeploymentService(stale_deployment)
    local = DeploymentProfile.local()
    preview = deployment.preview(local, schema_version=1, expected_revision=0)

    SettingsService(current_settings).patch(
        schema_version=1,
        expected_revision=0,
        changes={"limits.max_query_rows": 250},
    )

    with pytest.raises(ConfigurationError) as raised:
        deployment.switch(
            local,
            schema_version=1,
            expected_revision=0,
            confirmation=preview.confirmation,
            unattended=True,
        )

    _assert_code(raised, "DEPLOYMENT_REVISION_CONFLICT")
    persisted = AppConfig.load(initial.config_path)
    assert persisted.revision == 1
    assert persisted.max_query_rows == 250
    assert persisted.deployment == DeploymentProfile.host_remote_server()


def test_diagnostics_fail_closed_on_runtime_or_endpoint_substitution(tmp_path: Path) -> None:
    config = _config(tmp_path, provider="openai")
    config.deployment = _remote_profile()
    report = DeploymentService(config).diagnose(
        DeploymentObservation(
            mode=DeploymentMode.CONNECT_REMOTE,
            topology=DeploymentTopology.REMOTE_CLIENT,
            endpoint_origin="https://other.example.test",
            endpoint_identity_sha256=OTHER_IDENTITY,
            authenticated=False,
            listener_hosts=(),
        )
    )

    assert report.status == "failed"
    assert {item.code for item in report.diagnostics} == {
        "DEPLOYMENT_ENDPOINT_MISMATCH",
        "DEPLOYMENT_IDENTITY_MISMATCH",
        "DEPLOYMENT_AUTHENTICATION_MISSING",
    }

    config.deployment = DeploymentProfile.local()
    local_report = DeploymentService(config).diagnose(
        DeploymentObservation(
            mode=DeploymentMode.LOCAL_DESKTOP,
            topology=DeploymentTopology.LOCAL_ONLY,
            listener_hosts=("127.0.0.1", "0.0.0.0"),  # noqa: S104 - hostile fixture
        )
    )
    assert local_report.status == "failed"
    assert [item.code for item in local_report.diagnostics] == [
        "DEPLOYMENT_LISTENER_SCOPE_MISMATCH"
    ]


def test_host_diagnostic_rejects_provider_none(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.deployment = DeploymentProfile.host_remote_server()

    report = DeploymentService(config).diagnose(
        DeploymentObservation(
            mode=DeploymentMode.HOST_REMOTE_SERVER,
            topology=DeploymentTopology.REMOTE_HOST,
        )
    )

    assert report.status == "failed"
    assert [item.code for item in report.diagnostics] == ["DEPLOYMENT_PROVIDER_CONFLICT"]


def test_listener_scope_rejects_loopback_lookalike_hostnames(tmp_path: Path) -> None:
    report = DeploymentService(_config(tmp_path)).diagnose(
        DeploymentObservation(
            mode=DeploymentMode.LOCAL_DESKTOP,
            topology=DeploymentTopology.LOCAL_ONLY,
            listener_hosts=("127.attacker.example",),
        )
    )

    assert report.status == "failed"
    assert report.diagnostics[0].code == "DEPLOYMENT_LISTENER_SCOPE_MISMATCH"


@pytest.mark.parametrize("listener_host", ["[127.0.0.1]]", "[[::1]"])
def test_listener_scope_rejects_malformed_bracketed_hosts(
    tmp_path: Path,
    listener_host: str,
) -> None:
    report = DeploymentService(_config(tmp_path)).diagnose(
        DeploymentObservation(
            mode=DeploymentMode.LOCAL_DESKTOP,
            topology=DeploymentTopology.LOCAL_ONLY,
            listener_hosts=(listener_host,),
        )
    )

    assert report.status == "failed"
    assert report.diagnostics[0].code == "DEPLOYMENT_LISTENER_SCOPE_MISMATCH"


def test_backup_and_support_metadata_are_redacted_and_deterministic(tmp_path: Path) -> None:
    config = _config(tmp_path, provider="openai")
    config.deployment = _remote_profile()
    service = DeploymentService(config)

    for purpose in ("backup", "support"):
        metadata = service.metadata(purpose)
        encoded = json.dumps(asdict(metadata), sort_keys=True)
        assert metadata.purpose == purpose
        assert metadata.endpoint_identity_sha256 == REMOTE_IDENTITY
        assert "remote.example.test" not in encoded
        assert str(tmp_path) not in encoded
        assert "openai" not in encoded
        assert "secret" not in encoded.lower()
        assert "token" not in encoded.lower()


def test_metadata_rejects_unknown_purpose(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as raised:
        DeploymentService(_config(tmp_path)).metadata("telemetry")
    _assert_code(raised, "DEPLOYMENT_METADATA_PURPOSE_INVALID")
