"""Compatibility alias for the RootsMagic schema implementation."""

from __future__ import annotations

import sys

from ancestryllm.rootsmagic import schema as _schema

# Importers receive the implementation module itself so monkeypatches through
# either the legacy name or the physical owner observe the same state.
sys.modules[__name__] = _schema
