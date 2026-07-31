"""Narrow GEDCOM dependency bundle for incremental synchronization.

The sync implementation accepts a module-shaped dependency so its deterministic
algorithms can still be characterized with small test doubles.  Supported
application entry points use this bounded bundle instead of the broad legacy
``engine`` compatibility facade.
"""

from __future__ import annotations

from ancestryllm.gedcom.graph import (
    _rewrite_xrefs as _rewrite_xrefs,
)
from ancestryllm.gedcom.graph import (
    resolve_root_person as resolve_root_person,
)
from ancestryllm.gedcom.identity import (
    IDENTITY_FACT_TAGS as IDENTITY_FACT_TAGS,
)
from ancestryllm.gedcom.identity import (
    _blocking_keys as _blocking_keys,
)
from ancestryllm.gedcom.identity import (
    _normalise_country as _normalise_country,
)
from ancestryllm.gedcom.identity import (
    _top_level_blocks as _top_level_blocks,
)
from ancestryllm.gedcom.identity import (
    assess_similarity as assess_similarity,
)
from ancestryllm.gedcom.identity import (
    enrich_relationship_context as enrich_relationship_context,
)
from ancestryllm.gedcom.identity import (
    normalise_gedcom_date as normalise_gedcom_date,
)
from ancestryllm.gedcom.parser import (
    GedcomRecord as GedcomRecord,
)
from ancestryllm.gedcom.parser import (
    ParsedSource as ParsedSource,
)
from ancestryllm.gedcom.parser import (
    iter_gedcom_records as iter_gedcom_records,
)
from ancestryllm.gedcom.parser import (
    load_sources as load_sources,
)
from ancestryllm.gedcom.parser import (
    parse_gedcom_line as parse_gedcom_line,
)
from ancestryllm.gedcom.quality import (
    analyze_quality as analyze_quality,
)
from ancestryllm.gedcom.quality import (
    render_quality_report as render_quality_report,
)
from ancestryllm.gedcom.serialization import write_gedcom as write_gedcom

__all__ = [
    "GedcomRecord",
    "ParsedSource",
    "analyze_quality",
    "assess_similarity",
    "enrich_relationship_context",
    "iter_gedcom_records",
    "load_sources",
    "normalise_gedcom_date",
    "parse_gedcom_line",
    "render_quality_report",
    "resolve_root_person",
    "write_gedcom",
]
