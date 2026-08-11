"""Focused deployment-profile control-plane command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.deployment import DeploymentService
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
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


class DeploymentExecutor:
    """Expose profile previews and recovery without starting a deployment runtime."""

    def __init__(self, context: AppContext) -> None:
        self._service = DeploymentService(context.config)

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        action = invocation.key.action
        value: object
        if action == "modes":
            value = self._service.modes()
        elif action == "status":
            value = self._service.snapshot()
        elif action == "preview":
            value = self._service.preview(
                _target(invocation),
                schema_version=integer(invocation, "schema_version"),
                expected_revision=integer(invocation, "expected_revision"),
            )
        elif action == "switch":
            value = self._service.switch(
                _target(invocation),
                schema_version=integer(invocation, "schema_version"),
                expected_revision=integer(invocation, "expected_revision"),
                confirmation=text(invocation, "confirm"),
                unattended=boolean(invocation, "unattended"),
            )
        elif action == "diagnose":
            value = self._service.diagnose()
        elif action == "metadata":
            value = self._service.metadata(text(invocation, "purpose"))
        else:
            raise AncestryError(
                "ARGUMENT_INVALID",
                "The deployment action is unsupported.",
                exit_code=2,
            )
        return CommandOutcome(structured_result(value))


__all__ = ["DeploymentExecutor"]
