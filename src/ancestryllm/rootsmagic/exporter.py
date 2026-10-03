"""Compatibility alias for the application-owned RootsMagic publisher."""

from __future__ import annotations

import sys

from ancestryllm.application import _rootsmagic_export
from ancestryllm.application._rootsmagic_export import (
    RootsMagicExporter as RootsMagicExporter,
)
from ancestryllm.application._rootsmagic_export import (
    RootsMagicExportResult as RootsMagicExportResult,
)

__all__ = ["RootsMagicExportResult", "RootsMagicExporter"]

# Importers receive the implementation module itself so legacy monkeypatches
# continue to observe validation, staging, and publication state.
sys.modules[__name__] = _rootsmagic_export
