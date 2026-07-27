"""CLI and service parity for each public file-taking command."""

from __future__ import annotations

import dataclasses
import sqlite3
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


def _rootsmagic_tree(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex TEXT, Living INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Surname TEXT, Given TEXT, IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);
        CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
        INSERT INTO PersonTable VALUES (1, 'U', 0);
        INSERT INTO NameTable VALUES (1, 1, 'Fixture', 'CLI', 1);
        """
    )
    connection.commit()
    connection.close()


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


@pytest.mark.parametrize("action", ("merge", "subtree", "quality"))
def test_all_gedcom_cli_paths_normalize_to_the_stable_sanitized_error(
    action: str,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_input = Path("~PRIVATE-NONEXISTENT/tree.ged")
    private_detail = "PRIVATE path normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_input:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    second = tmp_path / "second.ged"
    _gedcom(second)
    output = tmp_path / "existing.out"
    output.write_text("fictional sentinel\n", encoding="utf-8")
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    arguments = ["gedcom", action, str(private_input)]
    if action == "merge":
        arguments.append(str(second))
    arguments.extend(["--output", str(output)])
    if action != "merge":
        arguments.extend(["--root-person", "Fictional Example"])

    assert main(arguments, app_context) == 2

    error = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in error
    assert str(private_input) not in error
    assert private_detail not in error
    assert output.read_text(encoding="utf-8") == "fictional sentinel\n"
    provider_call.assert_not_called()


def test_rootsmagic_cli_path_normalizes_to_the_stable_sanitized_error(
    app_context: AppContext,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_input = Path("~PRIVATE-NONEXISTENT/tree.rmtree")
    private_detail = "PRIVATE path normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_input:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)

    assert (
        main(
            [
                "rootsmagic",
                "query",
                "--tree",
                str(private_input),
                "--sql",
                "SELECT 1",
            ],
            app_context,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in error
    assert str(private_input) not in error
    assert private_detail not in error


@pytest.mark.parametrize("invalid_target", ("output", "report"))
def test_rootsmagic_export_cli_output_paths_share_stable_sanitized_error(
    invalid_target: str,
    app_context: AppContext,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "fictional.rmtree"
    _rootsmagic_tree(tree)
    app_context.config.family_tree_dirs = [tmp_path]
    invalid = Path(f"~PRIVATE-NONEXISTENT/{invalid_target}.ged")
    private_detail = "PRIVATE RootsMagic export normalization failure"
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == invalid:
            raise RuntimeError(private_detail)
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    provider_call = Mock()
    monkeypatch.setattr(app_context.llm, "generate", provider_call)
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_bytes(b"output sentinel\n")
    report.write_bytes(b"report sentinel\n")

    assert (
        main(
            [
                "rootsmagic",
                "export",
                "--tree",
                str(tree),
                "--output",
                str(invalid if invalid_target == "output" else output),
                "--report",
                str(invalid if invalid_target == "report" else report),
            ],
            app_context,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "[FILE_INPUT_UNREADABLE]" in error
    assert str(invalid) not in error
    assert private_detail not in error
    assert output.read_bytes() == b"output sentinel\n"
    assert report.read_bytes() == b"report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    provider_call.assert_not_called()


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
