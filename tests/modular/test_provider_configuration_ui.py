"""Exercise the renderer-safe provider, endpoint, and consent boundary."""

from __future__ import annotations

import ssl
from collections import deque
from typing import TYPE_CHECKING

import pytest

import ancestryllm.llm.endpoint_validation as endpoint_validation
from ancestryllm.core.errors import AncestryError, SecurityPolicyError
from ancestryllm.llm.contracts import DataClass, GenerationRequest
from ancestryllm.llm.endpoint_validation import (
    EndpointProbeRequest,
    EndpointProbeResponse,
    EndpointValidationService,
)
from ancestryllm.llm.profiles import ProviderProfileService
from ancestryllm.llm.provider_configuration import ProviderConfigurationService

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext


def _endpoint_validator(
    remote_address: dict[str, str] | None = None,
) -> EndpointValidationService:
    selected = remote_address or {"value": "8.8.8.8"}
    return EndpointValidationService(
        resolver=lambda hostname, _port: (
            ("127.0.0.1",) if hostname in {"127.0.0.1", "localhost"} else (selected["value"],)
        ),
        probe=lambda request: EndpointProbeResponse(
            status_code=200,
            peer_address=request.pinned_address,
        ),
    )


def _configuration_service(
    app_context: AppContext,
    validator: EndpointValidationService | None = None,
) -> tuple[ProviderConfigurationService, ProviderProfileService, EndpointValidationService]:
    selected = validator or _endpoint_validator()
    profiles = ProviderProfileService(app_context.database, endpoint_validator=selected)
    return ProviderConfigurationService(profiles, selected), profiles, selected


def test_provider_configuration_uses_safe_summaries_and_optimistic_revisions(
    app_context: AppContext,
) -> None:
    service, _, validator = _configuration_service(app_context)
    initial = service.snapshot()

    assert initial.schema_version == 1
    assert initial.profiles == ()
    assert initial.consents == ()
    assert len(initial.revision) == 64

    updated = service.create_profile(
        expected_revision=initial.revision,
        name="private-local",
        provider_id="ollama",
        model="fictional-model",
        endpoint="http://127.0.0.1:11434",
        endpoint_identity_sha256=validator.validate(
            "ollama", "http://127.0.0.1:11434"
        ).destination_digest,
    )

    assert updated.revision != initial.revision
    assert updated.profiles[0].name == "private-local"
    assert updated.profiles[0].provider_id == "ollama"
    assert updated.profiles[0].model == "fictional-model"
    assert updated.profiles[0].endpoint == "http://127.0.0.1:11434"
    assert updated.profiles[0].secret_reference is None
    assert updated.profiles[0].enabled is True

    with pytest.raises(AncestryError, match="changed") as conflict:
        service.create_profile(
            expected_revision=initial.revision,
            name="stale-write",
            provider_id="ollama",
            model="fictional-model",
            endpoint="http://127.0.0.1:11434",
            endpoint_identity_sha256="0" * 64,
        )
    assert conflict.value.code == "PROVIDER_CONFIGURATION_CONFLICT"
    assert [profile.name for profile in service.snapshot().profiles] == ["private-local"]


def test_consent_preview_warns_before_atomic_save_and_revoke(
    app_context: AppContext,
) -> None:
    service, _, validator = _configuration_service(app_context)
    with_profile = service.create_profile(
        expected_revision=service.snapshot().revision,
        name="fictional-cloud",
        provider_id="openai",
        model="fictional-model",
        endpoint="https://api.openai.com/v1",
        endpoint_identity_sha256=validator.validate(
            "openai", "https://api.openai.com/v1"
        ).destination_digest,
    )

    preview = service.preview_consent(
        provider_profile_name="fictional-cloud",
        modules=("gedcom",),
        purposes=("identity-comparison",),
        data_classes=(DataClass.LIVING_PERSON, DataClass.FREE_TEXT_NOTE),
        models=("fictional-model",),
        max_cost_usd=0.5,
        retain_payloads=True,
    )

    assert preview.provider_id == "openai"
    assert preview.provider_profile_name == "fictional-cloud"
    assert preview.modules == ("gedcom",)
    assert preview.purposes == ("identity-comparison",)
    assert preview.data_classes == ("free_text_note", "living_person")
    assert preview.models == ("fictional-model",)
    assert preview.max_cost_usd == 0.5
    assert preview.retain_payloads is True
    assert preview.warning_codes == (
        "LIVING_PERSON_DATA_INCLUDED",
        "REMOTE_PROVIDER_SELECTED",
        "REMOTE_RETENTION_ENABLED",
    )

    saved = service.create_consent(
        expected_revision=with_profile.revision,
        name="fictional-consent",
        preview=preview,
    )
    assert saved.consents[0].active is True
    assert saved.consents[0].provider_profile_name == "fictional-cloud"
    assert saved.consents[0].data_classes == ("free_text_note", "living_person")

    revoked = service.revoke_consent(
        expected_revision=saved.revision,
        name="fictional-consent",
    )
    assert revoked.consents[0].active is False


def test_desktop_profile_creation_rejects_unapproved_custom_endpoints(
    app_context: AppContext,
) -> None:
    service, _, _ = _configuration_service(app_context)

    with pytest.raises(SecurityPolicyError) as remote_ollama:
        service.create_profile(
            expected_revision=service.snapshot().revision,
            name="remote-local-runtime",
            provider_id="ollama",
            model="fictional-model",
            endpoint="https://ollama.example.test",
            endpoint_identity_sha256="0" * 64,
        )
    assert remote_ollama.value.code == "ENDPOINT_REJECTED"

    with pytest.raises(SecurityPolicyError) as custom_cloud:
        service.create_profile(
            expected_revision=service.snapshot().revision,
            name="custom-cloud",
            provider_id="openai",
            model="fictional-model",
            endpoint="https://example.test/v1",
            endpoint_identity_sha256="0" * 64,
        )
    assert custom_cloud.value.code == "ENDPOINT_REJECTED"


class _Resolver:
    def __init__(self, *responses: tuple[str, ...]) -> None:
        self.responses = deque(responses)

    def __call__(self, _host: str, _port: int) -> tuple[str, ...]:
        return self.responses.popleft()


def test_endpoint_test_pins_loopback_destination_and_returns_only_a_digest() -> None:
    requests: list[EndpointProbeRequest] = []

    def probe(request: EndpointProbeRequest) -> EndpointProbeResponse:
        requests.append(request)
        return EndpointProbeResponse(status_code=200, peer_address="127.0.0.1")

    result = EndpointValidationService(
        resolver=_Resolver(("127.0.0.1",), ("127.0.0.1",)),
        probe=probe,
    ).validate("ollama", "http://127.0.0.1:11434")

    assert result.schema_version == 1
    assert result.status == "reachable"
    assert result.http_status == 200
    assert result.endpoint_kind == "loopback"
    assert len(result.destination_digest) == 64
    assert "127.0.0.1" not in repr(result)
    assert requests == [
        EndpointProbeRequest(
            scheme="http",
            hostname="127.0.0.1",
            port=11434,
            target="/",
            pinned_address="127.0.0.1",
        )
    ]


@pytest.mark.parametrize(
    ("addresses", "expected_code"),
    [
        (("127.0.0.1", "8.8.8.8"), "ENDPOINT_RESOLUTION_REJECTED"),
        (("169.254.1.1",), "ENDPOINT_RESOLUTION_REJECTED"),
    ],
)
def test_endpoint_test_rejects_unsafe_resolution_before_connecting(
    addresses: tuple[str, ...],
    expected_code: str,
) -> None:
    called = False

    def probe(_request: EndpointProbeRequest) -> EndpointProbeResponse:
        nonlocal called
        called = True
        raise AssertionError("unsafe destinations must not be probed")

    with pytest.raises(SecurityPolicyError) as failure:
        EndpointValidationService(
            resolver=_Resolver(addresses),
            probe=probe,
        ).validate("openai", "https://api.openai.com/v1")

    assert failure.value.code == expected_code
    assert called is False


def test_endpoint_test_rejects_redirects_and_dns_destination_changes() -> None:
    with pytest.raises(SecurityPolicyError) as redirect:
        EndpointValidationService(
            resolver=_Resolver(("8.8.8.8",), ("8.8.8.8",)),
            probe=lambda _request: EndpointProbeResponse(
                status_code=302,
                peer_address="8.8.8.8",
            ),
        ).validate("openai", "https://api.openai.com/v1")
    assert redirect.value.code == "ENDPOINT_REDIRECT_REJECTED"

    with pytest.raises(SecurityPolicyError) as changed:
        EndpointValidationService(
            resolver=_Resolver(("8.8.8.8",), ("1.1.1.1",)),
            probe=lambda _request: EndpointProbeResponse(
                status_code=401,
                peer_address="8.8.8.8",
            ),
        ).validate("openai", "https://api.openai.com/v1")
    assert changed.value.code == "ENDPOINT_DESTINATION_CHANGED"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com/v1;alternate",
        "https://api.openai.com/v1?alternate=true",
        "https://api.openai.com/v1#alternate",
        "https://user@api.openai.com/v1",
    ],
)
def test_endpoint_test_rejects_ambiguous_url_components_before_connecting(
    endpoint: str,
) -> None:
    called = False

    def probe(_request: EndpointProbeRequest) -> EndpointProbeResponse:
        nonlocal called
        called = True
        raise AssertionError("ambiguous endpoints must not be probed")

    with pytest.raises(SecurityPolicyError) as failure:
        EndpointValidationService(
            resolver=_Resolver(("8.8.8.8",), ("8.8.8.8",)),
            probe=probe,
        ).validate("openai", endpoint)

    assert failure.value.code == "ENDPOINT_REJECTED"
    assert called is False


def test_endpoint_test_fails_closed_on_tls_identity_errors() -> None:
    def reject_certificate(_request: EndpointProbeRequest) -> EndpointProbeResponse:
        raise ssl.SSLCertVerificationError(1, "hostname mismatch")

    with pytest.raises(SecurityPolicyError) as failure:
        EndpointValidationService(
            resolver=_Resolver(("8.8.8.8",)),
            probe=reject_certificate,
        ).validate("openai", "https://api.openai.com/v1")

    assert failure.value.code == "ENDPOINT_TEST_FAILED"
    assert failure.value.details == {"error_type": "SSLCertVerificationError"}


def test_direct_endpoint_probe_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[tuple[tuple[str, int], float]] = []
    tls_hosts: list[str] = []

    class FakeSocket:
        def __init__(self) -> None:
            self.request = b""
            self.closed = False

        def settimeout(self, _timeout: float) -> None:
            return

        def getpeername(self) -> tuple[str, int]:
            return ("8.8.8.8", 443)

        def sendall(self, value: bytes) -> None:
            self.request += value

        def recv(self, _size: int) -> bytes:
            return b"HTTP/1.1 401 Unauthorized\r\n"

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        minimum_version: ssl.TLSVersion | None = None

        def wrap_socket(
            self,
            connection: FakeSocket,
            *,
            server_hostname: str,
        ) -> FakeSocket:
            tls_hosts.append(server_hostname)
            return connection

    transport = FakeSocket()
    tls_context = FakeContext()

    def connect(address: tuple[str, int], timeout: float) -> FakeSocket:
        connections.append((address, timeout))
        return transport

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(endpoint_validation.socket, "create_connection", connect)
    monkeypatch.setattr(
        endpoint_validation.ssl,
        "create_default_context",
        lambda: tls_context,
    )

    result = endpoint_validation._direct_probe(
        EndpointProbeRequest(
            scheme="https",
            hostname="api.openai.com",
            port=443,
            target="/v1",
            pinned_address="8.8.8.8",
        )
    )

    assert result == EndpointProbeResponse(status_code=401, peer_address="8.8.8.8")
    assert connections == [(("8.8.8.8", 443), 5.0)]
    assert tls_context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert tls_hosts == ["api.openai.com"]
    assert b"HEAD /v1 HTTP/1.1\r\nHost: api.openai.com\r\n" in transport.request
    assert transport.closed is True


def test_desktop_consent_requires_a_tested_endpoint_binding(
    app_context: AppContext,
) -> None:
    service, profiles, _ = _configuration_service(app_context)
    profiles.create_profile(
        "legacy-cloud",
        "openai",
        "fictional-model",
    )
    preview = service.preview_consent(
        provider_profile_name="legacy-cloud",
        modules=("gedcom",),
        purposes=("identity-comparison",),
        data_classes=(DataClass.DECEASED_PERSON,),
        models=("fictional-model",),
        max_cost_usd=0.5,
        retain_payloads=False,
    )

    with pytest.raises(SecurityPolicyError) as failure:
        service.create_consent(
            expected_revision=service.snapshot().revision,
            name="must-not-be-saved",
            preview=preview,
        )

    assert failure.value.code == "ENDPOINT_TEST_REQUIRED"


def test_tested_endpoint_identity_is_bound_at_save_consent_and_execution(
    app_context: AppContext,
) -> None:
    remote_address = {"value": "8.8.8.8"}
    validator = _endpoint_validator(remote_address)
    service, profiles, _ = _configuration_service(app_context, validator)
    endpoint = "https://api.openai.com/v1"
    tested_identity = validator.validate("openai", endpoint).destination_digest

    with pytest.raises(SecurityPolicyError) as mismatched_save:
        service.create_profile(
            expected_revision=service.snapshot().revision,
            name="mismatched-cloud",
            provider_id="openai",
            model="fictional-model",
            endpoint=endpoint,
            endpoint_identity_sha256="0" * 64,
        )
    assert mismatched_save.value.code == "ENDPOINT_DESTINATION_CHANGED"

    configured = service.create_profile(
        expected_revision=service.snapshot().revision,
        name="bound-cloud",
        provider_id="openai",
        model="fictional-model",
        endpoint=endpoint,
        endpoint_identity_sha256=tested_identity,
    )
    preview = service.preview_consent(
        provider_profile_name="bound-cloud",
        modules=("gedcom",),
        purposes=("identity-comparison",),
        data_classes=(DataClass.DECEASED_PERSON,),
        models=("fictional-model",),
        max_cost_usd=0.5,
        retain_payloads=False,
    )

    remote_address["value"] = "1.1.1.1"
    with pytest.raises(SecurityPolicyError) as changed_before_consent:
        service.create_consent(
            expected_revision=configured.revision,
            name="must-not-be-saved",
            preview=preview,
        )
    assert changed_before_consent.value.code == "ENDPOINT_DESTINATION_CHANGED"

    with pytest.raises(SecurityPolicyError) as changed_before_execution:
        profiles.resolve_request(
            GenerationRequest(
                provider_id="bound-cloud",
                model="",
                module_id="gedcom",
                purpose="identity-comparison",
                messages=(),
            )
        )
    assert changed_before_execution.value.code == "ENDPOINT_DESTINATION_CHANGED"
