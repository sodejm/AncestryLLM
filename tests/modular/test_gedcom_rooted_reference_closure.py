"""Rooted GEDCOM exports retain only their transitive record dependencies."""

from __future__ import annotations

from pathlib import Path

from ancestryllm.gedcom.engine import XREF_RE, parse_gedcom_line, validate_gedcom_555
from ancestryllm.gedcom.service import GedcomService


def _write_reference_graph(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR Fictional Rooted Closure Test",
                "1 GEDC",
                "2 VERS 5.5.5",
                "1 CHAR UTF-8",
                "1 SUBM @U1@",
                "0 @I1@ INDI",
                "1 NAME Rowan /Root/",
                "1 FAMS @F1@",
                "1 NOTE @N1@",
                "1 SOUR @S1@",
                "1 OBJE @O1@",
                "0 @I2@ INDI",
                "1 NAME Sage /Root/",
                "1 FAMS @F1@",
                "0 @F1@ FAM",
                "1 HUSB @I1@",
                "1 WIFE @I2@",
                "1 SOUR @S3@",
                "0 @N1@ NOTE",
                "1 CONT Fictional retained note",
                "1 SOUR @S2@",
                "0 @S1@ SOUR",
                "1 TITL Fictional retained source",
                "1 REPO @R1@",
                "0 @O1@ OBJE",
                "1 FILE fictional-retained-media.jpg",
                "1 NOTE @N2@",
                "0 @S2@ SOUR",
                "1 TITL Fictional transitive source",
                "1 OBJE @O2@",
                "0 @O2@ OBJE",
                "1 FILE fictional-transitive-media.jpg",
                "1 REPO @R2@",
                "0 @R1@ REPO",
                "1 NAME Fictional retained repository",
                "1 NOTE @N3@",
                "0 @R2@ REPO",
                "1 NAME Fictional transitive repository",
                "0 @N2@ NOTE",
                "1 CONT Fictional media note",
                "0 @N3@ NOTE",
                "1 CONT Fictional repository note",
                "0 @S3@ SOUR",
                "1 TITL Fictional family source",
                "1 NOTE @N5@",
                "0 @N5@ NOTE",
                "1 CONT Fictional family-source note",
                "0 @U1@ SUBM",
                "1 NAME Fictional Required Submitter",
                "1 NOTE @N4@",
                "0 @N4@ NOTE",
                "1 CONT Fictional submitter note",
                "0 @I9@ INDI",
                "1 NAME Detached /Person/",
                "1 FAMS @F9@",
                "1 NOTE @N9@",
                "0 @F9@ FAM",
                "1 HUSB @I9@",
                "1 SOUR @S9@",
                "0 @N9@ NOTE",
                "1 CONT Fictional detached note",
                "0 @S9@ SOUR",
                "1 TITL Fictional detached source",
                "1 REPO @R9@",
                "0 @R9@ REPO",
                "1 NAME Fictional detached repository",
                "0 @O9@ OBJE",
                "1 FILE fictional-detached-media.jpg",
                "0 @U9@ SUBM",
                "1 NAME Fictional Detached Submitter",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


def _declared_pointers(lines: list[str]) -> set[str]:
    return {
        parsed.xref
        for line in lines
        if (parsed := parse_gedcom_line(line)).level == 0 and parsed.xref
    }


def _exact_pointer_references(lines: list[str]) -> set[str]:
    return {
        parsed.value.strip()
        for line in lines
        if not (parsed := parse_gedcom_line(line)).xref and XREF_RE.fullmatch(parsed.value.strip())
    }


def test_subtree_retains_only_transitive_referenced_level_zero_records(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-reference-graph.ged"
    first_output = tmp_path / "first-rooted.ged"
    second_output = tmp_path / "second-rooted.ged"
    _write_reference_graph(source)
    source_before = source.read_bytes()

    service = GedcomService()
    service.subtree(source, first_output, root_person="@I1@")
    service.subtree(source, second_output, root_person="@I1@")

    first_bytes = first_output.read_bytes()
    assert source.read_bytes() == source_before
    assert first_bytes == second_output.read_bytes()
    assert first_bytes.endswith(b"\n")

    lines = first_output.read_text(encoding="utf-8").splitlines()
    validate_gedcom_555(lines)
    declared = _declared_pointers(lines)
    assert declared == {
        "@I1@",
        "@I2@",
        "@F1@",
        "@N1@",
        "@N2@",
        "@N3@",
        "@N4@",
        "@N5@",
        "@S1@",
        "@S2@",
        "@S3@",
        "@O1@",
        "@O2@",
        "@R1@",
        "@R2@",
        "@U1@",
    }
    assert _exact_pointer_references(lines) <= declared
    assert {
        "@I9@",
        "@F9@",
        "@N9@",
        "@S9@",
        "@R9@",
        "@O9@",
        "@U9@",
    }.isdisjoint(declared)
