"""Focused deployment-profile control-plane command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.deployment import (
    DeploymentDiagnostic,
    DeploymentModeDescriptor,
    DeploymentService,
)
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.operations import (
    DeploymentDiagnoseResult,
    DeploymentDiagnosticRecord,
    DeploymentMetadataResult,
    DeploymentModeRecord,
    DeploymentModesResult,
    DeploymentPreviewResult,
    DeploymentProfileRecord,
    DeploymentStatusResult,
    DeploymentSwitchResult,
)
from ancestryllm.core.deployment import DeploymentMode, DeploymentProfile
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.common import boolean, integer, optional_text, structured_result, text

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext


def _target(invocation: CommandInvocation) -> DeploymentProfile:
    raw_mode = text(invocation, "mode")
    try:
        mode = DeploymentMode(raw_mode)
    except ValueError as exc:
        raise AncestryError(
            "ARGUMENT_INVALID",
            "Command argument 'mode' is not a supported deployment mode.",
            exit_code=2,
        ) from exc
    endpoint_origin = optional_text(invocation, "endpoint_origin")
    endpoint_identity = optional_text(invocation, "endpoint_identity_sha256")
    if mode is DeploymentMode.CONNECT_REMOTE:
        return DeploymentProfile.connect_remote(
            endpoint_origin=endpoint_origin,
            endpoint_identity_sha256=endpoint_identity,
        )
    if endpoint_origin is not None or endpoint_identity is not None:
        raise AncestryError(
            "ARGUMENT_INVALID",
            "Remote endpoint arguments are valid only for Connect to Remote.",
            exit_code=2,
        )
    if mode is DeploymentMode.HOST_REMOTE_SERVER:
        return DeploymentProfile.host_remote_server()
    return DeploymentProfile.local()


def _profile_record(profile: DeploymentProfile) -> DeploymentProfileRecord:
    return DeploymentProfileRecord(
        schema_version=profile.schema_version,
        mode_code=profile.mode.value,
        topology_code=profile.topology.value,
        endpoint_origin=profile.endpoint_origin,
        endpoint_identity_sha256=profile.endpoint_identity_sha256,
    )


def _mode_record(descriptor: DeploymentModeDescriptor) -> DeploymentModeRecord:
    return DeploymentModeRecord(
        mode_code=descriptor.mode.value,
        label=descriptor.label,
        summary=descriptor.summary,
        consequences=descriptor.consequences,
        prerequisites=descriptor.prerequisites,
        default=descriptor.default,
        recommended=descriptor.recommended,
        advanced=descriptor.advanced,
    )


def _diagnostic_record(diagnostic: DeploymentDiagnostic) -> DeploymentDiagnosticRecord:
    return DeploymentDiagnosticRecord(
        diagnostic_code=diagnostic.code,
        status_code=diagnostic.status,
        message=diagnostic.message,
        remediation=diagnostic.remediation,
    )


class DeploymentExecutor:
    """Expose profile previews and recovery without starting a deployment runtime."""

    def __init__(self, context: AppContext) -> None:
        self._service = DeploymentService(context.config)

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        action = invocation.key.action
        value: object
        if action == "modes":
            value = DeploymentModesResult(
                modes=tuple(_mode_record(descriptor) for descriptor in self._service.modes())
            )
        elif action == "status":
            snapshot = self._service.snapshot()
            value = DeploymentStatusResult(
                schema_version=snapshot.schema_version,
                revision=snapshot.revision,
                profile=_profile_record(snapshot.profile),
            )
        elif action == "preview":
            preview = self._service.preview(
                _target(invocation),
                schema_version=integer(invocation, "schema_version"),
                expected_revision=integer(invocation, "expected_revision"),
            )
            value = DeploymentPreviewResult(
                schema_version=preview.schema_version,
                expected_revision=preview.expected_revision,
                current=_profile_record(preview.current),
                target=_profile_record(preview.target),
                consequences=preview.consequences,
                confirmation=preview.confirmation,
            )
        elif action == "switch":
            snapshot = self._service.switch(
                _target(invocation),
                schema_version=integer(invocation, "schema_version"),
                expected_revision=integer(invocation, "expected_revision"),
                confirmation=text(invocation, "confirm"),
                unattended=boolean(invocation, "unattended"),
            )
            value = DeploymentSwitchResult(
                schema_version=snapshot.schema_version,
                revision=snapshot.revision,
                profile=_profile_record(snapshot.profile),
            )
        elif action == "diagnose":
            report = self._service.diagnose()
            value = DeploymentDiagnoseResult(
                schema_version=report.schema_version,
                revision=report.revision,
                status_code=report.status,
                diagnostics=tuple(
                    _diagnostic_record(diagnostic) for diagnostic in report.diagnostics
                ),
            )
        elif action == "metadata":
            evidence = self._service.metadata(text(invocation, "purpose"))
            value = DeploymentMetadataResult(
                schema_version=evidence.schema_version,
                purpose_code=evidence.purpose,
                deployment_schema_version=evidence.deployment_schema_version,
                config_revision=evidence.config_revision,
                mode_code=evidence.mode.value,
                topology_code=evidence.topology.value,
                endpoint_identity_sha256=evidence.endpoint_identity_sha256,
            )
        else:
            raise AncestryError(
                "ARGUMENT_INVALID",
                "The deployment action is unsupported.",
                exit_code=2,
            )
        return CommandOutcome(structured_result(value))


__all__ = ["DeploymentExecutor"]
