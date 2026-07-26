"""Deterministic adversarial coverage for the public file-ingress policy."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

import ancestryllm.gedcom.engine as gedcom_engine
import ancestryllm.rootsmagic.exporter as exporter_module
from ancestryllm.core.config import AppConfig
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import (
    FileIngressLimits,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
)
from ancestryllm.gedcom import engine
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.rootsmagic.exporter import RootsMagicExporter
from ancestryllm.rootsmagic.reader import RootsMagicReader
from ancestryllm.rootsmagic.service import RootsMagicService


def _policy(kind: FileKind, **changes: int | None) -> FileIngressPolicy:
    defaults = FileIngressLimits()
    selected = dataclasses.replace(getattr(defaults, kind.value), **changes)
    return FileIngressPolicy(dataclasses.replace(defaults, **{kind.value: selected}))


def _write_tree(path: Path, people: int = 2) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE PersonTable (
            PersonID INTEGER PRIMARY KEY,
            Sex TEXT,
            Living INTEGER DEFAULT 0
        );
        CREATE TABLE NameTable (
            NameID INTEGER PRIMARY KEY,
            OwnerID INTEGER,
            Given TEXT,
            Surname TEXT,
            IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable (
            FamilyID INTEGER PRIMARY KEY,
            FatherID INTEGER,
            MotherID INTEGER
        );
        CREATE TABLE ChildTable (
            ChildID INTEGER,
            FamilyID INTEGER
        );
        """
    )
    for person_id in range(1, people + 1):
        connection.execute(
            "INSERT INTO PersonTable(PersonID, Sex, Living) VALUES (?, 'U', 0)",
            (person_id,),
        )
        connection.execute(
            "INSERT INTO NameTable(NameID, OwnerID, Given, Surname, IsPrimary) "
            "VALUES (?, ?, ?, 'Example', 1)",
            (person_id, person_id, f"Fictional{person_id}"),
        )
    connection.commit()
    connection.close()


def _write_person_gedcom(path: Path, pointer: str, given: str) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR Fictional ingress regression",
                "1 GEDC",
                "2 VERS 5.5.5",
                "2 FORM LINEAGE-LINKED",
                "1 CHAR UTF-8",
                f"0 {pointer} INDI",
                f"1 NAME {given} /Example/",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_text_byte_boundaries_use_encoded_bytes_and_allow_shared_concurrent_reads(
    tmp_path: Path,
) -> None:
    policy = _policy(
        FileKind.OCR,
        max_bytes=4,
        max_line_bytes=4,
        max_records=2,
    )
    below = tmp_path / "below.txt"
    below.write_bytes(b"abc")
    exact = tmp_path / "exact.txt"
    exact.write_text("éé", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")

    assert policy.read_text(empty, FileKind.OCR) == ""
    assert policy.read_text(below, FileKind.OCR) == "abc"
    assert policy.read_text(exact, FileKind.OCR) == "éé"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(
            executor.map(
                lambda path: policy.read_text(path, FileKind.OCR),
                (below, exact),
            )
        ) == ["abc", "éé"]


@pytest.mark.parametrize(
    ("kind", "json_document"),
    (
        (FileKind.CONFIG, False),
        (FileKind.GEDCOM, False),
        (FileKind.OCR, False),
        (FileKind.MANIFEST, True),
        (FileKind.JSON_SCHEMA, True),
        (FileKind.PROMPT_BODY, False),
    ),
)
def test_every_text_boundary_accepts_below_and_exact_bytes_then_rejects_one_over(
    tmp_path: Path,
    kind: FileKind,
    json_document: bool,
) -> None:
    maximum = 16
    policy = _policy(kind, max_bytes=maximum)
    base = b"{}" if json_document else b"x"
    below = tmp_path / f"{kind.value}-below"
    exact = tmp_path / f"{kind.value}-exact"
    over = tmp_path / f"{kind.value}-over"
    below.write_bytes(base + b" " * (maximum - len(base) - 1))
    exact.write_bytes(base + b" " * (maximum - len(base)))
    over.write_bytes(base + b" " * (maximum - len(base) + 1))

    if json_document:
        assert policy.read_json(below, kind) == {}
        assert policy.read_json(exact, kind) == {}
    else:
        assert len(policy.read_text(below, kind)) == maximum - 1
        assert len(policy.read_text(exact, kind)) == maximum
    with pytest.raises(FileIngressError) as raised:
        policy.read_json(over, kind) if json_document else policy.read_text(over, kind)
    assert raised.value.code == "FILE_INPUT_TOO_LARGE"


@pytest.mark.parametrize(
    ("payload", "changes", "code"),
    (
        (b"abcde", {"max_bytes": 4}, "FILE_INPUT_TOO_LARGE"),
        ("ééé".encode(), {"max_bytes": 8, "max_line_bytes": 5}, "FILE_LINE_TOO_LONG"),
        (b"a\nb\nc\n", {"max_bytes": 8, "max_records": 2}, "FILE_RECORD_LIMIT_EXCEEDED"),
    ),
)
def test_text_limits_reject_exactly_one_over_without_payload_disclosure(
    tmp_path: Path,
    payload: bytes,
    changes: dict[str, int],
    code: str,
) -> None:
    source = tmp_path / "private-input.txt"
    source.write_bytes(payload)
    policy = _policy(FileKind.OCR, **changes)

    with pytest.raises(FileIngressError) as raised:
        policy.read_text(source, FileKind.OCR)

    assert raised.value.code == code
    rendered = raised.value.render()
    assert str(source) not in rendered
    assert "private-input" not in rendered
    assert payload.decode("utf-8", errors="ignore") not in rendered


def test_gedcom_record_count_record_size_and_nesting_are_independent(
    tmp_path: Path,
) -> None:
    two_records = tmp_path / "two.ged"
    two_records.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    exact = _policy(
        FileKind.GEDCOM,
        max_bytes=64,
        max_line_bytes=32,
        max_records=2,
        max_record_bytes=7,
        max_nesting=2,
        max_collection_items=2,
    )
    assert [record.tag for record in engine.iter_gedcom_records(two_records, exact)] == [
        "HEAD",
        "TRLR",
    ]

    too_many = tmp_path / "three.ged"
    too_many.write_text("0 HEAD\n0 @I1@ INDI\n0 TRLR\n", encoding="utf-8")
    count_policy = _policy(
        FileKind.GEDCOM,
        max_bytes=64,
        max_line_bytes=32,
        max_records=2,
        max_record_bytes=32,
        max_nesting=2,
        max_collection_items=2,
    )
    with pytest.raises(FileIngressError) as count_error:
        list(engine.iter_gedcom_records(too_many, count_policy))
    assert count_error.value.code == "FILE_RECORD_LIMIT_EXCEEDED"

    oversized_record = tmp_path / "record.ged"
    oversized_record.write_text("0 HEAD\n1 NOTE fictional\n0 TRLR\n", encoding="utf-8")
    with pytest.raises(FileIngressError) as record_error:
        list(engine.iter_gedcom_records(oversized_record, exact))
    assert record_error.value.code == "FILE_RECORD_TOO_LARGE"

    nested = tmp_path / "nested.ged"
    nested.write_text("0 HEAD\n3 NOTE fictional\n0 TRLR\n", encoding="utf-8")
    nesting_policy = _policy(
        FileKind.GEDCOM,
        max_bytes=64,
        max_line_bytes=32,
        max_records=3,
        max_record_bytes=64,
        max_nesting=2,
        max_collection_items=4,
    )
    with pytest.raises(FileIngressError) as nesting_error:
        list(engine.iter_gedcom_records(nested, nesting_policy))
    assert nesting_error.value.code == "FILE_NESTING_LIMIT_EXCEEDED"


def test_json_nesting_collection_type_and_syntax_are_bounded(tmp_path: Path) -> None:
    policy = _policy(
        FileKind.JSON_SCHEMA,
        max_bytes=256,
        max_line_bytes=256,
        max_records=4,
        max_nesting=2,
        max_collection_items=2,
    )
    nested = tmp_path / "nested.json"
    nested.write_text('{"a":{"b":{"c":1}}}', encoding="utf-8")
    with pytest.raises(FileIngressError) as nesting_error:
        policy.read_json(nested, FileKind.JSON_SCHEMA)
    assert nesting_error.value.code == "FILE_NESTING_LIMIT_EXCEEDED"

    collection = tmp_path / "collection.json"
    collection.write_text('{"a":1,"b":2,"c":3}', encoding="utf-8")
    with pytest.raises(FileIngressError) as collection_error:
        policy.read_json(collection, FileKind.JSON_SCHEMA)
    assert collection_error.value.code == "FILE_COLLECTION_LIMIT_EXCEEDED"

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(FileIngressError) as type_error:
        policy.read_json(array, FileKind.JSON_SCHEMA, require_object=True)
    assert type_error.value.code == "FILE_JSON_TYPE_INVALID"

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{fictional", encoding="utf-8")
    with pytest.raises(FileIngressError) as syntax_error:
        policy.read_json(malformed, FileKind.JSON_SCHEMA)
    assert syntax_error.value.code == "FILE_JSON_INVALID"
    assert "fictional" not in syntax_error.value.render()


def test_json_nesting_is_bounded_before_recursive_parser_work(tmp_path: Path) -> None:
    policy = _policy(
        FileKind.JSON_SCHEMA,
        max_bytes=4_096,
        max_line_bytes=4_096,
        max_records=2,
        max_nesting=64,
        max_collection_items=1_000,
    )
    deeply_nested = tmp_path / "deep.json"
    deeply_nested.write_text("[" * 1_100 + "]" * 1_100, encoding="utf-8")

    with pytest.raises(FileIngressError) as raised:
        policy.read_json(deeply_nested, FileKind.JSON_SCHEMA)

    assert raised.value.code == "FILE_NESTING_LIMIT_EXCEEDED"

    braces_in_strings = tmp_path / "strings.json"
    braces_in_strings.write_text('{"value":"[[[{{{\\\\\\""}', encoding="utf-8")
    assert policy.read_json(braces_in_strings, FileKind.JSON_SCHEMA) == {"value": '[[[{{{\\"'}


def test_nonregular_archive_unreadable_and_midread_failures_are_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = FileIngressPolicy()
    with pytest.raises(FileIngressError) as directory_error:
        policy.inspect(tmp_path, FileKind.OCR)
    assert directory_error.value.code == "FILE_INPUT_NOT_REGULAR"

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "input.fifo"
        os.mkfifo(fifo)
        with pytest.raises(FileIngressError) as fifo_error:
            policy.inspect(fifo, FileKind.OCR)
        assert fifo_error.value.code == "FILE_INPUT_NOT_REGULAR"
    if os.name == "posix":
        with pytest.raises(FileIngressError) as device_error:
            policy.inspect(Path(os.devnull), FileKind.OCR)
        assert device_error.value.code == "FILE_INPUT_NOT_REGULAR"

    symlink_target = tmp_path / "symlink-target.txt"
    symlink_target.write_text("fictional", encoding="utf-8")
    symlink = tmp_path / "symlink.txt"
    try:
        symlink.symlink_to(symlink_target)
    except OSError:
        pass
    else:
        with pytest.raises(FileIngressError) as symlink_error:
            policy.inspect(symlink, FileKind.OCR)
        assert symlink_error.value.code == "FILE_INPUT_NOT_REGULAR"

    archive = tmp_path / "archive.txt"
    archive.write_bytes(b"PK\x03\x04fictional")
    with pytest.raises(FileIngressError) as archive_error:
        policy.inspect(archive, FileKind.OCR)
    assert archive_error.value.code == "FILE_ARCHIVE_UNSUPPORTED"

    invalid_database = tmp_path / "invalid.rmtree"
    invalid_database.write_bytes(b"fictional database")
    with pytest.raises(FileIngressError) as format_error:
        policy.inspect(invalid_database, FileKind.ROOTSMAGIC)
    assert format_error.value.code == "FILE_FORMAT_INVALID"

    regular = tmp_path / "regular.txt"
    regular.write_text("fictional", encoding="utf-8")
    original_open = os.open

    def denied(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int) -> int:
        if os.fsdecode(path) == str(regular):
            raise PermissionError("private payload must not escape")
        return original_open(path, flags)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(FileIngressError) as unreadable:
        policy.inspect(regular, FileKind.OCR)
    assert unreadable.value.code == "FILE_INPUT_UNREADABLE"
    assert "private payload" not in unreadable.value.render()
    monkeypatch.setattr(os, "open", original_open)

    def fail_after_read(
        _self: FileIngressPolicy,
        _path: str | Path,
        _kind: FileKind,
        _expected: object,
    ) -> None:
        raise OSError("private mid-read payload")

    monkeypatch.setattr(FileIngressPolicy, "assert_unchanged", fail_after_read)
    with pytest.raises(FileIngressError) as io_error:
        policy.read_text(regular, FileKind.OCR)
    assert io_error.value.code == "FILE_INPUT_IO"
    assert "private mid-read payload" not in io_error.value.render()


@pytest.mark.parametrize(
    "kind",
    (
        FileKind.CONFIG,
        FileKind.GEDCOM,
        FileKind.OCR,
        FileKind.MANIFEST,
        FileKind.JSON_SCHEMA,
        FileKind.PROMPT_BODY,
    ),
)
def test_every_text_boundary_maps_midread_io_failure_without_payload_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: FileKind,
) -> None:
    source = tmp_path / f"private-{kind.value}.txt"
    source.write_text("fictional private payload", encoding="utf-8")
    policy = FileIngressPolicy()

    def fail_after_read(
        _path: str | Path,
        _kind: FileKind,
        _expected: FileSnapshot,
    ) -> None:
        raise OSError("fictional private payload")

    monkeypatch.setattr(policy, "assert_unchanged", fail_after_read)
    with pytest.raises(FileIngressError) as raised:
        policy.read_text(source, kind)

    assert raised.value.code == "FILE_INPUT_IO"
    assert str(source) not in raised.value.render()
    assert "fictional private payload" not in raised.value.render()


@pytest.mark.parametrize(
    "kind",
    (
        FileKind.CONFIG,
        FileKind.GEDCOM,
        FileKind.OCR,
        FileKind.MANIFEST,
        FileKind.JSON_SCHEMA,
        FileKind.PROMPT_BODY,
    ),
)
@pytest.mark.parametrize("replace_source", (False, True))
def test_growth_or_replacement_during_consumption_is_rejected(
    tmp_path: Path,
    replace_source: bool,
    kind: FileKind,
) -> None:
    if replace_source and os.name == "nt":
        pytest.skip("Windows does not permit replacing an open source file.")
    source = tmp_path / "source.txt"
    source.write_text("first\nsecond\n", encoding="utf-8")
    policy = FileIngressPolicy()
    lines = policy.iter_text_lines(source, kind)
    assert next(lines) == "first\n"
    if replace_source:
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("replacement\n", encoding="utf-8")
        os.replace(replacement, source)
    else:
        with source.open("a", encoding="utf-8") as handle:
            handle.write("growth\n")

    with pytest.raises(FileIngressError) as raised:
        list(lines)
    assert raised.value.code == "FILE_INPUT_CHANGED"


def test_replacement_between_preflight_and_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        pytest.skip("Windows does not permit replacing the preflight target here.")
    source = tmp_path / "source.txt"
    source.write_text("first\n", encoding="utf-8")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("second\n", encoding="utf-8")
    policy = FileIngressPolicy()
    original_inspect = policy.inspect

    def inspect_then_replace(path: str | Path, kind: FileKind) -> FileSnapshot:
        snapshot = original_inspect(path, kind)
        os.replace(replacement, source)
        return snapshot

    monkeypatch.setattr(policy, "inspect", inspect_then_replace)
    with pytest.raises(FileIngressError) as raised:
        policy.read_text(source, FileKind.OCR)
    assert raised.value.code == "FILE_INPUT_CHANGED"


def test_copy_to_preserves_a_preexisting_destination_sentinel(tmp_path: Path) -> None:
    source = tmp_path / "source.ged"
    destination = tmp_path / "destination.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    sentinel = b"pre-existing destination sentinel\n"
    destination.write_bytes(sentinel)
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)

    with pytest.raises(FileExistsError):
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert destination.read_bytes() == sentinel


@pytest.mark.parametrize("dangling", (False, True))
def test_copy_to_preserves_a_preexisting_destination_symlink(
    tmp_path: Path,
    dangling: bool,
) -> None:
    source = tmp_path / "source.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    sentinel = tmp_path / "sentinel.ged"
    if not dangling:
        sentinel.write_bytes(b"symlink target sentinel\n")
    destination = tmp_path / "destination.ged"
    try:
        destination.symlink_to(sentinel)
    except OSError:
        pytest.skip("Symbolic links are unavailable on this filesystem.")
    link_target = os.readlink(destination)
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)

    with pytest.raises(FileExistsError):
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert destination.is_symlink()
    assert os.readlink(destination) == link_target
    if not dangling:
        assert sentinel.read_bytes() == b"symlink target sentinel\n"


def test_copy_to_removes_only_its_own_failed_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    destination = tmp_path / "destination.ged"
    replacement = tmp_path / "replacement.ged"
    sentinel = b"concurrent replacement sentinel\n"
    replacement.write_bytes(sentinel)
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)

    def replace_destination_then_fail(
        _path: str | Path,
        _kind: FileKind,
        _expected: FileSnapshot,
    ) -> None:
        os.replace(replacement, destination)
        raise FileIngressError(
            "FILE_INPUT_CHANGED",
            "The gedcom input changed while it was being consumed.",
        )

    monkeypatch.setattr(policy, "assert_unchanged", replace_destination_then_fail)

    with pytest.raises(FileIngressError) as raised:
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    assert destination.read_bytes() == sentinel


def test_copy_to_removes_a_failed_destination_created_by_this_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    destination = tmp_path / "destination.ged"
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)

    def fail_verification(
        _path: str | Path,
        _kind: FileKind,
        _expected: FileSnapshot,
    ) -> None:
        raise FileIngressError(
            "FILE_INPUT_CHANGED",
            "The gedcom input changed while it was being consumed.",
        )

    monkeypatch.setattr(policy, "assert_unchanged", fail_verification)

    with pytest.raises(FileIngressError) as raised:
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    assert not destination.exists()
    assert not destination.is_symlink()


def test_config_file_is_bounded_and_only_toml_can_override_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    config_path.write_text(
        (f'[storage]\ndata_dir = "{data_dir}"\n[file_ingress.ocr]\nmax_bytes = 42\n'),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANCESTRYLLM_OCR_MAX_BYTES", "1")

    config = AppConfig.load(config_path)

    assert config.file_ingress.ocr.max_bytes == 42
    assert config.file_ingress.gedcom == FileIngressLimits().gedcom
    config.save()
    assert AppConfig.load(config_path).file_ingress.ocr.max_bytes == 42

    oversized_config = tmp_path / "oversized.toml"
    oversized_config.write_bytes(b"x" * (FileIngressLimits().config.max_bytes + 1))
    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(oversized_config)
    assert raised.value.code == "FILE_INPUT_TOO_LARGE"


def test_empty_config_file_is_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "empty.toml"
    config_path.write_bytes(b"")
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(tmp_path / "data"))

    config = AppConfig.load(config_path)

    assert config.file_ingress == FileIngressLimits()


def test_explicit_dangling_config_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    try:
        config_path.symlink_to(tmp_path / "missing.toml")
    except OSError:
        pytest.skip("Symbolic links are unavailable on this filesystem.")
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(tmp_path / "data"))

    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(config_path)

    assert raised.value.code == "FILE_INPUT_NOT_REGULAR"


def test_deeply_nested_config_is_a_stable_bounded_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "deep.toml"
    config_path.write_text("value = " + "[" * 1_100 + "0" + "]" * 1_100, encoding="utf-8")
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(tmp_path / "data"))

    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(config_path)

    assert raised.value.code == "FILE_NESTING_LIMIT_EXCEEDED"
    assert str(config_path) not in raised.value.render()

    quoted_brackets = tmp_path / "quoted.toml"
    quoted_brackets.write_text(
        'basic = "[[[{{{\\\\\\""\nliteral = \']]]}}}\'\ncommented = 1 # [[[]]]\n',
        encoding="utf-8",
    )
    assert AppConfig.load(quoted_brackets).file_ingress == FileIngressLimits()


def test_gedcom_rejection_is_offline_and_preserves_existing_output(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ged"
    first.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    second = tmp_path / "second.ged"
    second.write_text("0 HEAD\n1 NOTE fictional private payload\n0 TRLR\n", encoding="utf-8")
    output = tmp_path / "existing.ged"
    sentinel = b"fictional sentinel\n"
    output.write_bytes(sentinel)
    llm = Mock()
    policy = _policy(
        FileKind.GEDCOM,
        max_bytes=128,
        max_line_bytes=16,
        max_records=10,
        max_record_bytes=64,
        max_nesting=10,
        max_collection_items=10,
    )

    with pytest.raises(FileIngressError) as raised:
        GedcomService(llm, policy).merge([first, second], output)

    assert raised.value.code == "FILE_LINE_TOO_LONG"
    assert output.read_bytes() == sentinel
    llm.generate.assert_not_called()


def test_rootsmagic_byte_and_row_bounds_apply_before_output(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "fictional.rmtree"
    _write_tree(tree, people=3)
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_text("GEDCOM sentinel\n", encoding="utf-8")
    report.write_text("report sentinel\n", encoding="utf-8")
    defaults = FileIngressLimits()
    row_limit = dataclasses.replace(
        defaults.rootsmagic,
        max_bytes=tree.stat().st_size,
        max_records=2,
        max_collection_items=10,
    )
    reader = RootsMagicReader(
        [tmp_path],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=row_limit)),
    )

    assert reader.list_trees() == [tree]
    with pytest.raises(FileIngressError) as rows_error:
        RootsMagicExporter(reader).export(tree, output, report_path=report)
    assert rows_error.value.code == "FILE_RECORD_LIMIT_EXCEEDED"
    assert output.read_text(encoding="utf-8") == "GEDCOM sentinel\n"
    assert report.read_text(encoding="utf-8") == "report sentinel\n"

    byte_limit = dataclasses.replace(defaults.rootsmagic, max_bytes=tree.stat().st_size - 1)
    bounded_reader = RootsMagicReader(
        [tmp_path],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=byte_limit)),
    )
    with pytest.raises(FileIngressError) as bytes_error:
        bounded_reader.resolve_tree(tree)
    assert bytes_error.value.code == "FILE_INPUT_TOO_LARGE"


def test_rootsmagic_exact_row_limit_is_valid(tmp_path: Path) -> None:
    tree = tmp_path / "exact.rmtree"
    _write_tree(tree, people=2)
    defaults = FileIngressLimits()
    selected = dataclasses.replace(
        defaults.rootsmagic,
        max_bytes=tree.stat().st_size,
        max_records=2,
        max_collection_items=10,
    )
    reader = RootsMagicReader(
        [tmp_path],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=selected)),
    )

    assert len(reader.read_table(tree, "PersonTable")) == 2


def test_rootsmagic_cross_volume_path_check_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "cross-volume.rmtree"
    _write_tree(tree)
    reader = RootsMagicReader([tmp_path])

    def different_volumes(_paths: tuple[str, str]) -> str:
        raise ValueError("Paths are on different drives")

    monkeypatch.setattr(os.path, "commonpath", different_volumes)

    with pytest.raises(AncestryError) as raised:
        reader.resolve_tree(tree)

    assert raised.value.code == "ROOTSMAGIC_TREE_NOT_FOUND"


@pytest.mark.parametrize("operation", ("list", "query", "export"))
def test_every_rootsmagic_entry_point_enforces_the_byte_preflight(
    operation: str, tmp_path: Path
) -> None:
    tree = tmp_path / "oversized.rmtree"
    _write_tree(tree)
    defaults = FileIngressLimits()
    selected = dataclasses.replace(defaults.rootsmagic, max_bytes=tree.stat().st_size - 1)
    reader = RootsMagicReader(
        [tmp_path],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=selected)),
    )
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_text("output sentinel\n", encoding="utf-8")
    report.write_text("report sentinel\n", encoding="utf-8")

    with pytest.raises(FileIngressError) as raised:
        if operation == "list":
            reader.list_trees()
        elif operation == "query":
            reader.query(tree, "SELECT PersonID FROM PersonTable")
        else:
            RootsMagicExporter(reader).export(tree, output, report_path=report)

    assert raised.value.code == "FILE_INPUT_TOO_LARGE"
    assert output.read_text(encoding="utf-8") == "output sentinel\n"
    assert report.read_text(encoding="utf-8") == "report sentinel\n"


def test_rootsmagic_row_rejection_precedes_natural_language_provider_call(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "rows.rmtree"
    _write_tree(tree, people=3)
    defaults = FileIngressLimits()
    selected = dataclasses.replace(
        defaults.rootsmagic,
        max_bytes=tree.stat().st_size,
        max_records=2,
        max_collection_items=10,
    )
    llm = Mock()
    service = RootsMagicService(
        AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path / "data",
            family_tree_dirs=[tmp_path],
            file_ingress=dataclasses.replace(defaults, rootsmagic=selected),
        ),
        llm,
    )

    with pytest.raises(FileIngressError) as raised:
        service.query_question(
            tree,
            "Who is in the fictional tree?",
            provider_id="fictional",
            model="fictional-model",
        )

    assert raised.value.code == "FILE_RECORD_LIMIT_EXCEEDED"
    llm.generate.assert_not_called()


def test_rootsmagic_replacement_before_natural_language_provider_call_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "race.rmtree"
    _write_tree(tree)
    llm = Mock()
    service = RootsMagicService(
        AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path / "data",
            family_tree_dirs=[tmp_path],
        ),
        llm,
    )
    validate_row_limits = service.reader.validate_row_limits

    def mutate_after_row_validation(
        path: Path,
        schema: dict[str, tuple[str, ...]],
        expected: FileSnapshot | None = None,
    ) -> None:
        validate_row_limits(path, schema, expected)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE PersonTable SET Sex = 'F' WHERE PersonID = 1")
        connection.commit()
        connection.close()

    monkeypatch.setattr(service.reader, "validate_row_limits", mutate_after_row_validation)

    with pytest.raises(FileIngressError) as raised:
        service.query_question(
            tree,
            "Who is in the fictional tree?",
            provider_id="fictional",
            model="fictional-model",
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    llm.generate.assert_not_called()


@pytest.mark.parametrize("alias", ("first_input", "second_input", "primary_output"))
def test_merge_quality_report_rejects_every_input_or_output_alias_before_writing(
    tmp_path: Path,
    alias: str,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output.write_text("primary sentinel\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (first, second, output)}
    report = {
        "first_input": first,
        "second_input": second,
        "primary_output": output,
    }[alias]

    with pytest.raises(AncestryError) as raised:
        GedcomService().merge(
            [first, second],
            output,
            root_person="@I1@",
            quality_path=report,
        )

    assert raised.value.code == "GEDCOM_REPORT_ALIAS"
    assert {path: path.read_bytes() for path in (first, second, output)} == before


def test_quality_report_rejects_hardlink_alias_to_immutable_input(tmp_path: Path) -> None:
    source = tmp_path / "source.ged"
    report = tmp_path / "report.md"
    _write_person_gedcom(source, "@I1@", "Ada")
    try:
        os.link(source, report)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem.")
    before = source.read_bytes()

    with pytest.raises(AncestryError) as raised:
        GedcomService().quality(source, report, root_person="@I1@")

    assert raised.value.code == "GEDCOM_REPORT_ALIAS"
    assert source.read_bytes() == before
    assert report.read_bytes() == before


def test_merge_report_rejects_hardlink_alias_to_primary_output(tmp_path: Path) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "report.md"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output.write_text("primary sentinel\n", encoding="utf-8")
    try:
        os.link(output, report)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem.")

    with pytest.raises(AncestryError) as raised:
        GedcomService().merge(
            [first, second],
            output,
            root_person="@I1@",
            quality_path=report,
        )

    assert raised.value.code == "GEDCOM_REPORT_ALIAS"
    assert output.read_text(encoding="utf-8") == "primary sentinel\n"
    assert report.read_text(encoding="utf-8") == "primary sentinel\n"


def test_merge_bundle_rolls_back_both_existing_outputs_when_report_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "quality.md"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output.write_text("primary sentinel\n", encoding="utf-8")
    report.write_text("report sentinel\n", encoding="utf-8")
    original_replace = os.replace
    failed = False

    def fail_first_report_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(destination) == report.resolve() and not failed:
            failed = True
            raise OSError("fictional second-artifact failure")
        original_replace(source, destination)

    monkeypatch.setattr(gedcom_engine.os, "replace", fail_first_report_replace)

    with pytest.raises(OSError, match="second-artifact failure"):
        GedcomService().merge(
            [first, second],
            output,
            root_person="@I1@",
            quality_path=report,
        )

    assert output.read_text(encoding="utf-8") == "primary sentinel\n"
    assert report.read_text(encoding="utf-8") == "report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize(
    ("report_target", "expected_code"),
    (
        ("source", "EXPORT_REPORT_ALIAS"),
        ("output", "EXPORT_REPORT_ALIAS"),
    ),
)
def test_rootsmagic_report_rejects_source_and_primary_output_aliases(
    tmp_path: Path,
    report_target: str,
    expected_code: str,
) -> None:
    tree = tmp_path / "tree.rmtree"
    output = tmp_path / "output.ged"
    _write_tree(tree)
    output.write_text("primary sentinel\n", encoding="utf-8")
    report = tree if report_target == "source" else output
    tree_before = tree.read_bytes()

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )

    assert raised.value.code == expected_code
    assert tree.read_bytes() == tree_before
    assert output.read_text(encoding="utf-8") == "primary sentinel\n"


@pytest.mark.parametrize("aliased_target", ("source", "output"))
def test_rootsmagic_report_rejects_hardlink_aliases(
    tmp_path: Path,
    aliased_target: str,
) -> None:
    tree = tmp_path / "tree.rmtree"
    output = tmp_path / "output.ged"
    report = tmp_path / "report.md"
    _write_tree(tree)
    output.write_text("primary sentinel\n", encoding="utf-8")
    target = tree if aliased_target == "source" else output
    try:
        os.link(target, report)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem.")
    tree_before = tree.read_bytes()

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )

    assert raised.value.code == "EXPORT_REPORT_ALIAS"
    assert tree.read_bytes() == tree_before
    assert output.read_text(encoding="utf-8") == "primary sentinel\n"


def test_rootsmagic_bundle_rolls_back_existing_sentinels_on_second_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "tree.rmtree"
    output = tmp_path / "output.ged"
    report = tmp_path / "report.md"
    _write_tree(tree)
    output.write_text("primary sentinel\n", encoding="utf-8")
    report.write_text("report sentinel\n", encoding="utf-8")
    original_replace = os.replace
    failed = False

    def fail_first_report_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(destination) == report.resolve() and not failed:
            failed = True
            raise OSError("fictional report publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(exporter_module.os, "replace", fail_first_report_replace)

    with pytest.raises(OSError, match="report publication failure"):
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )

    assert output.read_text(encoding="utf-8") == "primary sentinel\n"
    assert report.read_text(encoding="utf-8") == "report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_rootsmagic_late_source_change_restores_existing_sentinels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "tree.rmtree"
    output = tmp_path / "output.ged"
    report = tmp_path / "report.md"
    _write_tree(tree)
    output.write_text("primary sentinel\n", encoding="utf-8")
    report.write_text("report sentinel\n", encoding="utf-8")
    reader = RootsMagicReader([tmp_path])
    real_read_table = reader.read_table

    def mutate_after_final_table(path: Path, table_name: str) -> list[dict[str, object]]:
        rows = real_read_table(path, table_name)
        if table_name == "ChildTable":
            connection = sqlite3.connect(path)
            connection.execute("UPDATE PersonTable SET Sex = 'F' WHERE PersonID = 1")
            connection.commit()
            connection.close()
        return rows

    monkeypatch.setattr(reader, "read_table", mutate_after_final_table)

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(reader).export(
            tree,
            output,
            report_path=report,
        )

    assert raised.value.code == "ROOTSMAGIC_FILE_CHANGED"
    assert output.read_text(encoding="utf-8") == "primary sentinel\n"
    assert report.read_text(encoding="utf-8") == "report sentinel\n"


@pytest.mark.parametrize(
    ("aggregate_budget", "accepted"),
    ((3, False), (4, True), (5, True)),
)
def test_rootsmagic_total_row_budget_is_aggregate_as_well_as_per_table(
    tmp_path: Path,
    aggregate_budget: int,
    accepted: bool,
) -> None:
    tree = tmp_path / "aggregate.rmtree"
    _write_tree(tree, people=2)
    defaults = FileIngressLimits()
    selected = dataclasses.replace(
        defaults.rootsmagic,
        max_bytes=tree.stat().st_size,
        max_records=aggregate_budget,
        max_collection_items=10,
    )
    reader = RootsMagicReader(
        [tmp_path],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=selected)),
    )
    schema = reader.schema(tree)

    if accepted:
        reader.validate_row_limits(tree, schema)
    else:
        with pytest.raises(FileIngressError) as raised:
            reader.validate_row_limits(tree, schema)
        assert raised.value.code == "FILE_RECORD_LIMIT_EXCEEDED"
