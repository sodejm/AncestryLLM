"""CLI and service parity for each public file-taking command."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import Mock

import pytest

from ancestryllm.cli import main
from ancestryllm.core.context import AppContext
from ancestryllm.core.ingress import FileIngressLimits, FileKind


def _set_limit(
    context: AppContext,
    kind: FileKind,
    **changes: int | None,
) -> None:
    current = context.config.file_ingress
    selected = dataclasses.replace(getattr(current, kind.value), **changes)
    context.config.file_ingress = dataclasses.replace(current, **{kind.value: selected})


def _gedcom(path: Path, note: str = "") -> None:
    lines = ["0 HEAD"]
    if note:
        lines.append(f"1 NOTE {note}")
    lines.append("0 TRLR")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("action", ("merge", "subtree", "quality"))
def test_all_gedcom_cli_inputs_share_the_same_error_and_preserve_sentinel(
    action: str,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    oversized = tmp_path / "private.ged"
    _gedcom(oversized, "x" * 40)
    second = tmp_path / "second.ged"
    _gedcom(second)
    output = tmp_path / "existing.out"
    output.write_text("fictional sentinel\n", encoding="utf-8")
    _set_limit(
        app_context,
        FileKind.GEDCOM,
        max_bytes=256,
        max_line_bytes=24,
        max_records=10,
        max_record_bytes=128,
        max_nesting=10,
        max_collection_items=10,
    )

    if action == "merge":
        arguments = [
            "gedcom",
            action,
            str(oversized),
            str(second),
            "--output",
            str(output),
        ]
    else:
        arguments = [
            "gedcom",
            action,
            str(oversized),
            "--output",
            str(output),
            "--root-person",
            "Fictional Example",
        ]

    assert main(arguments, app_context) == 2
    error = capsys.readouterr().err
    assert "[FILE_LINE_TOO_LONG]" in error
    assert str(oversized) not in error
    assert "xxxxxxxx" not in error
    assert output.read_text(encoding="utf-8") == "fictional sentinel\n"


@pytest.mark.parametrize("kind", (FileKind.PROMPT_BODY, FileKind.JSON_SCHEMA, FileKind.OCR))
def test_prompt_schema_and_ocr_cli_files_fail_before_storage_or_provider_use(
    kind: FileKind,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / f"private-{kind.value}.txt"
    source.write_text("fictional-payload", encoding="utf-8")
    _set_limit(app_context, kind, max_bytes=4)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)

    if kind is FileKind.PROMPT_BODY:
        arguments = [
            "prompts",
            "save",
            "fictional",
            "--purpose",
            "local",
            "--body-file",
            str(source),
        ]
    elif kind is FileKind.JSON_SCHEMA:
        arguments = [
            "prompts",
            "save",
            "fictional",
            "--purpose",
            "local",
            "--body",
            "safe",
            "--schema-file",
            str(source),
        ]
    else:
        arguments = [
            "ocr",
            "extract",
            "--input",
            str(source),
            "--provider",
            "none",
            "--model",
            "offline",
        ]

    assert main(arguments, app_context) == 2
    error = capsys.readouterr().err
    assert "[FILE_INPUT_TOO_LARGE]" in error
    assert str(source) not in error
    assert "fictional-payload" not in error
    assert app_context.prompts.list() == []
    provider_call.assert_not_called()


@pytest.mark.parametrize("oversized", ("master", "snapshot", "manifest"))
def test_sync_ingress_rejection_creates_no_release_or_failure_report(
    oversized: str,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    manifest = tmp_path / "manifest.json"
    _gedcom(master)
    _gedcom(snapshot)
    manifest.write_text("{}", encoding="utf-8")
    release_root = tmp_path / "releases"
    _set_limit(
        app_context,
        FileKind.GEDCOM,
        max_bytes=16,
        max_line_bytes=16,
        max_records=10,
        max_record_bytes=16,
        max_nesting=10,
        max_collection_items=10,
    )
    _set_limit(
        app_context,
        FileKind.MANIFEST,
        max_bytes=8,
        max_line_bytes=8,
        max_records=4,
        max_nesting=4,
        max_collection_items=10,
    )
    selected = {"master": master, "snapshot": snapshot, "manifest": manifest}[oversized]
    selected.write_bytes(b"x" * 17)

    arguments = [
        "gedcom",
        "sync",
        "update",
        "--master",
        str(master),
        "--snapshot",
        f"fictional:other={snapshot}",
        "--release-root",
        str(release_root),
        "--no-quality-report",
    ]
    if oversized == "manifest":
        arguments.extend(["--manifest", str(manifest)])
    else:
        arguments.append("--initialize-manifest")

    assert main(arguments, app_context) == 2
    error = capsys.readouterr().err
    assert "[FILE_INPUT_TOO_LARGE]" in error
    assert str(selected) not in error
    assert not release_root.exists()


def test_prompt_file_one_shot_returns_the_documented_exit_code(
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "private-body.txt"
    source.write_text("fictional payload", encoding="utf-8")
    _set_limit(app_context, FileKind.PROMPT_BODY, max_bytes=4)

    assert (
        main(
            [
                "prompts",
                "save",
                "fixture",
                "--purpose",
                "local",
                "--body-file",
                str(source),
            ],
            app_context,
        )
        == 2
    )
    assert "[FILE_INPUT_TOO_LARGE]" in capsys.readouterr().err


def test_defaults_remain_explicit_and_documentable() -> None:
    limits = FileIngressLimits()
    assert limits.ocr.max_bytes == 5_000_000
    assert limits.gedcom.max_records == 5_000_000
    assert limits.rootsmagic.max_records == 5_000_000
