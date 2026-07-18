"""Central non-secret configuration with atomic, permission-safe writes."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_path, user_data_path

from ancestryllm.core.errors import ConfigurationError

APP_NAME = "ancestryllm"
DEFAULT_MODULES = ("gedcom", "rootsmagic", "ocr", "prompts", "people", "providers", "secrets")


def _secure_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


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

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workspace.db"

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        configured_config_dir = os.getenv("ANCESTRYLLM_CONFIG_DIR")
        configured_data_dir = os.getenv("ANCESTRYLLM_DATA_DIR")
        config_dir = _secure_directory(
            Path(configured_config_dir).expanduser().resolve()
            if configured_config_dir
            else path.parent.resolve()
            if path
            else user_config_path(APP_NAME)
        )
        data_dir = _secure_directory(
            Path(configured_data_dir).expanduser().resolve()
            if configured_data_dir
            else user_data_path(APP_NAME)
        )
        config_path = path or config_dir / "config.toml"
        if not config_path.exists():
            return cls(config_path=config_path, data_dir=data_dir)
        try:
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(
                "CONFIG_INVALID",
                f"Configuration could not be read: {config_path}",
                "Correct the TOML syntax or restore a known-good configuration file.",
                details={"error_type": type(exc).__name__},
            ) from exc
        storage = payload.get("storage", {})
        modules = payload.get("modules", {})
        providers = payload.get("providers", {})
        roots = storage.get("family_tree_dirs", [])
        configured_data = storage.get("data_dir")
        resolved_data = _secure_directory(
            Path(os.path.expandvars(os.path.expanduser(configured_data))).resolve()
            if configured_data
            else data_dir
        )
        return cls(
            config_path=config_path,
            data_dir=resolved_data,
            family_tree_dirs=[
                Path(os.path.expandvars(os.path.expanduser(value))).resolve() for value in roots
            ],
            enabled_modules=set(modules.get("enabled", DEFAULT_MODULES)),
            default_provider=str(providers.get("default", "none")),
            max_query_rows=max(
                1, min(int(payload.get("limits", {}).get("max_query_rows", 100)), 10_000)
            ),
            max_output_chars=max(
                1_000,
                min(int(payload.get("limits", {}).get("max_output_chars", 100_000)), 5_000_000),
            ),
            query_timeout_seconds=max(
                0.1, min(float(payload.get("limits", {}).get("query_timeout_seconds", 10.0)), 300.0)
            ),
            provider_timeout_seconds=max(
                1.0,
                min(float(payload.get("limits", {}).get("provider_timeout_seconds", 60.0)), 600.0),
            ),
        )

    def save(self) -> None:
        _secure_directory(self.config_path.parent)
        payload: dict[str, Any] = {
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
        }
        encoded = tomli_w.dumps(payload).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".config-", dir=self.config_path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.config_path)
            self.config_path.chmod(0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
