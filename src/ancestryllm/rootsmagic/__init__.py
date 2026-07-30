"""Immutable RootsMagic discovery, querying, and GEDCOM export."""

from ancestryllm.rootsmagic.core import QueryResult
from ancestryllm.rootsmagic.export import RootsMagicExportResult, RootsMagicGedcomDocument
from ancestryllm.rootsmagic.query import RootsMagicQueryService
from ancestryllm.rootsmagic.service import RootsMagicService

__all__ = [
    "QueryResult",
    "RootsMagicExportResult",
    "RootsMagicGedcomDocument",
    "RootsMagicQueryService",
    "RootsMagicService",
]
