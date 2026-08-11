"""Focused provider-profile and consent command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import SuccessResult
from ancestryllm.execution.common import (
    boolean,
    key_values,
    number,
    structured_result,
    text,
    text_values,
)
from ancestryllm.llm.contracts import DataClass

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext


class ProvidersExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        action = invocation.key.action
        if action == "list":
            value: object = {
                "profiles": self._context.provider_profiles.list_profiles(),
                "consents": self._context.provider_profiles.list_consents(),
            }
        elif action == "create":
            value = self._context.provider_profiles.create_profile(
                text(invocation, "name"),
                text(invocation, "provider"),
                text(invocation, "model"),
                key_values(text_values(invocation, "setting")),
            )
        elif action == "consent":
            value = self._context.provider_profiles.create_consent(
                text(invocation, "name"),
                text(invocation, "profile"),
                modules=list(text_values(invocation, "module")),
                purposes=list(text_values(invocation, "purpose")),
                data_classes=[DataClass(item) for item in text_values(invocation, "data_class")],
                models=list(text_values(invocation, "model")),
                max_cost_usd=number(invocation, "max_cost_usd"),
                retain_payloads=boolean(invocation, "retain_payloads"),
            )
        else:
            name = text(invocation, "name")
            self._context.provider_profiles.revoke_consent(name)
            return CommandOutcome(SuccessResult(f"Revoked consent: {name}"))
        return CommandOutcome(structured_result(value))


__all__ = ["ProvidersExecutor"]
