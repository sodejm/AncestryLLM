"""Immutable RootsMagic source, schema, and deterministic query boundary."""

from ancestryllm.rootsmagic.schema import (
    AdaptedTable,
    RootsMagicSchemaAdapter,
    semantic_row_key,
    semantic_value,
)
from ancestryllm.rootsmagic.source import (
    DatabaseSchema,
    JsonScalar,
    JsonValue,
    QueryResult,
    RootsMagicReader,
    SourceFingerprint,
    TableSchema,
    TruncationMetadata,
)

__all__ = [
    "AdaptedTable",
    "DatabaseSchema",
    "JsonScalar",
    "JsonValue",
    "QueryResult",
    "RootsMagicReader",
    "RootsMagicSchemaAdapter",
    "SourceFingerprint",
    "TableSchema",
    "TruncationMetadata",
    "semantic_row_key",
    "semantic_value",
]
