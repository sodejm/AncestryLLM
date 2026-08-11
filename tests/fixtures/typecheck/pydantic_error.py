"""Intentional Pydantic validator type error for checker parity evaluation."""

from pydantic import BaseModel, field_validator


class Message(BaseModel):
    """A representative model with the validation pattern used by the project."""

    role: str
    content: str

    @field_validator("content")
    @classmethod
    def content_is_not_blank(cls, value: str) -> str:
        return 123


def build_invalid_message() -> Message:
    return Message(role="user", content="valid")
