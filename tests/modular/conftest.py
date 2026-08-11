"""Provide shared isolated application-context fixtures for modular tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.secrets import MemorySecretStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def app_context(tmp_path: Path) -> AppContext:
    config = AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data")
    return AppContext.build(config, MemorySecretStore({}))
