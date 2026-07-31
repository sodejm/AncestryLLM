"""Public RootsMagic export façade with legacy publication compatibility."""

from ancestryllm.rootsmagic.exporter import RootsMagicExporter, RootsMagicExportResult
from ancestryllm.rootsmagic.mapping import (
    ExportReport,
    RootsMagicGedcomDocument,
    RootsMagicLossReport,
    RootsMagicMapper,
    RootsMagicUnmappedColumns,
)

__all__ = [
    "ExportReport",
    "RootsMagicExportResult",
    "RootsMagicExporter",
    "RootsMagicGedcomDocument",
    "RootsMagicLossReport",
    "RootsMagicMapper",
    "RootsMagicUnmappedColumns",
]
