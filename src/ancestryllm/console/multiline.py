"""Reusable prompt-toolkit multiline input with privacy-safe defaults."""

from __future__ import annotations

from typing import Protocol

from ancestryllm.core.errors import AncestryError

MAX_MULTILINE_CHARACTERS = 100_000


class AsyncPrompt(Protocol):
    async def prompt_async(self, prompt: str, **kwargs: object) -> str: ...


class MultilineEditor:
    """Read free text without history or command tokenization."""

    def __init__(
        self,
        session: AsyncPrompt,
        *,
        maximum_characters: int = MAX_MULTILINE_CHARACTERS,
    ) -> None:
        self._session = session
        self.maximum_characters = maximum_characters

    async def read(self, prompt: str) -> str:
        try:
            value = await self._session.prompt_async(
                prompt,
                multiline=True,
                prompt_continuation="... ",
            )
        except (EOFError, KeyboardInterrupt) as exc:
            raise AncestryError(
                "MULTILINE_INPUT_CANCELLED",
                "Multiline input was cancelled; the command was not run.",
                exit_code=2,
            ) from exc
        if not value:
            raise AncestryError(
                "MULTILINE_INPUT_EMPTY",
                "Multiline input cannot be empty.",
                "Enter text or cancel the command with Ctrl-C.",
                exit_code=2,
            )
        if len(value) > self.maximum_characters:
            raise AncestryError(
                "MULTILINE_INPUT_TOO_LARGE",
                f"Multiline input exceeds the {self.maximum_characters}-character limit.",
                exit_code=2,
            )
        return value
