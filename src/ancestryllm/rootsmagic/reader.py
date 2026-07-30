"""Compatibility alias for the immutable RootsMagic source implementation."""

from __future__ import annotations

import sys

from ancestryllm.rootsmagic import source as _source

# Preserve legacy monkeypatch and import behavior while physical ownership moves
# to ``rootsmagic.source``. Importers receive the implementation module itself.
sys.modules[__name__] = _source
