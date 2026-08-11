"""Safe deployment-profile previews, transitions, diagnostics, and evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from copy import copy
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal, Protocol, cast

from ancestryllm.core.deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
    DeploymentMode,
    DeploymentProfile,
    DeploymentTopology,
    normalize_endpoint_identity,
    normalize_endpoint_origin,
)
from ancestryllm.core.errors import ConfigurationError

DeploymentEvidencePurpose = Literal["backup", "support"]


class DeploymentConfigPort(Protocol):
    """Reviewed configuration values needed by the deployment control plane."""

    revision: int
    default_provider: str
    deployment: DeploymentProfile

    def save(self, *, expected_revision: int | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class DeploymentModeDescriptor:
    """Renderer-safe installation and first-run copy for one mode."""

    mode: DeploymentMode
    label: str
    summary: str
    consequences: tuple[str, ...]
    prerequisites: tuple[str, ...]
    default: bool = False
    recommended: bool = False
    advanced: bool = True


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """Current profile and optimistic-lock revision."""

    schema_version: int
    revision: int
    profile: DeploymentProfile


@dataclass(frozen=True, slots=True)
class DeploymentPreview:
    """Revision- and target-bound switch preview."""

    schema_version: int
    expected_revision: int
    current: DeploymentProfile
    target: DeploymentProfile
    consequences: tuple[str, ...]
    confirmation: str


@dataclass(frozen=True, slots=True)
class DeploymentObservation:
    """Sanitized runtime facts supplied by a host adapter."""

    mode: DeploymentMode
    topology: DeploymentTopology
    endpoint_origin: str | None = None
    endpoint_identity_sha256: str | None = None
    authenticated: bool | None = None
    listener_hosts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeploymentDiagnostic:
    """One stable, sanitized profile/runtime comparison result."""

    code: str
    status: Literal["passed", "failed"]
    message: str
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class DeploymentDiagnosticReport:
    """Deterministic aggregate profile/runtime diagnostic result."""

    schema_version: int
    revision: int
    status: Literal["passed", "failed"]
    diagnostics: tuple[DeploymentDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DeploymentEvidence:
    """Privacy-minimal metadata for backup manifests and support bundles."""

    schema_version: int
    purpose: DeploymentEvidencePurpose
    deployment_schema_version: int
    config_revision: int
    mode: DeploymentMode
    topology: DeploymentTopology
    endpoint_identity_sha256: str | None


_MODE_DESCRIPTORS = (
    DeploymentModeDescriptor(
        mode=DeploymentMode.LOCAL_DESKTOP,
        label="Local Desktop",
        summary=(
            "Recommended and supported: keeps the fewest moving parts and application storage "
            "on this device."
        ),
        consequences=(
            "Application storage and genealogy data remain on this device.",
            "Uses the native application-service runtime and a loopback-only network boundary.",
            "Backups remain the signed-in operating-system user's responsibility.",
            "Does not enroll with or host a remote server.",
        ),
        prerequisites=("A supported local installation.",),
        default=True,
        recommended=True,
        advanced=False,
    ),
    DeploymentModeDescriptor(
        mode=DeploymentMode.CONNECT_REMOTE,
        label="Connect to Remote",
        summary="Advanced: use an explicitly enrolled HTTPS server instead of local services.",
        consequences=(
            "Application storage remains on the selected remote server.",
            "Requires a network connection to one authenticated HTTPS endpoint and identity.",
            "Never copies or uploads a local family tree as part of the mode switch.",
            "Backups, availability, and support depend on the remote operator.",
        ),
        prerequisites=("Successful authenticated remote enrollment from DEPLOY-12 (#357).",),
    ),
    DeploymentModeDescriptor(
        mode=DeploymentMode.HOST_REMOTE_SERVER,
        label="Host Remote Server",
        summary="Advanced and self-supported: hand off setup to reviewed headless host tooling.",
        consequences=(
            "Application storage and encrypted data volumes remain on the operator-managed host.",
            "Profile selection starts no network service and never widens a listener.",
            "The host operator owns backups, certificates, recovery, availability, and support.",
        ),
        prerequisites=("Completed host-runtime bootstrap from DEPLOY-03/04 (#348/#363).",),
    ),
)
_DESCRIPTORS_BY_MODE = {item.mode: item for item in _MODE_DESCRIPTORS}


def _deployment_error(
    code: str, message: str, remediation: str | None = None
) -> ConfigurationError:
    return ConfigurationError(code, message, remediation, exit_code=2)


def _validated_profile(profile: DeploymentProfile) -> DeploymentProfile:
    if not isinstance(profile, DeploymentProfile):
        raise _deployment_error("DEPLOYMENT_PROFILE_INVALID", "The deployment profile is invalid.")
    return DeploymentProfile.from_mapping(profile.to_mapping())


def _confirmation(profile: DeploymentProfile, expected_revision: int) -> str:
    payload = {
        "expected_revision": expected_revision,
        "profile": profile.to_mapping(),
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"confirm-deployment-{digest}"


def _is_loopback(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized.startswith("[") or normalized.endswith("]"):
        if not (normalized.startswith("[") and normalized.endswith("]")):
            return False
        normalized = normalized[1:-1]
        if "[" in normalized or "]" in normalized:
            return False
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


class DeploymentService:
    """Own explicit profile choices without starting listeners or moving data."""

    def __init__(self, config: DeploymentConfigPort) -> None:
        self.config = config
        self._lock = threading.RLock()

    def modes(self) -> tuple[DeploymentModeDescriptor, ...]:
        return _MODE_DESCRIPTORS

    def snapshot(self) -> DeploymentSnapshot:
        with self._lock:
            return DeploymentSnapshot(
                schema_version=DEPLOYMENT_SCHEMA_VERSION,
                revision=self.config.revision,
                profile=self.config.deployment,
            )

    def _check_version_and_revision(self, schema_version: int, expected_revision: int) -> None:
        if schema_version != DEPLOYMENT_SCHEMA_VERSION:
            raise _deployment_error(
                "DEPLOYMENT_SCHEMA_UNSUPPORTED",
                "The deployment profile schema version is unsupported.",
            )
        if expected_revision != self.config.revision:
            raise _deployment_error(
                "DEPLOYMENT_REVISION_CONFLICT",
                "The deployment profile changed since it was read; reload and retry.",
            )

    def preview(
        self,
        target: DeploymentProfile,
        *,
        schema_version: int,
        expected_revision: int,
    ) -> DeploymentPreview:
        """Describe an exact target without mutating config or runtime state."""

        selected = _validated_profile(target)
        with self._lock:
            self._check_version_and_revision(schema_version, expected_revision)
            return DeploymentPreview(
                schema_version=DEPLOYMENT_SCHEMA_VERSION,
                expected_revision=expected_revision,
                current=self.config.deployment,
                target=selected,
                consequences=_DESCRIPTORS_BY_MODE[selected.mode].consequences,
                confirmation=_confirmation(selected, expected_revision),
            )

    def switch(
        self,
        target: DeploymentProfile,
        *,
        schema_version: int,
        expected_revision: int,
        confirmation: str,
        unattended: bool,
    ) -> DeploymentSnapshot:
        """Persist an authorized local transition; future authorities own remote activation."""

        selected = _validated_profile(target)
        with self._lock:
            self._check_version_and_revision(schema_version, expected_revision)
            if not unattended:
                raise _deployment_error(
                    "DEPLOYMENT_EXPLICIT_CONFIRMATION_REQUIRED",
                    "An unattended profile switch requires its explicit flag.",
                )
            expected_confirmation = _confirmation(selected, expected_revision)
            if not isinstance(confirmation, str) or not hmac.compare_digest(
                confirmation, expected_confirmation
            ):
                raise _deployment_error(
                    "DEPLOYMENT_CONFIRMATION_INVALID",
                    "The deployment confirmation does not match the exact preview.",
                )
            if selected.mode is not DeploymentMode.LOCAL_DESKTOP:
                if self.config.default_provider == "none":
                    raise _deployment_error(
                        "DEPLOYMENT_PROVIDER_CONFLICT",
                        "Provider none permits Local Desktop only.",
                        "Keep Local Desktop or separately select a reviewed provider with consent.",
                    )
                if selected.mode is DeploymentMode.CONNECT_REMOTE:
                    raise _deployment_error(
                        "DEPLOYMENT_ENROLLMENT_REQUIRED",
                        "Remote activation requires a successful authenticated enrollment.",
                        "Complete DEPLOY-12 (#357); a profile edit cannot replace enrollment.",
                    )
                raise _deployment_error(
                    "DEPLOYMENT_HOST_SETUP_REQUIRED",
                    "Hosted-server activation requires the reviewed headless setup authority.",
                    "Complete DEPLOY-03/04 (#348/#363); profile selection never starts a host.",
                )
            if selected == self.config.deployment:
                return self.snapshot()
            candidate = copy(self.config)
            candidate.deployment = selected
            candidate.revision = self.config.revision + 1
            try:
                saved = candidate.save(expected_revision=expected_revision)
            except OSError as exc:
                raise _deployment_error(
                    "DEPLOYMENT_PERSISTENCE_FAILED",
                    "The deployment profile could not be persisted safely.",
                ) from exc
            if not saved:
                raise _deployment_error(
                    "DEPLOYMENT_REVISION_CONFLICT",
                    "The deployment profile changed since it was read; reload and retry.",
                )
            self.config.deployment = candidate.deployment
            self.config.revision = candidate.revision
            return self.snapshot()

    def diagnose(
        self,
        observation: DeploymentObservation | None = None,
    ) -> DeploymentDiagnosticReport:
        """Compare stored intent with sanitized runtime evidence and fail closed."""

        profile = self.config.deployment
        observed = observation or DeploymentObservation(
            mode=DeploymentMode.LOCAL_DESKTOP,
            topology=DeploymentTopology.LOCAL_ONLY,
        )
        failures: list[DeploymentDiagnostic] = []

        def fail(code: str, message: str, remediation: str) -> None:
            failures.append(DeploymentDiagnostic(code, "failed", message, remediation))

        if observed.mode is not profile.mode:
            fail(
                "DEPLOYMENT_MODE_MISMATCH",
                "The runtime mode does not match the stored deployment profile.",
                "Stop the runtime and recover or explicitly select the intended profile.",
            )
        if observed.topology is not profile.topology:
            fail(
                "DEPLOYMENT_TOPOLOGY_MISMATCH",
                "The runtime topology does not match the stored deployment profile.",
                "Stop the runtime and use the reviewed setup path for the stored topology.",
            )
        if profile.mode is DeploymentMode.CONNECT_REMOTE:
            try:
                observed_origin = normalize_endpoint_origin(observed.endpoint_origin)
            except ConfigurationError:
                observed_origin = None
            if observed_origin != profile.endpoint_origin:
                fail(
                    "DEPLOYMENT_ENDPOINT_MISMATCH",
                    "The observed remote endpoint does not match the enrolled origin.",
                    "Stop and repeat authenticated enrollment for the intended exact origin.",
                )
            try:
                observed_identity = normalize_endpoint_identity(observed.endpoint_identity_sha256)
            except ConfigurationError:
                observed_identity = None
            if observed_identity != profile.endpoint_identity_sha256:
                fail(
                    "DEPLOYMENT_IDENTITY_MISMATCH",
                    "The observed endpoint identity does not match the enrolled identity.",
                    "Stop and investigate certificate or endpoint substitution before reconnecting.",
                )
            if observed.authenticated is not True:
                fail(
                    "DEPLOYMENT_AUTHENTICATION_MISSING",
                    "The remote runtime is not authenticated for the stored profile.",
                    "Re-enroll through the reviewed authenticated remote workflow.",
                )
        if (
            profile.mode is not DeploymentMode.LOCAL_DESKTOP
            and self.config.default_provider == "none"
        ):
            fail(
                "DEPLOYMENT_PROVIDER_CONFLICT",
                "Provider none conflicts with a remote deployment profile.",
                "Recover to Local Desktop or separately select a reviewed provider.",
            )
        if profile.mode is DeploymentMode.LOCAL_DESKTOP and any(
            not _is_loopback(host) for host in observed.listener_hosts
        ):
            fail(
                "DEPLOYMENT_LISTENER_SCOPE_MISMATCH",
                "Local Desktop observed a non-loopback listener.",
                "Stop the runtime and restore loopback-only binding before continuing.",
            )
        diagnostics: tuple[DeploymentDiagnostic, ...]
        if failures:
            diagnostics = tuple(failures)
            status: Literal["passed", "failed"] = "failed"
        else:
            diagnostics = (
                DeploymentDiagnostic(
                    "DEPLOYMENT_PROFILE_OK",
                    "passed",
                    "Stored deployment intent matches the supplied runtime evidence.",
                ),
            )
            status = "passed"
        return DeploymentDiagnosticReport(
            schema_version=DEPLOYMENT_SCHEMA_VERSION,
            revision=self.config.revision,
            status=status,
            diagnostics=diagnostics,
        )

    def metadata(self, purpose: str) -> DeploymentEvidence:
        """Return a redacted contract with no endpoint URL, path, provider, or secret."""

        if purpose not in {"backup", "support"}:
            raise _deployment_error(
                "DEPLOYMENT_METADATA_PURPOSE_INVALID",
                "The deployment metadata purpose is unsupported.",
            )
        profile = self.config.deployment
        return DeploymentEvidence(
            schema_version=1,
            purpose=cast("DeploymentEvidencePurpose", purpose),
            deployment_schema_version=profile.schema_version,
            config_revision=self.config.revision,
            mode=profile.mode,
            topology=profile.topology,
            endpoint_identity_sha256=profile.endpoint_identity_sha256,
        )


__all__ = [
    "DeploymentDiagnostic",
    "DeploymentDiagnosticReport",
    "DeploymentEvidence",
    "DeploymentModeDescriptor",
    "DeploymentObservation",
    "DeploymentPreview",
    "DeploymentService",
    "DeploymentSnapshot",
]
