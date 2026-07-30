"""Compatibility exports for the RootsMagic schema implementation."""

from ancestryllm.rootsmagic.schema import (
    AdaptedTable,
    RootsMagicSchemaAdapter,
    semantic_row_key,
    semantic_value,
)

__all__ = [
    "AdaptedTable",
    "RootsMagicSchemaAdapter",
    "semantic_row_key",
    "semantic_value",
]
