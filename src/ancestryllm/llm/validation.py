"""Treat all model output as untrusted data."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import ValidationError, validate

from ancestryllm.core.errors import ProviderError


def validate_structured_output(text: str, schema: dict[str, Any] | None) -> Any | None:
    if schema is None:
        return None
    try:
        parsed = json.loads(text)
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID",
            "The model returned output that did not match the required schema.",
            "Retry with a different model or inspect the saved non-sensitive run metadata.",
            details={"error_type": type(exc).__name__},
        ) from exc
    return parsed
