"""Pure structural validation for deterministic GEDCOM 5.5.5 output."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from ancestryllm.gedcom.model import (
    GedcomDocument,
    GedcomLine,
    GedcomParseError,
    parse_gedcom_line,
)


def validate_gedcom_document(
    document: GedcomDocument,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Validate one supported typed document without filesystem access."""

    if document.version == "5.5.5":
        validate_gedcom_555(document.lines, checkpoint=checkpoint)
        return
    if document.version != "5.5.1":
        raise GedcomParseError(f"Unsupported GEDCOM version: {document.version}")
    version_lines = [index for index, line in enumerate(document.lines) if line == "2 VERS 5.5.1"]
    if len(version_lines) != 1:
        raise GedcomParseError("GEDCOM 5.5.1 output must declare its version exactly once")
    compatible = list(document.lines)
    compatible[version_lines[0]] = "2 VERS 5.5.5"
    validate_gedcom_555(compatible, checkpoint=checkpoint)


def validate_gedcom_555(
    lines: Sequence[str],
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Validate the structural requirements emitted for GEDCOM 5.5.5.

    The validator checks generic grammar and referential shape while allowing
    custom tags. It performs no file access, publication, provider, or
    application-service work.
    """
    if not lines:
        raise GedcomParseError("GEDCOM output is empty")
    parsed_lines: list[GedcomLine] = []
    for number, line in enumerate(lines, 1):
        if checkpoint is not None:
            checkpoint()
        parsed_lines.append(parse_gedcom_line(line, number))
    if parsed_lines[0].level != 0 or parsed_lines[0].tag != "HEAD":
        raise GedcomParseError("GEDCOM must start with 0 HEAD")
    if parsed_lines[-1].level != 0 or parsed_lines[-1].tag != "TRLR":
        raise GedcomParseError("GEDCOM must end with 0 TRLR")
    pointers: set[str] = set()
    if sum(parsed.tag == "HEAD" for parsed in parsed_lines if parsed.level == 0) != 1:
        raise GedcomParseError("GEDCOM must contain exactly one 0 HEAD record")
    if sum(parsed.tag == "TRLR" for parsed in parsed_lines if parsed.level == 0) != 1:
        raise GedcomParseError("GEDCOM must contain exactly one 0 TRLR record")
    previous_level = 0
    head_version = ""
    head_charset = ""
    in_gedc = False
    for parsed in parsed_lines:
        if checkpoint is not None:
            checkpoint()
        if len(parsed.raw.encode("utf-8")) > 255:
            raise GedcomParseError("GEDCOM line exceeds the 255-byte limit")
        if len(parsed.tag) > 31:
            raise GedcomParseError(f"GEDCOM tag is longer than 31 characters: {parsed.tag}")
        tag_index = 2 if parsed.xref else 1
        raw_tag = parsed.raw.split()[tag_index]
        if raw_tag != parsed.tag:
            raise GedcomParseError(f"GEDCOM tags must be uppercase: {raw_tag}")
        if parsed.xref:
            if parsed.level != 0:
                raise GedcomParseError("xref IDs may only introduce level-zero records")
            if len(parsed.xref) > 22 or not re.fullmatch(
                r"@[A-Za-z_][A-Za-z0-9_:-]*@", parsed.xref
            ):
                raise GedcomParseError(f"Invalid GEDCOM xref ID: {parsed.xref}")
            if parsed.xref in pointers:
                raise GedcomParseError(f"Duplicate GEDCOM xref ID: {parsed.xref}")
            pointers.add(parsed.xref)
        if parsed.level > previous_level + 1:
            raise GedcomParseError("GEDCOM levels may not skip a level")
        previous_level = parsed.level
        if parsed.level == 1:
            in_gedc = parsed.tag == "GEDC"
            if parsed.tag == "CHAR":
                head_charset = parsed.value.strip().upper()
        elif parsed.level == 2 and in_gedc and parsed.tag == "VERS":
            head_version = parsed.value.strip()
    if head_version != "5.5.5":
        raise GedcomParseError(
            f"Expected HEAD.GEDC.VERS 5.5.5, found {head_version or '(missing)'}"
        )
    if head_charset != "UTF-8":
        raise GedcomParseError(f"Expected HEAD.CHAR UTF-8, found {head_charset or '(missing)'}")


__all__ = ["validate_gedcom_555", "validate_gedcom_document"]
