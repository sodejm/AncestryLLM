"""Supported GEDCOM serialization and publication façade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ancestryllm.gedcom.model import ParsedSource
from ancestryllm.gedcom.serializer import (
    SUPPORTED_GEDCOM_VERSIONS,
    wrap_long_gedcom_lines,
)


def write_gedcom(
    records: list[Any],
    output_path: str | Path,
    source_parsers: list[Any] | None = None,
    source_documents: list[ParsedSource] | None = None,
    pointer_map: dict[str, str] | None = None,
    include_individuals: set[str] | None = None,
    include_families: set[str] | None = None,
    gedcom_version: str = "5.5.5",
) -> Any:
    """Stage GEDCOM output through the publication compatibility adapter.

    Verified caller: ``gedcom.service``. The deterministic line kernel above
    is already independent; CORE-24 (#166) owns retirement of this private
    engine shim once publication orchestration has its permanent boundary.
    """
    from ancestryllm.gedcom.engine import write_gedcom as _write_gedcom

    return _write_gedcom(
        records,
        output_path,
        source_parsers,
        source_documents,
        pointer_map,
        include_individuals,
        include_families,
        gedcom_version,
    )


__all__ = [
    "SUPPORTED_GEDCOM_VERSIONS",
    "wrap_long_gedcom_lines",
    "write_gedcom",
]
