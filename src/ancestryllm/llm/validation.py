"""Treat all model output as untrusted data."""

from __future__ import annotations

import json
from typing import Any, Never

from jsonschema import ValidationError, validate

from ancestryllm.core.errors import ProviderError


def validate_structured_output(text: str, schema: dict[str, Any] | None) -> Any | None:
    if schema is None:
        return None

    def reject_non_json_number(value: str) -> Never:
        raise ValueError(f"Non-standard JSON number is not allowed: {value}")

    try:
        parsed = json.loads(text, parse_constant=reject_non_json_number)
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID",
            "The model returned output that did not match the required schema.",
            "Retry with a different model or inspect the saved non-sensitive run metadata.",
            details={"error_type": type(exc).__name__},
        ) from exc
    return parsed
