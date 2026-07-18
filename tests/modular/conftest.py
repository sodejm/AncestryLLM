from __future__ import annotations

from pathlib import Path

import pytest

from ancestryllm.core.config import AppConfig
from ancestryllm.core.context import AppContext
from ancestryllm.core.secrets import MemorySecretStore


@pytest.fixture
def app_context(tmp_path: Path) -> AppContext:
    config = AppConfig(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data")
    return AppContext.build(config, MemorySecretStore({}))
