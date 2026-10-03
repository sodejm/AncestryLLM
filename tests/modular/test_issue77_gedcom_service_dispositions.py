"""Public GEDCOM service dispositions for Issue #77.

These tests exercise the same service boundary used by the CLI and REPL.  They
keep malformed input from reaching providers or publication, while confirming
that useful semantic findings remain available in offline mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from ancestryllm.core.errors import AncestryError
from ancestryllm.gedcom.service import GedcomService

if TYPE_CHECKING:
    from pathlib import Path

_PRIVATE_MARKER = "PRIVATE-NAME-DO-NOT-DISCLOSE"
_SENTINEL = b"preexisting-output-sentinel\n"


def _document(*records: str) -> bytes:
    return (
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR AncestryLLM-Issue77",
                "1 GEDC",
                "2 VERS 5.5.5",
                "1 CHAR UTF-8",
                *records,
                "0 TRLR",
                "",
            )
        )
    ).encode("utf-8")


def _individual(pointer: str = "@I1@") -> tuple[str, ...]:
    return (f"0 {pointer} INDI", f"1 NAME {_PRIVATE_MARKER} /Fiction/")


def _assert_no_publish_residue(directory: Path) -> None:
    assert not list(directory.rglob(".ancestry-publish-*"))


@pytest.mark.parametrize(
    ("name", "payload", "expected_code"),
    (
        (
            "missing-head",
            b"\n".join((*(line.encode() for line in _individual()), b"0 TRLR\n")),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "duplicate-head",
            _document("0 HEAD", *_individual()),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "misplaced-head",
            b"\n".join(
                (
                    b"0 @I1@ INDI",
                    f"1 NAME {_PRIVATE_MARKER} /Fiction/".encode(),
                    b"0 HEAD",
                    b"0 TRLR\n",
                )
            ),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "missing-trailer",
            _document(*_individual()).removesuffix(b"0 TRLR\n"),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "duplicate-trailer",
            _document(*_individual(), "0 TRLR"),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "misplaced-trailer",
            _document("0 TRLR", *_individual()),
            "GEDCOM_PARSE_INVALID",
        ),
        (
            "duplicate-xref",
            _document(*_individual(), *_individual()),
            "GEDCOM_PARSE_INVALID",
        ),
        ("invalid-grammar", _document("0 @I1@", *_individual("@I2@")), "GEDCOM_PARSE_INVALID"),
        ("invalid-encoding", b"\xff\xfe\x00", "FILE_ENCODING_INVALID"),
        ("empty", b"", "FILE_INPUT_EMPTY"),
        (
            "nul",
            _document(*_individual()).replace(b"Fiction", b"Fic\x00tion"),
            "FILE_NUL_BYTE_UNSUPPORTED",
        ),
        (
            "oversized-physical-line",
            _document(*_individual(), "1 NOTE " + "x" * 1_048_576),
            "FILE_LINE_TOO_LONG",
        ),
    ),
    ids=(
        "missing-head",
        "duplicate-head",
        "misplaced-head",
        "missing-trailer",
        "duplicate-trailer",
        "misplaced-trailer",
        "duplicate-xref",
        "invalid-grammar",
        "invalid-encoding",
        "empty",
        "nul",
        "oversized-physical-line",
    ),
)
def test_quality_rejects_adversarial_input_before_provider_or_publication(
    tmp_path: Path,
    name: str,
    payload: bytes,
    expected_code: str,
) -> None:
    source = tmp_path / f"{name}.ged"
    output = tmp_path / f"{name}.quality.md"
    source.write_bytes(payload)
    output.write_bytes(_SENTINEL)
    provider = Mock()
    service = GedcomService(llm=provider)

    with pytest.raises(AncestryError) as raised:
        service.quality(source, output, root_person="@I1@", provider_id="none")

    error = raised.value
    assert error.code == expected_code
    assert _PRIVATE_MARKER not in error.render()
    assert _PRIVATE_MARKER not in repr(error)
    provider.generate.assert_not_called()
    assert output.read_bytes() == _SENTINEL
    _assert_no_publish_residue(tmp_path)


def test_quality_offline_reports_dangling_broken_cycle_malformed_and_empty_findings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "semantic-findings.ged"
    output = tmp_path / "semantic-findings.quality.md"
    source.write_bytes(
        _document(
            "0 @I1@ INDI",
            "1 NAME Root /Fiction/",
            "1 BIRT",
            "2 DATE definitely-not-a-date",
            "1 FAMC @F1@",
            "1 FAMS @F2@",
            "0 @I2@ INDI",
            "1 NAME Parent /Fiction/",
            "1 FAMC @F2@",
            "1 FAMS @F1@",
            "0 @F1@ FAM",
            "1 HUSB @I2@",
            "1 CHIL @I1@",
            "1 CHIL @I404@",
            "0 @F2@ FAM",
            "1 HUSB @I1@",
            "1 CHIL @I2@",
            "0 @F3@ FAM",
            "0 @I3@ INDI",
            "1 NAME Unlinked /Fiction/",
            "0 @F4@ FAM",
            "1 HUSB @I3@",
        )
    )
    provider = Mock()

    result = GedcomService(llm=provider).quality(
        source,
        output,
        root_person="@I1@",
        provider_id="none",
    )

    rendered = result.read_text(encoding="utf-8")
    # ``quality`` deliberately returns a rendered public report rather than
    # internal ``QualityFinding`` DTOs. Its stable report labels correspond to
    # the deterministic codes DANGLING_REFERENCE,
    # NONRECIPROCAL_FAMILY_REFERENCE, ANCESTRY_CYCLE, INVALID_DATE, and
    # EMPTY_FAMILY respectively.
    for finding_label in (
        "Dangling GEDCOM reference",
        "Nonreciprocal family reference",
        "Ancestry cycle detected",
        "Invalid birth date",
        "Empty family record",
    ):
        assert finding_label in rendered
    assert "AI refinement: disabled or unavailable" in rendered
    provider.generate.assert_not_called()
    _assert_no_publish_residue(tmp_path)


def test_merge_does_not_cross_bind_undefined_pointer_to_later_source_definition(
    tmp_path: Path,
) -> None:
    source_a = tmp_path / "source-a.ged"
    source_b = tmp_path / "source-b.ged"
    output = tmp_path / "merged.ged"
    source_a.write_bytes(
        _document(
            "0 @I1@ INDI",
            "1 NAME First /Fiction/",
            "1 FAMS @F1@",
            "0 @F1@ FAM",
            "1 HUSB @I1@",
            "1 CHIL @I99@",
        )
    )
    source_b.write_bytes(_document("0 @I99@ INDI", "1 NAME Second /Fiction/"))
    provider = Mock()

    GedcomService(llm=provider).merge(
        [source_a, source_b],
        output,
        provider_id="none",
    )

    merged = output.read_text(encoding="utf-8")
    assert "1 CHIL @I99@" in merged
    assert "0 @I99@ INDI" not in merged
    assert "0 @I2_1@ INDI" in merged
    provider.generate.assert_not_called()
    _assert_no_publish_residue(tmp_path)
