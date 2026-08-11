"""Public GEDCOM CLI boundaries exercised with adversarial local fixtures."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from ancestryllm.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.core.context import AppContext


def _set_gedcom_limit(context: AppContext, **changes: int | None) -> None:
    limits = context.config.file_ingress
    gedcom = dataclasses.replace(limits.gedcom, **changes)
    context.config.file_ingress = dataclasses.replace(limits, gedcom=gedcom)


def _write_person_gedcom(path: Path, pointer: str, given: str) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR Issue 77 CLI fixture",
                "1 GEDC",
                "2 VERS 5.5.5",
                "2 FORM LINEAGE-LINKED",
                "1 CHAR UTF-8",
                f"0 {pointer} INDI",
                f"1 NAME {given} /Example/",
                "1 BIRT",
                "2 DATE 1850",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("payload", "error_code", "private_marker"),
    (
        (
            b"0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n\xff\n0 TRLR\n",
            "FILE_ENCODING_INVALID",
            "private-invalid-utf8.ged",
        ),
        (
            b"0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME PRIVATE_NUL\x00 /Example/\n0 TRLR\n",
            "FILE_NUL_BYTE_UNSUPPORTED",
            "PRIVATE_NUL",
        ),
        (
            b"0 HEAD\nNOT_GEDCOM PRIVATE_MALFORMED\n0 TRLR\n",
            "GEDCOM_PARSE_INVALID",
            "PRIVATE_MALFORMED",
        ),
        (b"", "FILE_INPUT_EMPTY", "private-empty.ged"),
    ),
    ids=("invalid-utf8", "nul-byte", "malformed", "empty"),
)
def test_gedcom_merge_cli_rejects_adversarial_ingress_before_output_or_provider(
    payload: bytes,
    error_code: str,
    private_marker: str,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "private-invalid-utf8.ged"
    invalid.write_bytes(payload)
    second = tmp_path / "second.ged"
    _write_person_gedcom(second, "@I2@", "Grace")
    output = tmp_path / "merged.ged"
    sentinel = b"private output sentinel\n"
    output.write_bytes(sentinel)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    assert (
        main(
            [
                "gedcom",
                "merge",
                str(invalid),
                str(second),
                "--output",
                str(output),
                "--provider",
                "none",
            ],
            app_context,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert f"[{error_code}]" in error
    assert str(invalid) not in error
    assert private_marker not in error
    assert output.read_bytes() == sentinel
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()


def test_gedcom_merge_cli_rejects_oversized_line_before_output_or_provider(
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "private-oversized.ged"
    invalid.write_bytes(b"0 HEAD\n1 NOTE PRIVATE_OVERSIZED_" + b"x" * 48 + b"\n0 TRLR\n")
    second = tmp_path / "second.ged"
    _write_person_gedcom(second, "@I2@", "Grace")
    output = tmp_path / "merged.ged"
    sentinel = b"private output sentinel\n"
    output.write_bytes(sentinel)
    _set_gedcom_limit(app_context, max_line_bytes=32)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    assert (
        main(
            [
                "gedcom",
                "merge",
                str(invalid),
                str(second),
                "--output",
                str(output),
                "--provider",
                "none",
            ],
            app_context,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "[FILE_LINE_TOO_LONG]" in error
    assert str(invalid) not in error
    assert "PRIVATE_OVERSIZED" not in error
    assert output.read_bytes() == sentinel
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()


def test_gedcom_cli_provider_none_merges_and_writes_quality_without_llm(
    app_context: AppContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    merged = tmp_path / "merged.ged"
    merge_report = tmp_path / "merge-quality.md"
    standalone_report = tmp_path / "quality.md"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    assert (
        main(
            [
                "gedcom",
                "merge",
                str(first),
                str(second),
                "--output",
                str(merged),
                "--root-person",
                "@I1@",
                "--quality-report",
                str(merge_report),
                "--provider",
                "none",
            ],
            app_context,
        )
        == 0
    )
    assert "0 TRLR" in merged.read_text(encoding="utf-8")
    assert merge_report.read_text(encoding="utf-8").startswith("# GEDCOM Merge Quality Report")

    assert (
        main(
            [
                "gedcom",
                "quality",
                str(first),
                "--output",
                str(standalone_report),
                "--root-person",
                "@I1@",
                "--provider",
                "none",
            ],
            app_context,
        )
        == 0
    )
    assert standalone_report.read_text(encoding="utf-8").startswith("# GEDCOM Merge Quality Report")
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()


def test_gedcom_merge_cli_rejects_quality_report_alias_to_input_before_writing(
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "private-first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    source_before = first.read_bytes()
    sentinel = b"private output sentinel\n"
    output.write_bytes(sentinel)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    assert (
        main(
            [
                "gedcom",
                "merge",
                str(first),
                str(second),
                "--output",
                str(output),
                "--root-person",
                "@I1@",
                "--quality-report",
                str(first),
                "--provider",
                "none",
            ],
            app_context,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "[GEDCOM_REPORT_ALIAS]" in error
    assert str(first) not in error
    assert output.read_bytes() == sentinel
    assert first.read_bytes() == source_before
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()
