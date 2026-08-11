"""Central non-secret configuration with atomic, permission-safe writes."""

from __future__ import annotations

import importlib
import math
import os
import stat
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_path, user_data_path

from ancestryllm.core.deployment import DeploymentProfile
from ancestryllm.core.errors import ConfigurationError, FileIngressError
from ancestryllm.core.ingress import FileIngressLimits, FileIngressPolicy, FileKind

APP_NAME = "ancestryllm"
CONFIG_SCHEMA_VERSION = 1
DEFAULT_MODULES = ("gedcom", "rootsmagic", "ocr", "prompts", "people", "providers", "secrets")
_SECTION_KEYS = {
    "storage": {"data_dir", "family_tree_dirs"},
    "modules": {"enabled"},
    "providers": {"default"},
    "limits": {
        "max_query_rows",
        "max_output_chars",
        "query_timeout_seconds",
        "provider_timeout_seconds",
    },
    "deployment": {
        "schema_version",
        "mode",
        "topology",
        "endpoint_origin",
        "endpoint_identity_sha256",
    },
}
_TOP_LEVEL_KEYS = {*_SECTION_KEYS, "file_ingress", "schema_version", "revision"}


def _secure_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)
    return path


@contextmanager
def _exclusive_config_lock(path: Path) -> Iterator[None]:
    """Serialize config publication across processes without replacing the lock inode."""

    flags = os.O_RDWR | os.O_CREAT
    for optional_flag in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, optional_flag, 0)
    descriptor = os.open(path, flags, 0o600)
    lock_module: Any | None = None
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("The configuration lock must be a regular file.")
        os.fchmod(descriptor, 0o600)
        if os.name == "nt":
            lock_module = importlib.import_module("msvcrt")
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            lock_module.locking(descriptor, lock_module.LK_LOCK, 1)
        else:
            lock_module = importlib.import_module("fcntl")
            lock_module.flock(descriptor, lock_module.LOCK_EX)
        locked = True
        yield
    finally:
        if locked and lock_module is not None:
            with suppress(OSError):
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    lock_module.locking(descriptor, lock_module.LK_UNLCK, 1)
                else:
                    lock_module.flock(descriptor, lock_module.LOCK_UN)
        with suppress(OSError):
            os.close(descriptor)


def _config_error(message: str, *, section: str | None = None) -> ConfigurationError:
    details = {"section": section} if section is not None else {}
    return ConfigurationError(
        "CONFIG_INVALID",
        message,
        "Correct the documented config.toml structure and try again.",
        exit_code=2,
        details=details,
    )


def _section(payload: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = payload.get(name, {})
    if not isinstance(value, Mapping):
        raise _config_error(f"The {name} configuration section must be a table.", section=name)
    unknown = set(value) - _SECTION_KEYS[name]
    if unknown:
        raise _config_error(
            f"The {name} configuration section contains an unsupported key.",
            section=name,
        )
    return value


def _string(value: object, *, section: str, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise _config_error(
            f"The {section}.{field_name} setting must be a string.",
            section=section,
        )
    return value


def _integer(
    value: object,
    *,
    field_name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    selected = default if value is None else value
    if isinstance(selected, bool) or not isinstance(selected, int):
        raise _config_error(
            f"The limits.{field_name} setting must be an integer.",
            section="limits",
        )
    return max(minimum, min(selected, maximum))


def _number(
    value: object,
    *,
    field_name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    selected = default if value is None else value
    if isinstance(selected, bool) or not isinstance(selected, (int, float)):
        raise _config_error(
            f"The limits.{field_name} setting must be a number.",
            section="limits",
        )
    try:
        converted = float(selected)
    except (OverflowError, ValueError) as exc:
        raise _config_error(
            f"The limits.{field_name} setting must be a finite number.",
            section="limits",
        ) from exc
    if not math.isfinite(converted):
        raise _config_error(
            f"The limits.{field_name} setting must be finite.",
            section="limits",
        )
    return max(minimum, min(converted, maximum))


def _path_setting(
    value: str,
    *,
    field_name: str,
    section: str = "storage",
) -> Path:
    """Resolve one configured path without exposing invalid values."""

    if "\x00" in value:
        raise _config_error(
            f"The {section}.{field_name} setting must be a valid path.",
            section=section,
        )
    try:
        return Path(os.path.expandvars(value)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise _config_error(
            f"The {section}.{field_name} setting must be a valid path.",
            section=section,
        ) from exc


@dataclass(slots=True)
class AppConfig:
    """All non-secret application settings."""

    config_path: Path
    data_dir: Path
    family_tree_dirs: list[Path] = field(default_factory=list)
    enabled_modules: set[str] = field(default_factory=lambda: set(DEFAULT_MODULES))
    default_provider: str = "none"
    max_query_rows: int = 100
    max_output_chars: int = 100_000
    query_timeout_seconds: float = 10.0
    provider_timeout_seconds: float = 60.0
    file_ingress: FileIngressLimits = field(default_factory=FileIngressLimits)
    deployment: DeploymentProfile = field(default_factory=DeploymentProfile.local)
    schema_version: int = CONFIG_SCHEMA_VERSION
    revision: int = 0

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workspace.db"

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        configured_config_dir = os.getenv("ANCESTRYLLM_CONFIG_DIR")
        configured_data_dir = os.getenv("ANCESTRYLLM_DATA_DIR")
        if path is not None:
            config_path = path
            config_dir = path.parent
        else:
            config_dir = (
                _path_setting(
                    configured_config_dir,
                    field_name="ANCESTRYLLM_CONFIG_DIR",
                    section="environment",
                )
                if configured_config_dir
                else user_config_path(APP_NAME)
            )
            config_path = config_dir / "config.toml"
        if not os.path.lexists(config_path):
            if path is not None:
                raise FileIngressError(
                    "FILE_INPUT_UNREADABLE",
                    "The config input could not be opened safely.",
                    exit_code=2,
                    details={"input_class": FileKind.CONFIG.value},
                )
            data_dir = (
                _path_setting(
                    configured_data_dir,
                    field_name="ANCESTRYLLM_DATA_DIR",
                    section="environment",
                )
                if configured_data_dir
                else user_data_path(APP_NAME)
            )
            _secure_directory(config_dir)
            return cls(config_path=config_path, data_dir=_secure_directory(data_dir))
        ingress = FileIngressPolicy()
        try:
            config_text = ingress.read_text(config_path, FileKind.CONFIG, allow_empty=True)
            ingress.validate_toml_nesting(config_text)
            payload = tomllib.loads(config_text)
            ingress.validate_structure(
                payload,
                FileKind.CONFIG,
                root_container_implicit=True,
            )
        except RecursionError as exc:
            maximum = ingress.limit(FileKind.CONFIG).max_nesting
            raise FileIngressError(
                "FILE_NESTING_LIMIT_EXCEEDED",
                f"The config input exceeds the configured nesting limit ({maximum}).",
                details={
                    "input_class": FileKind.CONFIG.value,
                    "limit_name": "max_nesting",
                    "limit": maximum,
                },
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(
                "CONFIG_INVALID",
                "Configuration is not valid TOML.",
                "Correct the TOML syntax or restore a known-good configuration file.",
                exit_code=2,
                details={"error_type": type(exc).__name__},
            ) from exc
        if set(payload) - _TOP_LEVEL_KEYS:
            raise _config_error("The configuration contains an unsupported top-level section.")
        schema_version = payload.get("schema_version", CONFIG_SCHEMA_VERSION)
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != CONFIG_SCHEMA_VERSION
        ):
            raise _config_error("The configuration schema version is unsupported.")
        revision = payload.get("revision", 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise _config_error("The configuration revision must be a non-negative integer.")
        storage = _section(payload, "storage")
        modules = _section(payload, "modules")
        providers = _section(payload, "providers")
        limits = _section(payload, "limits")
        deployment = (
            DeploymentProfile.from_mapping(payload["deployment"])
            if "deployment" in payload
            else DeploymentProfile.local()
        )
        configured_data = storage.get("data_dir")
        if configured_data is not None:
            configured_data = _string(
                configured_data,
                section="storage",
                field_name="data_dir",
            )
        roots_value = storage.get("family_tree_dirs", [])
        if not isinstance(roots_value, list) or any(
            not isinstance(item, str) or not item.strip() for item in roots_value
        ):
            raise _config_error(
                "The storage.family_tree_dirs setting must be an array of paths.",
                section="storage",
            )
        enabled_value = modules.get("enabled", list(DEFAULT_MODULES))
        if not isinstance(enabled_value, list) or any(
            not isinstance(item, str) or not item.strip() for item in enabled_value
        ):
            raise _config_error(
                "The modules.enabled setting must be an array of module names.",
                section="modules",
            )
        default_provider = _string(
            providers.get("default", "none"),
            section="providers",
            field_name="default",
        )
        if configured_data:
            resolved_data = _path_setting(configured_data, field_name="data_dir")
        elif configured_data_dir:
            resolved_data = _path_setting(
                configured_data_dir,
                field_name="ANCESTRYLLM_DATA_DIR",
                section="environment",
            )
        else:
            resolved_data = user_data_path(APP_NAME)
        family_tree_dirs = [
            _path_setting(value, field_name="family_tree_dirs") for value in roots_value
        ]
        max_query_rows = _integer(
            limits.get("max_query_rows"),
            field_name="max_query_rows",
            default=100,
            minimum=1,
            maximum=10_000,
        )
        max_output_chars = _integer(
            limits.get("max_output_chars"),
            field_name="max_output_chars",
            default=100_000,
            minimum=1_000,
            maximum=5_000_000,
        )
        query_timeout_seconds = _number(
            limits.get("query_timeout_seconds"),
            field_name="query_timeout_seconds",
            default=10.0,
            minimum=0.1,
            maximum=300.0,
        )
        provider_timeout_seconds = _number(
            limits.get("provider_timeout_seconds"),
            field_name="provider_timeout_seconds",
            default=60.0,
            minimum=1.0,
            maximum=600.0,
        )
        file_ingress = FileIngressLimits.from_mapping(payload.get("file_ingress"))
        if path is None:
            _secure_directory(config_dir)
        return cls(
            config_path=config_path,
            data_dir=_secure_directory(resolved_data),
            family_tree_dirs=family_tree_dirs,
            enabled_modules=set(enabled_value),
            default_provider=default_provider,
            max_query_rows=max_query_rows,
            max_output_chars=max_output_chars,
            query_timeout_seconds=query_timeout_seconds,
            provider_timeout_seconds=provider_timeout_seconds,
            file_ingress=file_ingress,
            deployment=deployment,
            schema_version=schema_version,
            revision=revision,
        )

    def save(self, *, expected_revision: int | None = None) -> bool:
        self.config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "storage": {
                "data_dir": str(self.data_dir),
                "family_tree_dirs": [str(path) for path in self.family_tree_dirs],
            },
            "modules": {"enabled": sorted(self.enabled_modules)},
            "providers": {"default": self.default_provider},
            "limits": {
                "max_query_rows": self.max_query_rows,
                "max_output_chars": self.max_output_chars,
                "query_timeout_seconds": self.query_timeout_seconds,
                "provider_timeout_seconds": self.provider_timeout_seconds,
            },
            "file_ingress": self.file_ingress.to_mapping(),
            "deployment": self.deployment.to_mapping(),
        }
        encoded = tomli_w.dumps(payload).encode("utf-8")
        lock_path = self.config_path.with_name(f".{self.config_path.name}.lock")
        with _exclusive_config_lock(lock_path):
            actual_revision = (
                AppConfig.load(self.config_path).revision if self.config_path.exists() else 0
            )
            if expected_revision is not None and actual_revision != expected_revision:
                return False
            fd, temporary_name = tempfile.mkstemp(prefix=".config-", dir=self.config_path.parent)
            temporary = Path(temporary_name)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.config_path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return True
