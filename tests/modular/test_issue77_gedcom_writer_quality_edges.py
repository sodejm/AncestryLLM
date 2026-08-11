"""Public GEDCOM writer and quality edge characterizations for Issue #77.

These tests intentionally operate through the service, serialization, and
parser boundaries.  They use fictional local files, keep inputs immutable,
and explicitly select the offline provider mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from ancestryllm.gedcom.parser import iter_gedcom_records, parse_gedcom_line
from ancestryllm.gedcom.service import GedcomService

if TYPE_CHECKING:
    from pathlib import Path


def _document(*records: str, version: str = "5.5.5") -> bytes:
    """Return a minimal fictional UTF-8 GEDCOM document."""
    return (
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR Issue77FictionalFixture",
                "1 GEDC",
                f"2 VERS {version}",
                "1 CHAR UTF-8",
                *records,
                "0 TRLR",
                "",
            )
        )
    ).encode("utf-8")


def _logical_value(record_lines: list[str], tag: str) -> str:
    """Reassemble standard CONC continuations for one GEDCOM value."""
    for index, raw in enumerate(record_lines):
        parsed = parse_gedcom_line(raw)
        if parsed.tag != tag:
            continue
        value = parsed.value
        level = parsed.level
        for continuation_raw in record_lines[index + 1 :]:
            continuation = parse_gedcom_line(continuation_raw)
            if continuation.level <= level:
                break
            if continuation.level == level + 1 and continuation.tag == "CONC":
                value += continuation.value
            elif continuation.level == level + 1 and continuation.tag == "CONT":
                value += "\n" + continuation.value
        return value
    raise AssertionError(f"Expected {tag} value was not emitted")


@pytest.mark.parametrize("physical_bytes", (254, 255, 256))
@pytest.mark.parametrize("gedcom_version", ("5.5.5", "5.5.1"))
def test_public_writer_reparses_utf8_boundary_notes_without_loss(
    tmp_path: Path,
    physical_bytes: int,
    gedcom_version: str,
) -> None:
    """The public writer preserves text across the 255-byte physical limit."""
    source = tmp_path / f"source-{physical_bytes}-{gedcom_version}.ged"
    output = tmp_path / f"output-{physical_bytes}-{gedcom_version}.ged"
    # ``1 NOTE `` is seven ASCII bytes; include a two-byte UTF-8 character in
    # every case so the boundary is checked in bytes rather than characters.
    note = "é" + "x" * (physical_bytes - len("1 NOTE ".encode("utf-8")) - 2)
    source.write_bytes(
        _document(
            "0 @I1@ INDI",
            "1 NAME Aster /Fiction/",
            f"1 NOTE {note}",
        )
    )
    source_before = source.read_bytes()

    result = GedcomService().subtree(
        source,
        output,
        root_person="@I1@",
        gedcom_version=gedcom_version,
    )

    assert result.output_path == output.resolve()
    assert source.read_bytes() == source_before
    emitted_records = list(iter_gedcom_records(output))
    emitted_lines = [line for record in emitted_records for line in record.lines]
    assert f"2 VERS {gedcom_version}" in emitted_lines
    assert all(len(line.encode("utf-8")) <= 255 for line in emitted_lines)
    person = next(record for record in emitted_records if record.pointer == "@I1@")
    assert _logical_value(person.lines, "NOTE") == note
    assert _logical_value(person.lines, "NAME") == "Aster /Fiction/"
    assert sum(parse_gedcom_line(line).tag == "CONC" for line in person.lines) == (
        1 if physical_bytes == 256 else 0
    )


def test_quality_does_not_treat_literal_xref_text_as_a_reference(tmp_path: Path) -> None:
    """Free-form extension text must not produce a dangling-xref diagnostic."""
    source = tmp_path / "literal-xref.ged"
    report = tmp_path / "literal-xref.quality.md"
    source.write_bytes(
        _document(
            "0 @I1@ INDI",
            "1 NAME Aster /Fiction/",
            "1 _TEXT literal @Z9@",
        )
    )
    source_before = source.read_bytes()
    provider = Mock()

    result = GedcomService(llm=provider).quality(
        source,
        report,
        root_person="@I1@",
        provider_id="none",
    )

    assert result == report.resolve()
    assert source.read_bytes() == source_before
    assert "Dangling GEDCOM reference" not in report.read_text(encoding="utf-8")
    provider.generate.assert_not_called()


def test_merge_quality_keeps_source_local_undefined_xrefs_dangling(tmp_path: Path) -> None:
    """A later source's xref declaration must not repair source A's broken edge."""
    source_a = tmp_path / "source-a.ged"
    source_b = tmp_path / "source-b.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "merged.quality.md"
    source_a.write_bytes(
        _document(
            "0 @I1@ INDI",
            "1 NAME Aster /Fiction/",
            "1 FAMS @Z9@",
        )
    )
    source_b.write_bytes(_document("0 @Z9@ NOTE", "1 CONT fictional later declaration"))
    source_a_before = source_a.read_bytes()
    source_b_before = source_b.read_bytes()
    provider = Mock()

    GedcomService(llm=provider).merge(
        [source_a, source_b],
        output,
        root_person="@I1@",
        quality_path=report,
        provider_id="none",
    )

    emitted = output.read_text(encoding="utf-8")
    rendered = report.read_text(encoding="utf-8")
    assert source_a.read_bytes() == source_a_before
    assert source_b.read_bytes() == source_b_before
    assert "1 FAMS @Z9@" in emitted
    assert "0 @Z9@ NOTE" not in emitted
    assert "Dangling GEDCOM reference" in rendered
    assert "undefined @Z9@" in rendered
    provider.generate.assert_not_called()
