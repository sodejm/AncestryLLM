"""Contracts for the physically separated GEDCOM document foundation."""

from __future__ import annotations

import pytest

from ancestryllm.gedcom import parser, serialization
from ancestryllm.gedcom.model import (
    GedcomLine,
    GedcomParseError,
    GedcomRecord,
    ParsedSource,
    parse_gedcom_line,
)
from ancestryllm.gedcom.serializer import wrap_long_gedcom_lines
from ancestryllm.gedcom.validator import validate_gedcom_555


def _document_with_note(note: str) -> list[str]:
    return [
        "0 HEAD",
        "1 GEDC",
        "2 VERS 5.5.5",
        "1 CHAR UTF-8",
        "0 @I1@ INDI",
        "1 NAME Rowan /Fiction/",
        f"1 NOTE {note}",
        "0 TRLR",
    ]


def _reassemble_note(lines: list[str]) -> str:
    note = ""
    collecting = False
    for raw in lines:
        parsed = parse_gedcom_line(raw)
        if parsed.level == 1 and parsed.tag == "NOTE":
            note = parsed.value
            collecting = True
        elif collecting and parsed.level == 2 and parsed.tag == "CONC":
            note += parsed.value
        elif collecting:
            break
    return note


def test_supported_facades_reexport_the_document_contracts() -> None:
    assert parser.GedcomLine is GedcomLine
    assert parser.GedcomParseError is GedcomParseError
    assert parser.GedcomRecord is GedcomRecord
    assert parser.ParsedSource is ParsedSource
    assert parser.parse_gedcom_line is parse_gedcom_line
    assert parser.validate_gedcom_555 is validate_gedcom_555
    assert serialization.wrap_long_gedcom_lines is wrap_long_gedcom_lines


def test_parse_serialize_parse_preserves_unicode_content_and_structure() -> None:
    note = "Fictional archive note " + ("🌳é" * 180)
    wrapped = wrap_long_gedcom_lines(_document_with_note(note))

    validate_gedcom_555(wrapped)

    assert all(len(line.encode("utf-8")) <= 255 for line in wrapped)
    assert _reassemble_note(wrapped) == note
    assert [parse_gedcom_line(line).tag for line in wrapped[:6]] == [
        "HEAD",
        "GEDC",
        "VERS",
        "CHAR",
        "INDI",
        "NAME",
    ]


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "GEDCOM output is empty"),
        (
            ["0 HEAD", "1 GEDC", "2 VERS 5.5.1", "1 CHAR UTF-8", "0 TRLR"],
            "Expected HEAD.GEDC.VERS 5.5.5, found 5.5.1",
        ),
        (
            ["0 HEAD", "1 GEDC", "2 VERS 5.5.5", "1 CHAR ASCII", "0 TRLR"],
            "Expected HEAD.CHAR UTF-8, found ASCII",
        ),
    ],
)
def test_document_validator_preserves_stable_diagnostics(
    lines: list[str],
    message: str,
) -> None:
    with pytest.raises(GedcomParseError, match=f"^{message}$"):
        validate_gedcom_555(lines)
