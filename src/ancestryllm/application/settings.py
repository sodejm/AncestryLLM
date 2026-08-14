"""Versioned, allowlisted non-secret settings management."""

from __future__ import annotations

import math
import threading
from copy import copy
from dataclasses import dataclass
from typing import Literal, Protocol

from ancestryllm.core.errors import ConfigurationError

SettingValue = str | int | float
SettingType = Literal["string", "integer", "number"]


class SettingsConfigPort(Protocol):
    """Mutable configuration values required by the settings service."""

    schema_version: int
    revision: int
    default_provider: str
    max_query_rows: int
    max_output_chars: int
    query_timeout_seconds: float
    provider_timeout_seconds: float

    def save(self, *, expected_revision: int | None = None) -> bool:
        """Persist validated settings with optimistic revision control and report whether content changed."""
        ...


@dataclass(frozen=True, slots=True)
class SettingValidation:
    """Machine-readable validation bounds for one reviewed setting."""

    allowed_values: tuple[str, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None


@dataclass(frozen=True, slots=True)
class SettingField:
    """One renderer-safe setting descriptor."""

    key: str
    label: str
    help: str
    type: SettingType
    value: SettingValue
    default_value: SettingValue
    validation: SettingValidation
    restart_required: bool = False
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """Current reviewed settings and optimistic-lock revision."""

    schema_version: int
    revision: int
    fields: tuple[SettingField, ...]


@dataclass(frozen=True, slots=True)
class _SettingSpec:
    key: str
    attribute: str
    label: str
    help: str
    type: SettingType
    default_value: SettingValue
    validation: SettingValidation
    restart_required: bool = False


_PROVIDERS = ("none", "ollama", "openai", "anthropic", "gemini", "openrouter")
_SETTING_SPECS = (
    _SettingSpec(
        "providers.default",
        "default_provider",
        "Default provider",
        "Provider selected when a command does not specify one explicitly.",
        "string",
        "none",
        SettingValidation(allowed_values=_PROVIDERS),
    ),
    _SettingSpec(
        "limits.max_query_rows",
        "max_query_rows",
        "Maximum query rows",
        "Largest number of database rows returned by one query.",
        "integer",
        100,
        SettingValidation(minimum=1, maximum=10_000),
    ),
    _SettingSpec(
        "limits.max_output_chars",
        "max_output_chars",
        "Maximum output characters",
        "Largest rendered output accepted from one operation.",
        "integer",
        100_000,
        SettingValidation(minimum=1_000, maximum=5_000_000),
    ),
    _SettingSpec(
        "limits.query_timeout_seconds",
        "query_timeout_seconds",
        "Query timeout",
        "Maximum seconds allowed for one local database query.",
        "number",
        10.0,
        SettingValidation(minimum=0.1, maximum=300.0),
    ),
    _SettingSpec(
        "limits.provider_timeout_seconds",
        "provider_timeout_seconds",
        "Provider timeout",
        "Maximum seconds allowed for one explicitly selected provider request.",
        "number",
        60.0,
        SettingValidation(minimum=1.0, maximum=600.0),
    ),
)
_SPECS_BY_KEY = {spec.key: spec for spec in _SETTING_SPECS}


def _settings_error(code: str, message: str, *, exit_code: int = 2) -> ConfigurationError:
    return ConfigurationError(code, message, exit_code=exit_code)


def _validated_value(spec: _SettingSpec, value: object) -> SettingValue:
    validation = spec.validation
    if spec.type == "string":
        if not isinstance(value, str) or value not in validation.allowed_values:
            raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
        return value
    if spec.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
        converted: int | float = value
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
        converted = float(value)
        if not math.isfinite(converted):
            raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
    if validation.minimum is not None and converted < validation.minimum:
        raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
    if validation.maximum is not None and converted > validation.maximum:
        raise _settings_error("SETTINGS_VALUE_INVALID", "A setting value is invalid.")
    return converted


class SettingsService:
    """Expose and atomically update only the reviewed non-secret settings."""

    def __init__(self, config: SettingsConfigPort) -> None:
        self.config = config
        self._lock = threading.RLock()

    def snapshot(self) -> SettingsSnapshot:
        """Return a consistent snapshot of the settings service state."""
        with self._lock:
            fields = tuple(
                SettingField(
                    key=spec.key,
                    label=spec.label,
                    help=spec.help,
                    type=spec.type,
                    value=getattr(self.config, spec.attribute),
                    default_value=spec.default_value,
                    validation=spec.validation,
                    restart_required=spec.restart_required,
                )
                for spec in _SETTING_SPECS
            )
            return SettingsSnapshot(
                schema_version=self.config.schema_version,
                revision=self.config.revision,
                fields=fields,
            )

    def patch(
        self,
        *,
        schema_version: int,
        expected_revision: int,
        changes: dict[str, object],
    ) -> SettingsSnapshot:
        """Validate and persist one optimistic settings update."""

        if schema_version != self.config.schema_version:
            raise _settings_error(
                "SETTINGS_SCHEMA_UNSUPPORTED",
                "The settings schema version is unsupported.",
            )
        with self._lock:
            if expected_revision != self.config.revision:
                raise _settings_error(
                    "SETTINGS_REVISION_CONFLICT",
                    "Settings changed since they were read; reload and retry.",
                )
            candidate = copy(self.config)
            candidate.revision = self.config.revision + 1
            for key, value in changes.items():
                spec = _SPECS_BY_KEY.get(key)
                if spec is None:
                    raise _settings_error(
                        "SETTINGS_FIELD_UNKNOWN",
                        "The settings update contains an unsupported field.",
                    )
                validated = _validated_value(spec, value)
                if key == "providers.default":
                    assert isinstance(validated, str)
                    candidate.default_provider = validated
                elif key == "limits.max_query_rows":
                    assert isinstance(validated, int)
                    candidate.max_query_rows = validated
                elif key == "limits.max_output_chars":
                    assert isinstance(validated, int)
                    candidate.max_output_chars = validated
                elif key == "limits.query_timeout_seconds":
                    assert isinstance(validated, (int, float))
                    candidate.query_timeout_seconds = float(validated)
                elif key == "limits.provider_timeout_seconds":
                    assert isinstance(validated, (int, float))
                    candidate.provider_timeout_seconds = float(validated)
            try:
                saved = candidate.save(expected_revision=expected_revision)
            except OSError as exc:
                raise _settings_error(
                    "SETTINGS_SAVE_FAILED",
                    "Settings could not be stored safely.",
                    exit_code=1,
                ) from exc
            if not saved:
                raise _settings_error(
                    "SETTINGS_REVISION_CONFLICT",
                    "Settings changed since they were read; reload and retry.",
                )
            self.config = candidate
            return self.snapshot()
