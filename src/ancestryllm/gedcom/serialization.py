"""Atomic, preservation-oriented GEDCOM serialization boundary."""

from ancestryllm.gedcom.engine import (
    SUPPORTED_GEDCOM_VERSIONS,
    _wrap_long_gedcom_lines,
    write_gedcom,
)

# Keep the legacy implementation private while giving sibling feature packages
# a supported façade. Moving the implementation belongs to the kernel
# decomposition work; callers must not import ``engine`` directly.
wrap_long_gedcom_lines = _wrap_long_gedcom_lines

__all__ = [
    "SUPPORTED_GEDCOM_VERSIONS",
    "wrap_long_gedcom_lines",
    "write_gedcom",
]
