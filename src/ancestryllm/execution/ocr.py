"""Focused OCR extraction command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.core.ingress import FileIngressPolicy, FileKind
from ancestryllm.execution.common import consent, optional_text, path, structured_result, text

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext


class OcrExecutor:
    """Dispatch OCR commands through the application service boundary."""

    def __init__(self, context: AppContext, ingress: FileIngressPolicy) -> None:
        self._context = context
        self._ingress = ingress

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        """Read bounded OCR input and dispatch consent-bound extraction."""
        from ancestryllm.ocr.service import OcrService

        source = self._ingress.read_text(path(invocation, "input"), FileKind.OCR)
        return CommandOutcome(
            structured_result(
                OcrService(self._context.llm).extract(
                    source,
                    provider_id=text(invocation, "provider"),
                    model=text(invocation, "model"),
                    consent=consent(
                        self._context,
                        optional_text(invocation, "consent"),
                    ),
                ),
            )
        )


__all__ = ["OcrExecutor"]
