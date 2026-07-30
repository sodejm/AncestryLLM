"""Immutable RootsMagic source, schema, and deterministic query boundary."""

from ancestryllm.rootsmagic.reader import QueryResult, RootsMagicReader, SourceFingerprint
from ancestryllm.rootsmagic.schema_adapter import (
    AdaptedTable,
    RootsMagicSchemaAdapter,
    semantic_row_key,
    semantic_value,
)

__all__ = [
    "AdaptedTable",
    "QueryResult",
    "RootsMagicReader",
    "RootsMagicSchemaAdapter",
    "SourceFingerprint",
    "semantic_row_key",
    "semantic_value",
]
