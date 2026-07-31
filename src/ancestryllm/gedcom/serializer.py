"""Pure, deterministic GEDCOM line serialization."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ancestryllm.gedcom.model import GedcomDocument, GedcomParseError, parse_gedcom_line

SUPPORTED_GEDCOM_VERSIONS = ("5.5.5", "5.5.1")


def serialize_gedcom_document(document: GedcomDocument) -> str:
    """Serialize typed GEDCOM content without choosing or writing a path."""

    if document.version not in SUPPORTED_GEDCOM_VERSIONS:
        raise GedcomParseError(f"Unsupported GEDCOM version: {document.version}")
    return "\n".join(document.lines) + "\n"


def _canonical_gedcom_line(line: str) -> str:
    """Emit one line with canonical ASCII level/tag spelling."""
    parsed = parse_gedcom_line(line)
    prefix = f"{parsed.level} "
    if parsed.xref:
        prefix += f"{parsed.xref} "
    prefix += parsed.tag
    return f"{prefix} {parsed.value}" if parsed.value else prefix


def _take_utf8_prefix(value: str, limit: int) -> tuple[str, str]:
    """Take the largest character-safe UTF-8 prefix within ``limit`` bytes."""
    used = 0
    end = 0
    for index, character in enumerate(value):
        size = len(character.encode("utf-8"))
        if used + size > limit:
            break
        used += size
        end = index + 1
    if end == 0 and value:
        raise GedcomParseError("GEDCOM continuation limit cannot hold one character")
    return value[:end], value[end:]


def wrap_long_gedcom_lines(
    lines: Sequence[str],
    *,
    checkpoint: Callable[[], None] | None = None,
) -> list[str]:
    """Wrap long text values using standard level+1 ``CONC`` continuations."""
    wrapped: list[str] = []
    for line in lines:
        if checkpoint is not None:
            checkpoint()
        parsed = parse_gedcom_line(line)
        canonical = _canonical_gedcom_line(line)
        if len(canonical.encode("utf-8")) <= 255 or not parsed.value:
            wrapped.append(canonical)
            continue
        if parsed.level >= 99:
            raise GedcomParseError("Cannot wrap a value below GEDCOM level 99")
        prefix = canonical[: len(canonical) - len(parsed.value)]
        remaining = parsed.value
        first_limit = 255 - len(prefix.encode("utf-8"))
        first, remaining = _take_utf8_prefix(remaining, first_limit)
        wrapped.append(prefix + first)
        continuation_prefix = f"{parsed.level + 1} CONC "
        continuation_limit = 255 - len(continuation_prefix.encode("utf-8"))
        while remaining:
            if checkpoint is not None:
                checkpoint()
            chunk, remaining = _take_utf8_prefix(remaining, continuation_limit)
            wrapped.append(continuation_prefix + chunk)
    return wrapped


__all__ = [
    "SUPPORTED_GEDCOM_VERSIONS",
    "serialize_gedcom_document",
    "wrap_long_gedcom_lines",
]
