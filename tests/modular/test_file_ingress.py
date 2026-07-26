"""Deterministic adversarial coverage for the public file-ingress policy."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sqlite3
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import ancestryllm.core.ingress as ingress_module
import ancestryllm.core.publication as publication_module
import ancestryllm.gedcom.engine as gedcom_engine
import ancestryllm.gedcom.service as gedcom_service_module
import ancestryllm.rootsmagic.exporter as exporter_module
from ancestryllm.core.config import AppConfig
from ancestryllm.core.errors import AncestryError, ConfigurationError, FileIngressError
from ancestryllm.core.ingress import (
    FileFingerprint,
    FileIngressLimits,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
)
from ancestryllm.core.publication import paths_alias, publish_staged_bundle, staging_path
from ancestryllm.gedcom import engine
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.rootsmagic.exporter import RootsMagicExporter
from ancestryllm.rootsmagic.reader import RootsMagicReader
from ancestryllm.rootsmagic.service import RootsMagicService


def _policy(kind: FileKind, **changes: int | None) -> FileIngressPolicy:
    defaults = FileIngressLimits()
    selected = dataclasses.replace(getattr(defaults, kind.value), **changes)
    return FileIngressPolicy(dataclasses.replace(defaults, **{kind.value: selected}))


def _staged_bytes(target: Path, payload: bytes) -> Path:
    staged = staging_path(target)
    token = publication_module.write_staged_bytes(staged, payload)
    publication_module.claim_staged_path(staged, token)
    return staged


def _simulated_windows_handle_unlink(path: Path, expected: Any) -> bool:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        opened = publication_module._PathIdentity.from_stat(os.fstat(descriptor))
        if not expected.pristine(opened):
            return False
        publication_module._NATIVE_UNLINK(path)
        return True
    finally:
        os.close(descriptor)


def _simulated_windows_prepared_commit(prepared: Any, target: Path) -> None:
    if os.path.lexists(target):
        raise FileExistsError(target)
    publication_module._NATIVE_REPLACE(prepared.candidate.path, target)


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


@pytest.mark.parametrize("continuation_tag", ("CONC", "CONT"))
def test_gedcom_continuations_count_toward_the_logical_record_byte_limit(
    tmp_path: Path,
    continuation_tag: str,
) -> None:
    record_prefix = b"0 @I1@ INDI\n1 NOTE fictional\n"
    source = tmp_path / f"{continuation_tag.casefold()}-continuation.ged"
    source.write_bytes(
        record_prefix + f"2 {continuation_tag} continuation\n".encode() + b"0 TRLR\n"
    )
    policy = _policy(
        FileKind.GEDCOM,
        max_bytes=256,
        max_line_bytes=64,
        max_records=2,
        max_record_bytes=len(record_prefix),
        max_nesting=3,
        max_collection_items=4,
    )

    with pytest.raises(FileIngressError) as raised:
        list(engine.iter_gedcom_records(source, policy))

    assert raised.value.code == "FILE_RECORD_TOO_LARGE"


def test_gedcom_streaming_never_uses_whole_file_path_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "streamed.ged"
    _write_person_gedcom(source, "@I1@", "Ada")
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes

    def reject_source_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == source:
            raise AssertionError("GEDCOM ingress must not read the whole file as text")
        return original_read_text(path, encoding=encoding, errors=errors)

    def reject_source_read_bytes(path: Path) -> bytes:
        if path == source:
            raise AssertionError("GEDCOM ingress must not read the whole file as bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_text", reject_source_read_text)
    monkeypatch.setattr(Path, "read_bytes", reject_source_read_bytes)

    assert [record.tag for record in engine.iter_gedcom_records(source)] == [
        "HEAD",
        "INDI",
        "TRLR",
    ]


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


def test_json_and_toml_scalar_leaves_do_not_consume_nesting_budget(
    tmp_path: Path,
) -> None:
    json_policy = _policy(
        FileKind.JSON_SCHEMA,
        max_bytes=256,
        max_line_bytes=256,
        max_records=4,
        max_nesting=1,
        max_collection_items=4,
    )
    one_container = tmp_path / "one-container.json"
    one_container.write_text('{"value":1}', encoding="utf-8")

    assert json_policy.read_json(one_container, FileKind.JSON_SCHEMA) == {"value": 1}

    nested_container = tmp_path / "two-containers.json"
    nested_container.write_text('{"value":[1]}', encoding="utf-8")
    with pytest.raises(FileIngressError) as nested_error:
        json_policy.read_json(nested_container, FileKind.JSON_SCHEMA)
    assert nested_error.value.code == "FILE_NESTING_LIMIT_EXCEEDED"

    toml_policy = _policy(
        FileKind.CONFIG,
        max_bytes=256,
        max_line_bytes=256,
        max_records=4,
        max_nesting=1,
        max_collection_items=4,
    )
    toml_policy.validate_toml_nesting("values = [1]\n")
    toml_policy.validate_structure(
        {"values": [1]},
        FileKind.CONFIG,
        root_container_implicit=True,
    )

    with pytest.raises(FileIngressError) as toml_nested_error:
        toml_policy.validate_structure(
            {"values": [[1]]},
            FileKind.CONFIG,
            root_container_implicit=True,
        )
    assert toml_nested_error.value.code == "FILE_NESTING_LIMIT_EXCEEDED"


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


def test_initial_buffered_read_failure_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "private-initial-read.txt"
    source.write_text("fictional payload\n", encoding="utf-8")

    def fail_initial_read(_self, _buffer) -> int:
        raise OSError("PRIVATE initial buffered read failure")

    monkeypatch.setattr(ingress_module._BoundedRawReader, "readinto", fail_initial_read)

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_text(source, FileKind.OCR)

    assert raised.value.code == "FILE_INPUT_IO"
    assert "PRIVATE" not in raised.value.render()
    assert str(source) not in raised.value.render()


def test_unsafe_input_path_spellings_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = FileIngressPolicy()
    with pytest.raises(FileIngressError) as nul_error:
        policy.inspect(Path("PRIVATE\x00input.txt"), FileKind.OCR)
    assert nul_error.value.code == "FILE_INPUT_UNREADABLE"
    assert "PRIVATE" not in nul_error.value.render()

    private_path = Path("~PRIVATE-NONEXISTENT/input.txt")
    original_expanduser = Path.expanduser

    def reject_private_user(path: Path) -> Path:
        if path == private_path:
            raise RuntimeError("PRIVATE user lookup failure")
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", reject_private_user)
    with pytest.raises(FileIngressError) as user_error:
        policy.sha256(private_path, FileKind.OCR)

    assert user_error.value.code == "FILE_INPUT_UNREADABLE"
    assert "PRIVATE" not in user_error.value.render()


@pytest.mark.parametrize(
    "unreliable_inode",
    (0, -1),
)
def test_unreliable_file_identity_fails_before_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unreliable_inode: int,
) -> None:
    source = tmp_path / "PRIVATE-unreliable-identity.txt"
    source.write_text("fictional payload\n", encoding="utf-8")
    actual = os.lstat(source)
    unreliable = Mock(
        st_mode=actual.st_mode,
        st_dev=actual.st_dev,
        st_ino=actual.st_ino,
        st_size=actual.st_size,
        st_mtime_ns=actual.st_mtime_ns,
        st_ctime_ns=actual.st_ctime_ns,
        st_nlink=actual.st_nlink,
    )
    unreliable.st_ino = unreliable_inode
    monkeypatch.setattr(ingress_module.os, "lstat", lambda _path: unreliable)
    open_file = Mock(side_effect=AssertionError("input must not be opened"))
    monkeypatch.setattr(ingress_module.os, "open", open_file)

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().inspect(source, FileKind.OCR)

    assert raised.value.code == "FILE_INPUT_UNREADABLE"
    assert raised.value.exit_code == 2
    assert raised.value.details == {"input_class": "ocr"}
    assert "PRIVATE" not in raised.value.render()
    assert str(source) not in raised.value.render()
    open_file.assert_not_called()


@pytest.mark.parametrize("unreliable_inode", (0, -1))
def test_unreliable_opened_identity_fails_before_prefix_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unreliable_inode: int,
) -> None:
    source = tmp_path / "PRIVATE-unreliable-opened-identity.txt"
    source.write_text("fictional payload\n", encoding="utf-8")
    original_fstat = ingress_module.os.fstat

    def unreliable_fstat(descriptor: int) -> Mock:
        actual = original_fstat(descriptor)
        return Mock(
            st_mode=actual.st_mode,
            st_dev=actual.st_dev,
            st_ino=unreliable_inode,
            st_size=actual.st_size,
            st_mtime_ns=actual.st_mtime_ns,
            st_ctime_ns=actual.st_ctime_ns,
            st_nlink=actual.st_nlink,
        )

    monkeypatch.setattr(ingress_module.os, "fstat", unreliable_fstat)
    read_file = Mock(side_effect=AssertionError("input bytes must not be consumed"))
    monkeypatch.setattr(ingress_module.os, "read", read_file)

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().inspect(source, FileKind.OCR)

    assert raised.value.code == "FILE_INPUT_UNREADABLE"
    assert raised.value.exit_code == 2
    assert "PRIVATE" not in raised.value.render()
    assert str(source) not in raised.value.render()
    read_file.assert_not_called()


def test_zero_device_identity_remains_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "supported-device-zero.txt"
    source.write_text("fictional payload\n", encoding="utf-8")
    original_lstat = ingress_module.os.lstat
    original_fstat = ingress_module.os.fstat

    def with_zero_device(value: os.stat_result) -> Mock:
        return Mock(
            st_mode=value.st_mode,
            st_dev=0,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns,
            st_ctime_ns=value.st_ctime_ns,
            st_nlink=value.st_nlink,
        )

    monkeypatch.setattr(
        ingress_module.os,
        "lstat",
        lambda path: with_zero_device(original_lstat(path)),
    )
    monkeypatch.setattr(
        ingress_module.os,
        "fstat",
        lambda descriptor: with_zero_device(original_fstat(descriptor)),
    )

    snapshot = FileIngressPolicy().inspect(source, FileKind.OCR)

    assert snapshot.device == 0
    assert snapshot.inode > 0


@pytest.mark.parametrize("operation", ("merge", "subtree", "quality"))
@pytest.mark.parametrize("failure_stage", ("expanduser", "resolve"))
def test_gedcom_services_use_sanitized_path_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure_stage: str,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output = tmp_path / "PRIVATE-output.ged"
    output.write_bytes(b"sentinel\n")
    private_input = Path("~PRIVATE-NONEXISTENT/tree.ged")
    selected_input = private_input if failure_stage == "expanduser" else first
    private_detail = "PRIVATE path normalization failure"
    if failure_stage == "expanduser":
        original_expanduser = Path.expanduser

        def reject_private_user(path: Path) -> Path:
            if path == private_input:
                raise RuntimeError(private_detail)
            return original_expanduser(path)

        monkeypatch.setattr(Path, "expanduser", reject_private_user)
    else:
        original_resolve = Path.resolve

        def reject_private_output(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> Path:
            if path == output:
                raise OSError(private_detail)
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", reject_private_output)
    llm = Mock()
    service = GedcomService(llm)

    with pytest.raises(FileIngressError) as raised:
        if operation == "merge":
            service.merge([selected_input, second], output)
        elif operation == "subtree":
            service.subtree(selected_input, output, root_person="@I1@")
        else:
            service.quality(selected_input, output, root_person="@I1@")

    assert raised.value.code == "FILE_INPUT_UNREADABLE"
    assert raised.value.exit_code == 2
    assert "PRIVATE" not in raised.value.render()
    assert private_detail not in raised.value.render()
    assert output.read_bytes() == b"sentinel\n"
    llm.generate.assert_not_called()


@pytest.mark.parametrize("failure_stage", ("expanduser", "resolve"))
def test_rootsmagic_tree_resolution_uses_sanitized_path_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    reader = RootsMagicReader([tmp_path])
    private_input = Path("~PRIVATE-NONEXISTENT/tree.rmtree")
    private_detail = "PRIVATE path normalization failure"
    requested: str | Path
    if failure_stage == "expanduser":
        original_expanduser = Path.expanduser

        def reject_private_user(path: Path) -> Path:
            if path == private_input:
                raise RuntimeError(private_detail)
            return original_expanduser(path)

        monkeypatch.setattr(Path, "expanduser", reject_private_user)
        requested = private_input
    else:
        candidate = tmp_path / "private-relative.rmtree"
        original_resolve = Path.resolve

        def reject_private_candidate(
            path: Path,
            *args: object,
            **kwargs: object,
        ) -> Path:
            if path == candidate:
                raise OSError(private_detail)
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", reject_private_candidate)
        requested = "private-relative"

    with pytest.raises(FileIngressError) as raised:
        reader.resolve_tree(requested)

    assert raised.value.code == "FILE_INPUT_UNREADABLE"
    assert raised.value.exit_code == 2
    assert "PRIVATE" not in raised.value.render()
    assert private_detail not in raised.value.render()


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


def test_copy_to_normalizes_source_read_failures_and_removes_its_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    destination = tmp_path / "destination.ged"
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)

    def fail_chunks(_handle, _kind):
        yield b"partial"
        raise OSError("PRIVATE source read failure")

    monkeypatch.setattr(policy, "_bounded_chunks", fail_chunks)

    with pytest.raises(FileIngressError) as raised:
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert raised.value.code == "FILE_INPUT_IO"
    assert "PRIVATE" not in raised.value.render()
    assert not destination.exists()


def test_copy_failure_cleanup_preserves_a_last_moment_destination_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    source.write_bytes(b"0 HEAD\n0 TRLR\n")
    destination = tmp_path / "destination.ged"
    replacement = tmp_path / "concurrent-destination.ged"
    sentinel = b"concurrent replacement sentinel\n"
    replacement.write_bytes(sentinel)
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)
    original_replace = publication_module._NATIVE_REPLACE
    raced = False

    def fail_chunks(_handle, _kind):
        yield b"partial"
        raise OSError("fictional source read failure")

    def swap_during_cleanup(source_path: str | Path, target_path: str | Path) -> None:
        nonlocal raced
        if Path(source_path) == destination and not raced:
            original_replace(replacement, destination)
            raced = True
        original_replace(source_path, target_path)

    monkeypatch.setattr(policy, "_bounded_chunks", fail_chunks)
    monkeypatch.setattr(publication_module, "_NATIVE_REPLACE", swap_during_cleanup)

    with pytest.raises(FileIngressError) as raised:
        policy.copy_to(
            source,
            destination,
            FileKind.GEDCOM,
            expected=fingerprint,
        )

    assert raised.value.code == "FILE_INPUT_IO"
    assert raced
    assert destination.read_bytes() == sentinel


def test_copy_cleanup_uses_an_open_descriptor_with_zero_inode_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "destination.ged"
    destination.write_bytes(b"concurrent sentinel\n")
    with destination.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        current = os.lstat(destination)
        zero_opened = os.stat_result(
            (
                opened.st_mode,
                0,
                opened.st_dev,
                opened.st_nlink,
                opened.st_uid,
                opened.st_gid,
                opened.st_size,
                opened.st_atime,
                opened.st_mtime,
                opened.st_ctime,
            )
        )
        zero_current = os.stat_result(
            (
                current.st_mode,
                0,
                current.st_dev,
                current.st_nlink,
                current.st_uid,
                current.st_gid,
                current.st_size,
                current.st_atime,
                current.st_mtime,
                current.st_ctime,
            )
        )
        monkeypatch.setattr(ingress_module.os, "fstat", lambda _descriptor: zero_opened)
        monkeypatch.setattr(ingress_module.os, "lstat", lambda _path: zero_current)

        publication_module.cleanup_open_path(destination, handle.fileno())

    assert not destination.exists()


def test_copy_to_retries_short_unbuffered_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.ged"
    payload = b"0 HEAD\n0 TRLR\n"
    source.write_bytes(payload)
    destination = tmp_path / "destination.ged"
    policy = FileIngressPolicy()
    fingerprint = policy.fingerprint(source, FileKind.GEDCOM)
    original_open = Path.open

    class ShortWriter:
        def __init__(self, handle) -> None:
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.handle.close()

        def fileno(self) -> int:
            return self.handle.fileno()

        def flush(self) -> None:
            self.handle.flush()

        def write(self, value) -> int:
            return self.handle.write(value[:1])

    def short_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        handle = original_open(path, mode, *args, **kwargs)
        if path == destination and mode == "xb":
            return ShortWriter(handle)
        return handle

    monkeypatch.setattr(Path, "open", short_open)

    policy.copy_to(
        source,
        destination,
        FileKind.GEDCOM,
        expected=fingerprint,
    )

    assert destination.read_bytes() == payload


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
        '[modules]\nenabled = ["[[[{{{\\\\\\"", \']]]}}}\'] # [[[]]]\n',
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


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity", "1e400"))
def test_json_rejects_every_nonfinite_number_as_invalid_json(
    tmp_path: Path,
    constant: str,
) -> None:
    source = tmp_path / "manifest.json"
    source.write_text(f'{{"value": {constant}}}', encoding="utf-8")

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_json(source, FileKind.MANIFEST)

    assert raised.value.code == "FILE_JSON_INVALID"
    assert constant not in raised.value.render()
    assert str(source) not in raised.value.render()


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
def test_every_text_boundary_rejects_embedded_nul_without_disclosure(
    tmp_path: Path,
    kind: FileKind,
) -> None:
    source = tmp_path / f"private-{kind.value}.txt"
    source.write_bytes(b"fictional\x00private\n")

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_text(source, kind)

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "fictional" not in raised.value.render()
    assert str(source) not in raised.value.render()


@pytest.mark.parametrize(
    "payload",
    (
        '{"value": "fictional\\u0000PRIVATE"}',
        '{"fictional\\u0000PRIVATE": "value"}',
    ),
)
def test_json_rejects_escaped_nul_in_keys_and_values(
    tmp_path: Path,
    payload: str,
) -> None:
    source = tmp_path / "private-manifest.json"
    source.write_text(payload, encoding="utf-8")

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_json(source, FileKind.MANIFEST)

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "PRIVATE" not in raised.value.render()
    assert str(source) not in raised.value.render()


def test_config_rejects_escaped_nul_outside_path_fields(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[modules]\nenabled = ["gedcom", "\\u0000PRIVATE"]\n',
        encoding="utf-8",
    )

    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(config)

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "PRIVATE" not in raised.value.render()


def test_raced_regular_file_to_fifo_open_is_nonblocking_and_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("Nonblocking FIFOs are unavailable on this platform.")
    source = tmp_path / "raced.txt"
    source.write_text("fictional\n", encoding="utf-8")
    original_open = os.open
    replaced = False

    def replace_with_fifo(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
    ) -> int:
        nonlocal replaced
        if Path(path) == source and not replaced:
            replaced = True
            source.unlink()
            os.mkfifo(source)
            assert flags & os.O_NONBLOCK
        return original_open(path, flags)

    monkeypatch.setattr(os, "open", replace_with_fifo)

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().inspect(source, FileKind.OCR)

    assert raised.value.code == "FILE_INPUT_NOT_REGULAR"


@pytest.mark.parametrize("operation", ("text", "sha256", "copy"))
def test_growth_past_the_byte_budget_is_rejected_by_actual_bytes(
    tmp_path: Path,
    operation: str,
) -> None:
    source = tmp_path / "growing.txt"
    source.write_bytes(b"first\n")
    destination = tmp_path / "copy.txt"
    policy = _policy(FileKind.OCR, max_bytes=7, max_line_bytes=7, max_records=10)

    if operation == "text":
        lines = policy.iter_text_lines(source, FileKind.OCR)
        assert next(lines) == "first\n"
        source.write_bytes(b"first\nxx")
        consume = partial(list, lines)
    else:
        fingerprint = FileIngressPolicy().fingerprint(source, FileKind.OCR)

        class GrowingPolicy(FileIngressPolicy):
            def _bounded_chunks(self, handle, kind):  # type: ignore[no-untyped-def]
                for chunk in super()._bounded_chunks(handle, kind):
                    yield chunk
                    if source.stat().st_size == 6:
                        with source.open("ab") as output:
                            output.write(b"xx")

        selected = GrowingPolicy(policy.limits)
        if operation == "sha256":
            consume = partial(selected.sha256, source, FileKind.OCR)
        else:
            consume = partial(
                selected.copy_to,
                source,
                destination,
                FileKind.OCR,
                expected=fingerprint,
            )

    with pytest.raises(FileIngressError) as raised:
        consume()

    assert raised.value.code == "FILE_INPUT_TOO_LARGE"
    if operation == "copy":
        assert not destination.exists()


@pytest.mark.parametrize(
    ("payload", "kind"),
    (
        (b"\xef\xbb\xbf0 HEAD\n", FileKind.GEDCOM),
        (b"\xff\xfe" + "0 HEAD\n".encode("utf-16-le"), FileKind.GEDCOM),
        (b"\xfe\xff" + "0 HEAD\n".encode("utf-16-be"), FileKind.GEDCOM),
    ),
)
def test_bom_bytes_count_toward_total_first_line_and_gedcom_record_limits(
    tmp_path: Path,
    payload: bytes,
    kind: FileKind,
) -> None:
    source = tmp_path / "bom.ged"
    source.write_bytes(payload)
    exact = _policy(
        kind,
        max_bytes=len(payload),
        max_line_bytes=len(payload),
        max_records=1,
        max_record_bytes=len(payload),
        max_nesting=1,
        max_collection_items=1,
    )

    line = next(exact.iter_text_line_items(source, kind, count_lines_as_records=False))
    assert line.text == "0 HEAD\n"
    assert line.byte_count == len(payload)
    assert [record.tag for record in engine.iter_gedcom_records(source, exact)] == ["HEAD"]

    line_limited = _policy(
        kind,
        max_bytes=len(payload),
        max_line_bytes=len(payload) - 1,
        max_records=1,
        max_record_bytes=len(payload),
        max_nesting=1,
        max_collection_items=1,
    )
    with pytest.raises(FileIngressError) as line_error:
        list(line_limited.iter_text_lines(source, kind))
    assert line_error.value.code == "FILE_LINE_TOO_LONG"

    total_limited = _policy(
        kind,
        max_bytes=len(payload) - 1,
        max_line_bytes=len(payload),
        max_records=1,
        max_record_bytes=len(payload),
        max_nesting=1,
        max_collection_items=1,
    )
    with pytest.raises(FileIngressError) as total_error:
        list(engine.iter_gedcom_records(source, total_limited))
    assert total_error.value.code == "FILE_INPUT_TOO_LARGE"

    record_limited = _policy(
        kind,
        max_bytes=len(payload),
        max_line_bytes=len(payload),
        max_records=1,
        max_record_bytes=len(payload) - 1,
        max_nesting=1,
        max_collection_items=1,
    )
    with pytest.raises(FileIngressError) as record_error:
        list(engine.iter_gedcom_records(source, record_limited))
    assert record_error.value.code == "FILE_RECORD_TOO_LARGE"


@pytest.mark.parametrize(
    "payload",
    (
        b"\xff",
        b"\xff\xfe\x00",
        b"\xff\xfe\x00\x00fictional",
        b"\x00\x00\xfe\xfffictional",
    ),
)
def test_truncated_or_unsupported_boms_have_a_stable_encoding_error(
    tmp_path: Path,
    payload: bytes,
) -> None:
    source = tmp_path / "invalid-bom.ged"
    source.write_bytes(payload)

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_text(source, FileKind.GEDCOM)

    assert raised.value.code == "FILE_ENCODING_INVALID"


@pytest.mark.parametrize(
    "kind",
    (
        FileKind.CONFIG,
        FileKind.OCR,
        FileKind.MANIFEST,
        FileKind.JSON_SCHEMA,
        FileKind.PROMPT_BODY,
    ),
)
def test_utf16_is_only_accepted_for_gedcom(
    tmp_path: Path,
    kind: FileKind,
) -> None:
    source = tmp_path / f"{kind.value}.txt"
    source.write_bytes(b"\xff\xfe" + "fictional\n".encode("utf-16-le"))

    with pytest.raises(FileIngressError) as raised:
        FileIngressPolicy().read_text(source, kind)

    assert raised.value.code == "FILE_ENCODING_INVALID"


@pytest.mark.parametrize(
    "payload",
    (
        "[limits]\nmax_query_rows = []\n",
        "[limits]\nprovider_timeout_seconds = nan\n",
        f"[limits]\nprovider_timeout_seconds = {10**400}\n",
        "[storage]\nfamily_tree_dirs = 1\n",
        '[modules]\nenabled = "gedcom"\n',
        "[providers]\ndefault = 1\n",
        '[file_ingress.ocr]\nmax_bytes = "large"\n',
        f"[file_ingress.ocr]\nmax_line_bytes = {sys.maxsize}\n",
        '[unsupported]\nvalue = "ignored"\n',
    ),
)
def test_wrong_config_semantic_types_are_sanitized_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(payload, encoding="utf-8")
    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))

    with pytest.raises(ConfigurationError) as raised:
        AppConfig.load(config)

    assert raised.value.code == "CONFIG_INVALID"
    assert not data_dir.exists()
    assert payload.strip() not in raised.value.render()


def test_unknown_file_ingress_names_are_not_retained_in_error_details(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[file_ingress."PRIVATE-SECRET-CLASS"]\nmax_bytes = 1\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as raised:
        AppConfig.load(config)

    assert raised.value.code == "CONFIG_INVALID"
    assert raised.value.details == {"unknown_count": 1}
    assert "PRIVATE-SECRET-CLASS" not in str(raised.value.details)


@pytest.mark.parametrize(
    ("variable", "uses_explicit_config"),
    (
        ("ANCESTRYLLM_CONFIG_DIR", False),
        ("ANCESTRYLLM_DATA_DIR", True),
    ),
)
def test_selected_environment_paths_are_normalized_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    uses_explicit_config: bool,
) -> None:
    private_path = tmp_path / "private-environment-path"
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    original_resolve = Path.resolve

    def reject_private_path(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if path == private_path:
            raise RuntimeError("PRIVATE environment path failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_private_path)
    monkeypatch.setenv(variable, str(private_path))

    with pytest.raises(ConfigurationError) as raised:
        AppConfig.load(config if uses_explicit_config else None)

    assert raised.value.code == "CONFIG_INVALID"
    assert str(private_path) not in raised.value.render()
    assert "PRIVATE" not in raised.value.render()
    assert not private_path.exists()


def test_explicit_config_ignores_invalid_environment_fallbacks_when_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_config_fallback = tmp_path / "private-config-fallback"
    private_data_fallback = tmp_path / "private-data-fallback"
    selected_data = tmp_path / "selected-data"
    config = tmp_path / "config.toml"
    config.write_text(f'[storage]\ndata_dir = "{selected_data}"\n', encoding="utf-8")
    original_resolve = Path.resolve

    def reject_irrelevant_fallbacks(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if path in {private_config_fallback, private_data_fallback}:
            raise RuntimeError("irrelevant fallback must not be resolved")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_irrelevant_fallbacks)
    monkeypatch.setenv("ANCESTRYLLM_CONFIG_DIR", str(private_config_fallback))
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(private_data_fallback))

    loaded = AppConfig.load(config)

    assert loaded.config_path == config
    assert loaded.data_dir == selected_data
    assert not private_config_fallback.exists()
    assert not private_data_fallback.exists()


def test_explicit_missing_config_is_not_treated_as_first_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-config.toml"
    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))

    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(missing)

    assert raised.value.code == "FILE_INPUT_UNREADABLE"
    assert str(missing) not in raised.value.render()
    assert not missing.exists()
    assert not data_dir.exists()


def test_explicit_config_never_changes_its_parent_directory_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory modes are unavailable on Windows.")
    config_dir = tmp_path / "shared-config"
    config_dir.mkdir(mode=0o755)
    config_dir.chmod(0o755)
    config = config_dir / "config.toml"
    data_dir = tmp_path / "private-data"
    config.write_text(
        f'[storage]\ndata_dir = "{data_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ANCESTRYLLM_CONFIG_DIR", raising=False)
    before = config_dir.stat().st_mode & 0o777

    selected = AppConfig.load(config)
    after_load = config_dir.stat().st_mode & 0o777
    selected.save()
    after_save = config_dir.stat().st_mode & 0o777

    assert before == after_load == after_save == 0o755


@pytest.mark.parametrize(
    "payload",
    (
        '[storage]\ndata_dir = "\\u0000private-data"\n',
        '[storage]\nfamily_tree_dirs = ["\\u0000private-tree"]\n',
    ),
)
def test_configured_path_nul_is_rejected_by_the_typed_ingress_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(payload, encoding="utf-8")
    data_dir = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(data_dir))

    with pytest.raises(FileIngressError) as raised:
        AppConfig.load(config)

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "private" not in raised.value.render()
    assert not data_dir.exists()


@pytest.mark.parametrize("field_name", ("data_dir", "family_tree_dirs"))
def test_configured_path_resolution_failure_is_normalized_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    configured = tmp_path / "private-unresolvable"
    config = tmp_path / "config.toml"
    if field_name == "data_dir":
        payload = f'[storage]\ndata_dir = "{configured}"\n'
    else:
        payload = f'[storage]\nfamily_tree_dirs = ["{configured}"]\n'
    config.write_text(payload, encoding="utf-8")
    fallback_data = tmp_path / "must-not-exist"
    monkeypatch.setenv("ANCESTRYLLM_DATA_DIR", str(fallback_data))
    original_resolve = Path.resolve

    def reject_configured_path(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if path == configured:
            raise OSError("private path resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_configured_path)

    with pytest.raises(ConfigurationError) as raised:
        AppConfig.load(config)

    assert raised.value.code == "CONFIG_INVALID"
    assert str(configured) not in raised.value.render()
    assert "private path resolution failure" not in raised.value.render()
    assert not fallback_data.exists()


@pytest.mark.parametrize("payload", (b"", b" \r\n\t\n"))
def test_empty_or_whitespace_gedcom_is_rejected_offline_without_replacing_output(
    tmp_path: Path,
    payload: bytes,
) -> None:
    empty = tmp_path / "private-empty.ged"
    empty.write_bytes(payload)
    valid = tmp_path / "valid.ged"
    _write_person_gedcom(valid, "@I1@", "Ada")
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")
    llm = Mock()

    with pytest.raises(FileIngressError) as raised:
        GedcomService(llm).merge([empty, valid], output)

    assert raised.value.code == "FILE_INPUT_EMPTY"
    assert output.read_bytes() == b"sentinel\n"
    llm.generate.assert_not_called()


def test_malformed_gedcom_service_error_omits_path_and_raw_line(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "private-malformed.ged"
    private_line = "PRIVATE-PAYLOAD malformed genealogy"
    malformed.write_text(f"0 HEAD\n{private_line}\n0 TRLR\n", encoding="utf-8")
    valid = tmp_path / "valid.ged"
    _write_person_gedcom(valid, "@I1@", "Ada")
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")

    with pytest.raises(AncestryError) as raised:
        GedcomService().merge([malformed, valid], output)

    assert raised.value.code == "GEDCOM_PARSE_INVALID"
    assert private_line not in raised.value.render()
    assert str(malformed) not in raised.value.render()
    assert output.read_bytes() == b"sentinel\n"


class _MutateOnSecondVerifyPolicy(FileIngressPolicy):
    def __init__(self, target: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.verifications = 0

    def verify(
        self,
        path: str | Path,
        kind: FileKind,
        expected: FileFingerprint,
    ) -> None:
        if Path(path).absolute() == self.target:
            self.verifications += 1
            if self.verifications == 2:
                with self.target.open("ab") as handle:
                    handle.write(b"0 NOTE changed after parse\n")
        super().verify(path, kind, expected)


@pytest.mark.parametrize("operation", ("merge", "subtree", "quality"))
def test_gedcom_source_change_after_parse_rolls_back_every_service_output(
    tmp_path: Path,
    operation: str,
) -> None:
    source = tmp_path / "source.ged"
    _write_person_gedcom(source, "@I1@", "Ada")
    other = tmp_path / "other.ged"
    _write_person_gedcom(other, "@I2@", "Grace")
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")
    service = GedcomService(ingress=_MutateOnSecondVerifyPolicy(source))

    with pytest.raises(FileIngressError) as raised:
        if operation == "merge":
            service.merge([source, other], output)
        elif operation == "subtree":
            service.subtree(source, output, root_person="@I1@")
        else:
            service.quality(source, output, root_person="@I1@")

    assert raised.value.code == "FILE_INPUT_CHANGED"
    assert output.read_bytes() == b"sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_gedcom_source_change_after_parse_is_rejected_before_provider_call(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    first.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME John /Smith/\n1 BIRT\n2 DATE 1850\n"
        "2 PLAC Boston, Massachusetts, USA\n0 TRLR\n",
        encoding="utf-8",
    )
    second.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I2@ INDI\n1 NAME John /Smyth/\n1 BIRT\n2 DATE 1851\n"
        "2 PLAC Boston, Massachusetts, USA\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")
    llm = Mock()

    with pytest.raises(FileIngressError) as raised:
        GedcomService(
            llm,
            ingress=_MutateOnSecondVerifyPolicy(first),
        ).merge(
            [first, second],
            output,
            provider_id="fictional",
            model="fixture",
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    llm.generate.assert_not_called()
    assert output.read_bytes() == b"sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_gedcom_change_during_provider_call_is_detected_before_publication(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    first.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME John /Smith/\n1 BIRT\n2 DATE 1850\n"
        "2 PLAC Boston, Massachusetts, USA\n0 TRLR\n",
        encoding="utf-8",
    )
    second.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I2@ INDI\n1 NAME John /Smyth/\n1 BIRT\n2 DATE 1851\n"
        "2 PLAC Boston, Massachusetts, USA\n0 TRLR\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")
    llm = Mock()

    def mutate_during_generate(_request: object, _consent: object) -> Mock:
        with first.open("ab") as handle:
            handle.write(b"0 NOTE changed during provider call\n")
        return Mock(parsed={}, provider_id="fictional", model="fixture")

    llm.generate.side_effect = mutate_during_generate

    with pytest.raises(FileIngressError) as raised:
        GedcomService(llm).merge(
            [first, second],
            output,
            provider_id="fictional",
            model="fixture",
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    assert llm.generate.call_count == 1
    assert output.read_bytes() == b"sentinel\n"


def test_casefold_colliding_bundle_destinations_are_rejected_portably(
    tmp_path: Path,
) -> None:
    output = tmp_path / "Tree.ged"
    report = tmp_path / "tree.GED"

    assert paths_alias(output, report)

    tree = tmp_path / "tree.rmtree"
    _write_tree(tree)
    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )
    assert raised.value.code == "EXPORT_REPORT_ALIAS"
    assert not output.exists()
    assert not report.exists()


def test_staging_path_keeps_an_exclusive_owner_only_placeholder(tmp_path: Path) -> None:
    reserved = staging_path(tmp_path / "output.ged")

    assert reserved.is_file()
    assert reserved.stat().st_mode & 0o777 == 0o600
    assert publication_module.cleanup_staged_path(reserved)


def test_first_claim_requires_the_atomic_writers_identity_token(tmp_path: Path) -> None:
    target = tmp_path / "output.ged"
    staged = staging_path(target)
    replacement = tmp_path / "replacement.ged"
    replacement.write_bytes(b"untrusted replacement\n")
    os.replace(replacement, staged)

    with pytest.raises(OSError, match="identity token is required"):
        publication_module.claim_staged_path(staged)

    assert staged.read_bytes() == b"untrusted replacement\n"
    assert not publication_module.cleanup_staged_path(staged)
    staged.unlink()


def test_atomic_writer_token_claims_only_its_descriptor_content(tmp_path: Path) -> None:
    target = tmp_path / "output.ged"
    staged = staging_path(target)
    token = publication_module.write_staged_bytes(
        staged,
        b"verified writer output\n",
    )

    publication_module.claim_staged_path(staged, token)

    assert publication_module.cleanup_staged_path(staged)


@pytest.mark.parametrize("writer_kind", ("gedcom", "rootsmagic"))
def test_reserved_writer_never_clobbers_a_foreign_staging_replacement(
    tmp_path: Path,
    writer_kind: str,
) -> None:
    output = tmp_path / "output.ged"
    staged = staging_path(output)
    replacement_payload = b"concurrent stage replacement\n"
    replacement = tmp_path / "concurrent-writer.ged"
    replacement.write_bytes(replacement_payload)
    os.replace(replacement, staged)

    with pytest.raises(OSError, match="reservation was replaced"):
        if writer_kind == "gedcom":
            gedcom_engine._atomic_write_text(staged, "writer payload\n")
        else:
            RootsMagicExporter._atomic_write(staged, "writer payload\n")

    assert staged.read_bytes() == replacement_payload
    assert not publication_module.cleanup_staged_path(staged)
    staged.unlink()


def test_zero_inode_staging_fails_early_without_leaking_its_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_from_stat = publication_module._PathIdentity.from_stat.__func__

    def zero_inode(
        cls: type[Any],
        value: os.stat_result,
    ) -> Any:
        return dataclasses.replace(original_from_stat(cls, value), inode=0)

    monkeypatch.setattr(
        publication_module._PathIdentity,
        "from_stat",
        classmethod(zero_inode),
    )

    with pytest.raises(OSError, match="reliable publication identities"):
        staging_path(tmp_path / "output.ged")

    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("writer_kind", ("gedcom", "rootsmagic"))
@pytest.mark.parametrize("failure_phase", ("write", "fsync", "close"))
def test_reserved_writers_retry_identity_cleanup_after_lifecycle_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_kind: str,
    failure_phase: str,
) -> None:
    output = tmp_path / "output.txt"
    staged = staging_path(output)
    original_write = publication_module.os.write
    original_close = publication_module.os.close
    writes = 0
    close_failed = False

    def fail_write(descriptor: int, payload: Any) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return original_write(descriptor, memoryview(payload)[:1])
        raise OSError("fictional write failure")

    def fail_close_once(descriptor: int) -> None:
        nonlocal close_failed
        if not close_failed:
            close_failed = True
            original_close(descriptor)
            raise OSError("fictional close failure")
        original_close(descriptor)

    monkeypatch.setattr(
        publication_module,
        "cleanup_open_path",
        lambda _path, _descriptor: False,
    )
    if failure_phase == "write":
        monkeypatch.setattr(publication_module.os, "write", fail_write)
    if failure_phase == "fsync":
        monkeypatch.setattr(
            publication_module.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("fictional fsync failure")),
        )
    if failure_phase == "close":
        monkeypatch.setattr(publication_module.os, "close", fail_close_once)

    with pytest.raises(OSError, match=f"fictional {failure_phase} failure"):
        if writer_kind == "gedcom":
            gedcom_engine._atomic_write_text(staged, "fictional payload\n")
        else:
            RootsMagicExporter._atomic_write(staged, "fictional payload\n")

    assert not output.exists()
    assert not staged.exists()
    assert not list(tmp_path.iterdir())


def test_copy_only_install_rejects_same_size_restored_mtime_source_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    sealed = staged.stat()
    original_open = publication_module.os.open
    original_close = publication_module.os.close
    source_opens = 0

    def mutate_before_copy_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal source_opens
        if Path(path) == staged and flags & os.O_ACCMODE == os.O_RDONLY:
            source_opens += 1
            if source_opens == 2:
                descriptor = original_open(staged, os.O_WRONLY | os.O_TRUNC)
                try:
                    os.write(descriptor, b"bad\n")
                finally:
                    original_close(descriptor)
                os.utime(staged, ns=(sealed.st_atime_ns, sealed.st_mtime_ns))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publication_module.os, "open", mutate_before_copy_open)

    with pytest.raises(OSError, match="publication source changed"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"old\n"
    assert staged.read_bytes() == b"bad\n"
    staged.unlink()


@pytest.mark.parametrize("interrupt_after_install", (1, 2))
def test_keyboard_interrupt_rolls_back_each_bundle_install_boundary(
    tmp_path: Path,
    interrupt_after_install: int,
) -> None:
    first_target = tmp_path / "first.ged"
    second_target = tmp_path / "second.md"
    first_target.write_bytes(b"old first\n")
    second_target.write_bytes(b"old second\n")
    first_staged = _staged_bytes(first_target, b"new first\n")
    second_staged = _staged_bytes(second_target, b"new second\n")
    installs = 0

    def interrupt_boundary(source: str | Path, destination: str | Path) -> None:
        nonlocal installs
        if Path(source) == Path(destination):
            installs += 1
            if installs == interrupt_after_install:
                raise KeyboardInterrupt
            return
        os.replace(source, destination)

    with pytest.raises(KeyboardInterrupt):
        publish_staged_bundle(
            (
                (first_staged, first_target),
                (second_staged, second_target),
            ),
            replace=interrupt_boundary,
        )

    assert first_target.read_bytes() == b"old first\n"
    assert second_target.read_bytes() == b"old second\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_quarantine_discard_does_not_use_a_public_check_then_path_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = tmp_path / "owned.ged"
    owned.write_bytes(b"owned\n")
    expected = publication_module._identity(owned)
    path_unlinks: list[Path] = []
    original_path_unlink = Path.unlink

    def race_public_unlink(path: Path, missing_ok: bool = False) -> None:
        path_unlinks.append(path)
        replacement = tmp_path / "replacement.ged"
        replacement.write_bytes(b"replacement\n")
        os.replace(replacement, path)
        original_path_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", race_public_unlink)

    assert publication_module._unlink_if_owned(owned, expected)
    assert path_unlinks == []
    assert not owned.exists()


def test_simulated_windows_cleanup_uses_handle_deletion_without_opening_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = tmp_path / "owned.ged"
    owned.write_bytes(b"owned\n")
    expected = publication_module._identity(owned)
    original_open = publication_module.os.open
    directory_open_attempts: list[Path] = []
    handle_deletes: list[Path] = []

    def reject_windows_directory_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        selected = Path(path)
        if selected.name.startswith(".ancestry-publish-quarantine-"):
            directory_open_attempts.append(selected)
            raise PermissionError("Windows does not open directories through os.open")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def delete_through_verified_handle(path: Path, identity: Any) -> bool:
        handle_deletes.append(path)
        return _simulated_windows_handle_unlink(path, identity)

    monkeypatch.setattr(
        publication_module,
        "_supports_directory_fd_cleanup",
        lambda: False,
    )
    monkeypatch.setattr(publication_module.os, "open", reject_windows_directory_open)
    monkeypatch.setattr(
        publication_module,
        "_unlink_without_directory_fd",
        delete_through_verified_handle,
    )
    assert publication_module._unlink_if_owned(owned, expected)
    assert directory_open_attempts == []
    assert len(handle_deletes) == 1
    assert not owned.exists()
    assert not list(tmp_path.glob(".ancestry-publish-quarantine-*"))


def test_simulated_windows_rollback_restores_outputs_without_recovery_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_target = tmp_path / "first.ged"
    second_target = tmp_path / "second.md"
    first_target.write_bytes(b"old first\n")
    second_target.write_bytes(b"old second\n")
    first_staged = _staged_bytes(first_target, b"new first\n")
    second_staged = _staged_bytes(second_target, b"new second\n")
    handle_deletes: list[Path] = []

    def delete_through_verified_handle(path: Path, identity: Any) -> bool:
        handle_deletes.append(path)
        return _simulated_windows_handle_unlink(path, identity)

    monkeypatch.setattr(
        publication_module,
        "_supports_directory_fd_cleanup",
        lambda: False,
    )
    monkeypatch.setattr(
        publication_module,
        "_unlink_without_directory_fd",
        delete_through_verified_handle,
    )
    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        _simulated_windows_prepared_commit,
    )

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            (
                (first_staged, first_target),
                (second_staged, second_target),
            ),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert handle_deletes
    assert first_target.read_bytes() == b"old first\n"
    assert second_target.read_bytes() == b"old second\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_simulated_windows_cleanup_revalidates_move_before_handle_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = tmp_path / "owned.ged"
    replacement = tmp_path / "concurrent.ged"
    owned.write_bytes(b"owned\n")
    replacement.write_bytes(b"concurrent\n")
    expected = publication_module._identity(owned)
    original_replace = publication_module._NATIVE_REPLACE
    handle_deletes: list[Path] = []
    raced = False

    def replace_before_quarantine(source: str | Path, destination: str | Path) -> None:
        nonlocal raced
        if Path(source) == owned and not raced:
            original_replace(replacement, owned)
            raced = True
        original_replace(source, destination)

    def delete_restored_quarantine_copy(path: Path, identity: Any) -> bool:
        handle_deletes.append(path)
        return _simulated_windows_handle_unlink(path, identity)

    monkeypatch.setattr(
        publication_module,
        "_supports_directory_fd_cleanup",
        lambda: False,
    )
    monkeypatch.setattr(publication_module, "_NATIVE_REPLACE", replace_before_quarantine)
    monkeypatch.setattr(
        publication_module,
        "_unlink_without_directory_fd",
        delete_restored_quarantine_copy,
    )
    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        _simulated_windows_prepared_commit,
    )

    assert not publication_module._unlink_if_owned(owned, expected)
    assert raced
    assert owned.read_bytes() == b"concurrent\n"
    assert len(handle_deletes) == 1
    assert handle_deletes[0].parent.name.startswith(".ancestry-publish-quarantine-")
    assert not list(tmp_path.glob(".ancestry-publish-quarantine-*"))


def test_simulated_windows_writer_retries_cleanup_after_closing_shared_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output.ged"
    staged = staging_path(output)
    original_open = publication_module.os.open
    original_close = publication_module.os.close
    original_fsync = publication_module.os.fsync
    original_replace = publication_module._NATIVE_REPLACE
    writer_descriptors: set[int] = set()
    blocked_moves = 0

    def track_writer_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == staged and flags & os.O_ACCMODE == os.O_RDWR:
            writer_descriptors.add(descriptor)
        return descriptor

    def track_writer_close(descriptor: int) -> None:
        original_close(descriptor)
        writer_descriptors.discard(descriptor)

    def fail_writer_fsync(descriptor: int) -> None:
        if descriptor in writer_descriptors:
            raise OSError("fictional Windows write failure")
        original_fsync(descriptor)

    def deny_move_while_shared(source: str | Path, destination: str | Path) -> None:
        nonlocal blocked_moves
        if Path(source) == staged and writer_descriptors:
            blocked_moves += 1
            raise PermissionError("fictional Windows sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(
        publication_module,
        "_supports_directory_fd_cleanup",
        lambda: False,
    )
    monkeypatch.setattr(
        publication_module,
        "_unlink_without_directory_fd",
        _simulated_windows_handle_unlink,
    )
    monkeypatch.setattr(publication_module.os, "open", track_writer_open)
    monkeypatch.setattr(publication_module.os, "close", track_writer_close)
    monkeypatch.setattr(publication_module.os, "fsync", fail_writer_fsync)
    monkeypatch.setattr(publication_module, "_NATIVE_REPLACE", deny_move_while_shared)

    with pytest.raises(OSError, match="Windows write failure"):
        publication_module.write_staged_bytes(staged, b"fictional payload\n")

    assert blocked_moves == 1
    assert writer_descriptors == set()
    assert not staged.exists()
    assert not publication_module.is_staging_path(staged)
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_simulated_windows_copy_failure_retries_cleanup_after_descriptor_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"fictional payload\n")
    original_open = publication_module.os.open
    original_close = publication_module.os.close
    original_fsync = publication_module.os.fsync
    original_replace = publication_module._NATIVE_REPLACE
    destination_descriptors: dict[int, Path] = {}
    blocked_moves = 0

    def track_destination_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        selected = Path(path)
        if (
            selected.name == "owned"
            and selected.parent.name.startswith(".ancestry-publish-quarantine-")
            and flags & os.O_CREAT
        ):
            destination_descriptors[descriptor] = selected
        return descriptor

    def track_destination_close(descriptor: int) -> None:
        original_close(descriptor)
        destination_descriptors.pop(descriptor, None)

    def fail_destination_fsync(descriptor: int) -> None:
        if descriptor in destination_descriptors:
            raise OSError("fictional Windows copy failure")
        original_fsync(descriptor)

    def deny_move_while_shared(source: str | Path, destination: str | Path) -> None:
        nonlocal blocked_moves
        if Path(source) in destination_descriptors.values():
            blocked_moves += 1
            raise PermissionError("fictional Windows sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(
        publication_module,
        "_supports_directory_fd_cleanup",
        lambda: False,
    )
    monkeypatch.setattr(
        publication_module,
        "_unlink_without_directory_fd",
        _simulated_windows_handle_unlink,
    )
    monkeypatch.setattr(publication_module.os, "open", track_destination_open)
    monkeypatch.setattr(publication_module.os, "close", track_destination_close)
    monkeypatch.setattr(publication_module.os, "fsync", fail_destination_fsync)
    monkeypatch.setattr(publication_module, "_NATIVE_REPLACE", deny_move_while_shared)

    with pytest.raises(OSError, match="Windows copy failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert blocked_moves == 1
    assert destination_descriptors == {}
    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_quarantine_identity_lookup_error_does_not_abort_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_identity = publication_module._identity
    failed = False
    quarantine_lookups = 0

    def fail_first_quarantine_identity(path: Path) -> Any:
        nonlocal failed, quarantine_lookups
        if path.parent.name.startswith(".ancestry-publish-quarantine-"):
            quarantine_lookups += 1
            if quarantine_lookups == 3 and not failed:
                failed = True
                raise OSError("fictional quarantine lookup failure")
        return original_identity(path)

    monkeypatch.setattr(publication_module, "_identity", fail_first_quarantine_identity)

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert failed
    assert target.read_bytes() == b"old\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_common_writer_and_publication_work_without_birthtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_from_stat = publication_module._PathIdentity.from_stat.__func__

    def no_birthtime(
        cls: type[Any],
        value: os.stat_result,
    ) -> Any:
        return dataclasses.replace(original_from_stat(cls, value), created_ns=None)

    monkeypatch.setattr(
        publication_module._PathIdentity,
        "from_stat",
        classmethod(no_birthtime),
    )
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"portable identity\n")

    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"portable identity\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_portable_rollback_preserves_mode_and_timestamps_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    target.chmod(0o755)
    timestamp_ns = 1_600_000_000_123_456_789
    os.utime(target, ns=(timestamp_ns, timestamp_ns))
    staged = _staged_bytes(target, b"new\n")
    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(RuntimeError, match="validation failure"):
            publish_staged_bundle(
                ((staged, target),),
                replace=os.replace,
                validate_after=lambda: (_ for _ in ()).throw(
                    RuntimeError("fictional validation failure")
                ),
            )
    finally:
        os.umask(previous_umask)

    restored = target.stat()
    assert target.read_bytes() == b"old\n"
    assert restored.st_mode & 0o777 == 0o755
    assert restored.st_atime_ns == timestamp_ns
    assert restored.st_mtime_ns == timestamp_ns
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_rollback_prefers_backup_metadata_over_read_mutated_displacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "output.ged"
    source_path = tmp_path / "staged.ged"
    backup_path = tmp_path / ".ancestry-publish-backup-fictional"
    displaced_path = tmp_path / ".ancestry-publish-displaced-fictional"
    old_payload = b"old\n"
    new_payload = b"new\n"
    source_path.write_bytes(new_payload)
    backup_path.write_bytes(old_payload)
    displaced_path.write_bytes(old_payload)
    backup_path.chmod(0o755)
    displaced_path.chmod(0o755)
    original_timestamp_ns = 1_600_000_000_123_456_789
    read_mutated_timestamp_ns = 1_700_000_000_123_456_789
    os.utime(
        backup_path,
        ns=(original_timestamp_ns, original_timestamp_ns),
    )
    os.utime(
        displaced_path,
        ns=(read_mutated_timestamp_ns, original_timestamp_ns),
    )
    old_digest = hashlib.sha256(old_payload).digest()
    artifact = publication_module._Artifact(
        source=publication_module._OwnedPath(
            source_path,
            publication_module._identity(source_path),
            hashlib.sha256(new_payload).digest(),
        ),
        target=target,
        original_target=publication_module._identity(displaced_path),
        backup=publication_module._OwnedPath(
            backup_path,
            publication_module._identity(backup_path),
            old_digest,
        ),
        displaced=publication_module._OwnedPath(
            displaced_path,
            publication_module._identity(displaced_path),
            old_digest,
        ),
        displacement_attempted=True,
    )

    assert publication_module._rollback_bundle([artifact]) is None

    restored = target.stat()
    assert restored.st_mode & 0o777 == 0o755
    assert restored.st_atime_ns == original_timestamp_ns
    assert restored.st_mtime_ns == original_timestamp_ns
    assert target.read_bytes() == old_payload
    assert not source_path.exists()
    assert not backup_path.exists()
    assert not displaced_path.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_private_candidate_digest_verification_requests_noatime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = tmp_path / "owned"
    payload = b"new\n"
    candidate_path.write_bytes(payload)
    candidate = publication_module._OwnedPath(
        candidate_path,
        publication_module._identity(candidate_path),
        hashlib.sha256(payload).digest(),
    )
    prepared = publication_module._PreparedRegularInstall(tmp_path / "output.ged")
    original_open = publication_module.os.open
    noatime = getattr(publication_module.os, "O_NOATIME", 1 << 29)
    observed_flags: list[int] = []
    monkeypatch.setattr(publication_module.os, "O_NOATIME", noatime, raising=False)
    monkeypatch.setattr(publication_module, "_PLATFORM", "linux")

    def record_candidate_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        selected = Path(path)
        if selected == candidate_path and flags & os.O_ACCMODE == os.O_RDONLY:
            observed_flags.append(flags)
        return original_open(path, flags & ~noatime, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publication_module.os, "open", record_candidate_open)

    publication_module._open_verified_install_candidate(candidate, prepared)

    try:
        assert observed_flags
        assert all(flags & noatime for flags in observed_flags)
    finally:
        assert prepared.descriptor is not None
        os.close(prepared.descriptor)


def test_backup_destination_close_failure_removes_the_untracked_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_open = publication_module.os.open
    original_close = publication_module.os.close
    backup_descriptors: set[int] = set()
    failed = False

    def record_backup_descriptor(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path).name.startswith(".ancestry-publish-backup-") and flags & os.O_EXCL:
            backup_descriptors.add(descriptor)
        return descriptor

    def fail_backup_close(descriptor: int) -> None:
        nonlocal failed
        if descriptor in backup_descriptors and not failed:
            failed = True
            original_close(descriptor)
            raise OSError("fictional destination close failure")
        original_close(descriptor)

    monkeypatch.setattr(publication_module.os, "open", record_backup_descriptor)
    monkeypatch.setattr(publication_module.os, "close", fail_backup_close)

    with pytest.raises(OSError, match="destination close failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert failed
    assert target.read_bytes() == b"old\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_backup_copy_race_cannot_overwrite_a_symlink_referent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"victim\n")
    raced_paths: list[Path] = []
    original_open = publication_module.os.open

    def race_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        selected = Path(path)
        if (
            selected.name.startswith(".ancestry-publish-backup-")
            and flags & os.O_EXCL
            and not raced_paths
        ):
            raced = selected
            raced.symlink_to(victim)
            raced_paths.append(raced)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publication_module.os, "open", race_open)
    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"new\n"
    assert victim.read_bytes() == b"victim\n"
    assert raced_paths[0].is_symlink()
    raced_paths[0].unlink()


def test_backup_cleanup_failure_does_not_report_a_committed_bundle_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    retained: list[Path] = []
    original_cleanup = publication_module._unlink_if_owned

    def fail_backup_cleanup(path: Path, expected: Any, **kwargs: Any) -> bool:
        if path.name.startswith(".ancestry-publish-backup-"):
            retained.append(path)
            return False
        return original_cleanup(path, expected, **kwargs)

    monkeypatch.setattr(publication_module, "_unlink_if_owned", fail_backup_cleanup)
    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"new\n"
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"old\n"
    retained[0].unlink(missing_ok=True)


def test_swapped_staging_source_is_never_published_or_deleted(tmp_path: Path) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    replacement = tmp_path / "concurrent-stage.ged"
    replacement.write_bytes(b"concurrent\n")
    original_replace = os.replace
    swapped = False

    def swap_before_install(source: str | Path, destination: str | Path) -> None:
        nonlocal swapped
        if not swapped and Path(source) == target:
            original_replace(replacement, staged)
            swapped = True
        original_replace(source, destination)

    with pytest.raises(OSError, match="changed"):
        publish_staged_bundle(((staged, target),), replace=swap_before_install)

    assert target.read_bytes() == b"old\n"
    assert staged.read_bytes() == b"concurrent\n"
    staged.unlink()


def test_target_replaced_after_backup_is_preserved_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    replacement = tmp_path / "concurrent-target.ged"
    replacement.write_bytes(b"concurrent\n")
    backups: list[Any] = []
    original_backup = publication_module._backup_target

    def backup_then_replace(artifact: Any) -> None:
        original_backup(artifact)
        backup = artifact.backup
        assert backup is not None
        backups.append(backup)
        os.replace(replacement, artifact.target)

    monkeypatch.setattr(publication_module, "_backup_target", backup_then_replace)

    with pytest.raises(OSError, match="concurrent replacement"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    backup_path = backups[0].path
    assert target.read_bytes() == b"concurrent\n"
    assert backup_path.read_bytes() == b"old\n"
    backup_path.unlink()


def test_target_replaced_before_rollback_is_preserved_with_recovery_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    replacement = tmp_path / "concurrent-target.ged"
    replacement.write_bytes(b"concurrent\n")

    def replace_then_fail() -> None:
        os.replace(replacement, target)
        raise RuntimeError("fictional validation failure")

    with pytest.raises(OSError, match="concurrent replacement"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=replace_then_fail,
        )

    recovery = list(tmp_path.glob(".ancestry-publish-backup-*")) + list(
        tmp_path.glob(".ancestry-publish-displaced-*")
    )
    assert target.read_bytes() == b"concurrent\n"
    assert len(recovery) == 2
    assert all(path.read_bytes() == b"old\n" for path in recovery)
    for path in recovery:
        path.unlink()


@pytest.mark.parametrize("fail_validation", (False, True))
def test_replaced_backup_is_never_restored_or_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_validation: bool,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    backups: list[Any] = []
    original_backup = publication_module._backup_target

    def capture_backup(artifact: Any) -> None:
        original_backup(artifact)
        backup = artifact.backup
        assert backup is not None
        backups.append(backup)

    monkeypatch.setattr(publication_module, "_backup_target", capture_backup)

    def replace_backup() -> None:
        backup_path = backups[0].path
        replacement = tmp_path / "concurrent-backup.ged"
        replacement.write_bytes(b"concurrent backup\n")
        os.replace(replacement, backup_path)
        if fail_validation:
            raise RuntimeError("fictional validation failure")

    if fail_validation:
        with pytest.raises(RuntimeError, match="validation failure"):
            publish_staged_bundle(
                ((staged, target),),
                replace=os.replace,
                validate_after=replace_backup,
            )
        assert target.read_bytes() == b"old\n"
    else:
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=replace_backup,
        )
        assert target.read_bytes() == b"new\n"

    backup_path = backups[0].path
    assert backup_path.read_bytes() == b"concurrent backup\n"
    backup_path.unlink()


def test_service_cleanup_preserves_a_replacement_at_its_staging_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "output.ged"
    report = tmp_path / "quality.md"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output.write_bytes(b"sentinel\n")
    replacements: list[Path] = []

    def replace_output_stage_then_fail(_report: object, report_path: str | Path) -> None:
        report_stage = Path(report_path)
        output_stage = next(
            path for path in tmp_path.glob(".ancestry-publish-*") if path != report_stage
        )
        replacement = tmp_path / "concurrent-stage.ged"
        replacement.write_bytes(b"concurrent\n")
        os.replace(replacement, output_stage)
        replacements.append(output_stage)
        raise OSError("fictional report write failure")

    monkeypatch.setattr(
        gedcom_engine,
        "write_quality_report",
        replace_output_stage_then_fail,
    )

    with pytest.raises(OSError, match="report write failure"):
        GedcomService().merge(
            [first, second],
            output,
            root_person="@I1@",
            quality_path=report,
        )

    assert output.read_bytes() == b"sentinel\n"
    assert replacements[0].read_bytes() == b"concurrent\n"
    replacements[0].unlink()


def test_gedcom_service_cleans_both_stages_when_caller_catches_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "output.ged"
    report = tmp_path / "quality.md"
    _write_person_gedcom(first, "@I1@", "Ada")
    _write_person_gedcom(second, "@I2@", "Grace")
    output.write_bytes(b"old output\n")
    report.write_bytes(b"old report\n")
    staged_paths: list[Path] = []

    def interrupt_publish(artifacts: Any, **_kwargs: Any) -> None:
        staged_paths.extend(Path(source) for source, _target in artifacts)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        gedcom_service_module,
        "publish_staged_bundle",
        interrupt_publish,
    )

    with pytest.raises(KeyboardInterrupt):
        GedcomService().merge(
            [first, second],
            output,
            root_person="@I1@",
            quality_path=report,
        )

    assert len(staged_paths) == 2
    assert all(not path.exists() for path in staged_paths)
    assert output.read_bytes() == b"old output\n"
    assert report.read_bytes() == b"old report\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_rootsmagic_exporter_cleans_both_stages_when_caller_catches_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "tree.rmtree"
    output = tmp_path / "output.ged"
    report = tmp_path / "export.md"
    _write_tree(tree)
    output.write_bytes(b"old output\n")
    report.write_bytes(b"old report\n")
    staged_paths: list[Path] = []

    def interrupt_publish(artifacts: Any, **_kwargs: Any) -> None:
        staged_paths.extend(Path(source) for source, _target in artifacts)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        exporter_module,
        "publish_staged_bundle",
        interrupt_publish,
    )

    with pytest.raises(KeyboardInterrupt):
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            living="include",
            report_path=report,
        )

    assert len(staged_paths) == 2
    assert all(not path.exists() for path in staged_paths)
    assert output.read_bytes() == b"old output\n"
    assert report.read_bytes() == b"old report\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_casefold_equivalent_nonexistent_parent_variants_are_rejected(
    tmp_path: Path,
) -> None:
    output = tmp_path / "CaseParent" / "Tree.ged"
    report = tmp_path / "caseparent" / "tree.GED"

    assert paths_alias(output, report)

    tree = tmp_path / "tree.rmtree"
    _write_tree(tree)
    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )

    assert raised.value.code == "EXPORT_REPORT_ALIAS"
    assert not output.parent.exists()
    assert not report.parent.exists()


@pytest.mark.parametrize("report_below_output", (True, False))
def test_rootsmagic_rejects_output_report_ancestor_paths_before_mkdir(
    tmp_path: Path,
    report_below_output: bool,
) -> None:
    tree = tmp_path / "tree.rmtree"
    _write_tree(tree)
    ancestor = tmp_path / "would-be-file"
    descendant = ancestor / "report.md"
    output, report = (ancestor, descendant) if report_below_output else (descendant, ancestor)

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            tree,
            output,
            report_path=report,
        )

    assert raised.value.code == "EXPORT_REPORT_ALIAS"
    assert not ancestor.exists()


def test_unreliable_or_reused_inode_identity_never_authorizes_cleanup(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned.ged"
    owned.write_bytes(b"owned\n")
    identity = publication_module._identity(owned)
    zero_inode = dataclasses.replace(identity, inode=0)
    reused_inode = dataclasses.replace(identity, created_ns=(identity.created_ns or 0) + 1)
    no_birthtime = dataclasses.replace(identity, created_ns=None)
    reused_without_birthtime = dataclasses.replace(
        no_birthtime,
        changed_ns=no_birthtime.changed_ns + 1,
    )

    assert not identity.same_object(zero_inode)
    assert not identity.same_object(reused_inode)
    assert not no_birthtime.same_object(reused_without_birthtime)
    assert not publication_module._unlink_if_owned(owned, zero_inode)
    assert owned.read_bytes() == b"owned\n"


def test_identity_cleanup_quarantines_and_restores_a_last_moment_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = tmp_path / "owned.ged"
    owned.write_bytes(b"owned\n")
    expected = publication_module._identity(owned)
    replacement = tmp_path / "concurrent.ged"
    replacement.write_bytes(b"concurrent\n")
    original_replace = publication_module._NATIVE_REPLACE
    raced = False

    def swap_during_cleanup(source: str | Path, destination: str | Path) -> None:
        nonlocal raced
        if Path(source) == owned and not raced:
            original_replace(replacement, owned)
            raced = True
        original_replace(source, destination)

    monkeypatch.setattr(publication_module, "_NATIVE_REPLACE", swap_during_cleanup)

    assert not publication_module._unlink_if_owned(owned, expected)
    assert raced
    assert owned.read_bytes() == b"concurrent\n"


def test_in_place_staging_mutation_after_claim_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"claimed\n")
    claimed = staged.stat()
    staged.write_bytes(b"changed\n")
    os.utime(staged, ns=(claimed.st_atime_ns, claimed.st_mtime_ns))
    changed = staged.stat()

    assert changed.st_size == claimed.st_size
    assert changed.st_mtime_ns == claimed.st_mtime_ns
    assert changed.st_ctime_ns != claimed.st_ctime_ns

    with pytest.raises(OSError, match="changed after it was sealed"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert not target.exists()
    assert staged.read_bytes() == b"changed\n"
    assert not publication_module.cleanup_staged_path(staged)
    staged.unlink()


def test_unusable_alias_spelling_fails_closed(tmp_path: Path) -> None:
    assert paths_alias("\x00unsafe", tmp_path / "output.ged")


def test_missing_nested_output_parent_is_not_created_by_staging(tmp_path: Path) -> None:
    parent = tmp_path / "missing" / "nested"

    with pytest.raises(FileNotFoundError):
        staging_path(parent / "output.ged")

    assert not parent.exists()


def test_regular_install_falls_back_to_portable_no_clobber_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    staged_identity = publication_module._identity(staged)
    staged_stat = staged.stat()
    original_commit = publication_module._commit_prepared_namespace
    candidates: list[Path] = []

    def inspect_private_commit(
        prepared: Any,
        destination: Path,
    ) -> None:
        source = prepared.candidate.path
        candidates.append(source)
        assert source.parent.name.startswith(".ancestry-publish-quarantine-")
        assert source.read_bytes() == b"new\n"
        assert not target.exists()
        assert not staged_identity.same_file_id(publication_module._identity(source))
        original_commit(prepared, destination)

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        inspect_private_commit,
    )

    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert len(candidates) == 1
    assert target.read_bytes() == b"new\n"
    published_stat = target.stat()
    assert stat.S_IMODE(published_stat.st_mode) == stat.S_IMODE(staged_stat.st_mode)
    assert published_stat.st_mtime_ns == staged_stat.st_mtime_ns
    assert not staged.exists()


def test_failed_private_install_never_exposes_partial_public_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    sentinel = b"existing public sentinel\n"
    target.write_bytes(sentinel)
    staged = _staged_bytes(target, b"complete publication payload\n")
    original_open = publication_module.os.open
    original_close = publication_module.os.close
    original_write = publication_module.os.write
    install_descriptors: set[int] = set()
    writes = 0
    public_observations: list[bytes] = []

    def track_private_install_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        selected = Path(path)
        if (
            selected.name == "owned"
            and selected.parent.name.startswith(".ancestry-publish-quarantine-")
            and flags & os.O_CREAT
        ):
            install_descriptors.add(descriptor)
        return descriptor

    def track_private_install_close(descriptor: int) -> None:
        original_close(descriptor)
        install_descriptors.discard(descriptor)

    def fail_after_private_partial_write(descriptor: int, payload: Any) -> int:
        nonlocal writes
        if descriptor not in install_descriptors:
            return original_write(descriptor, payload)
        writes += 1
        public_observations.append(target.read_bytes())
        if writes == 1:
            return original_write(descriptor, memoryview(payload)[:1])
        raise OSError("fictional private install write failure")

    monkeypatch.setattr(publication_module.os, "open", track_private_install_open)
    monkeypatch.setattr(publication_module.os, "close", track_private_install_close)
    monkeypatch.setattr(publication_module.os, "write", fail_after_private_partial_write)

    with pytest.raises(OSError, match="private install write failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert public_observations == [sentinel, sentinel]
    assert target.read_bytes() == sentinel
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_atomic_install_preserves_a_destination_that_appears_at_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    sentinel = b"concurrent destination sentinel\n"
    original_commit = publication_module._commit_prepared_namespace

    def create_sentinel_then_commit(prepared: Any, destination: Path) -> None:
        destination.write_bytes(sentinel)
        original_commit(prepared, destination)

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        create_sentinel_then_commit,
    )

    with pytest.raises(OSError, match="target appeared"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == sentinel
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_failed_commit_verification_preserves_a_foreign_target_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    replacement = tmp_path / "foreign.ged"
    sentinel = b"foreign replacement sentinel\n"
    replacement.write_bytes(sentinel)
    original_verify = publication_module._verify_committed_install
    original_cleanup = publication_module._cleanup_prepared_candidate
    original_close = publication_module._close_prepared_descriptor
    cleanup_descriptors: list[int] = []
    close_descriptors: list[int | None] = []
    swapped = False

    def replace_after_commit_before_verify(
        prepared: Any,
        destination: Path,
    ) -> Any:
        nonlocal swapped
        os.replace(replacement, destination)
        swapped = True
        return original_verify(prepared, destination)

    def capture_failed_cleanup(
        prepared: Any,
        destination: Path,
    ) -> None:
        assert prepared.descriptor is not None
        os.fstat(prepared.descriptor)
        cleanup_descriptors.append(prepared.descriptor)
        original_cleanup(prepared, destination)

    def capture_descriptor_close(prepared: Any) -> None:
        close_descriptors.append(prepared.descriptor)
        original_close(prepared)

    monkeypatch.setattr(
        publication_module,
        "_verify_committed_install",
        replace_after_commit_before_verify,
    )
    monkeypatch.setattr(
        publication_module,
        "_cleanup_prepared_candidate",
        capture_failed_cleanup,
    )
    monkeypatch.setattr(
        publication_module,
        "_close_prepared_descriptor",
        capture_descriptor_close,
    )

    with pytest.raises(OSError, match="replaced during commit"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert swapped
    assert cleanup_descriptors == close_descriptors
    assert len(close_descriptors) == 1
    assert target.read_bytes() == sentinel
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.skipif(
    publication_module._PLATFORM not in {"darwin", "linux"},
    reason="descriptor-relative namespace regression is POSIX-specific",
)
def test_quarantine_path_swap_never_publishes_or_deletes_foreign_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"trusted\n")
    sentinel = b"foreign quarantine sentinel\n"
    moved_quarantine = tmp_path / "held-quarantine"
    foreign_owned: Path | None = None
    original_commit = publication_module._commit_prepared_namespace

    def swap_quarantine_then_commit(prepared: Any, destination: Path) -> None:
        nonlocal foreign_owned
        prepared.quarantine.directory.rename(moved_quarantine)
        prepared.quarantine.directory.mkdir()
        foreign_owned = prepared.quarantine.path
        foreign_owned.write_bytes(sentinel)
        original_commit(prepared, destination)

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        swap_quarantine_then_commit,
    )

    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"trusted\n"
    assert foreign_owned is not None
    assert foreign_owned.read_bytes() == sentinel
    assert not (moved_quarantine / "owned").exists()

    foreign_owned.unlink()
    foreign_owned.parent.rmdir()
    moved_quarantine.rmdir()


@pytest.mark.skipif(
    publication_module._PLATFORM not in {"darwin", "linux"},
    reason="descriptor-relative namespace regression is POSIX-specific",
)
def test_destination_parent_swap_cannot_redirect_the_held_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "release"
    release_root.mkdir()
    target = release_root / "output.ged"
    staged = _staged_bytes(target, b"trusted\n")
    moved_root = tmp_path / "moved-release"
    sentinel = b"foreign destination sentinel\n"
    original_commit = publication_module._commit_prepared_namespace

    def swap_parent_then_commit(prepared: Any, destination: Path) -> None:
        original_commit(prepared, destination)
        release_root.rename(moved_root)
        release_root.mkdir()
        target.write_bytes(sentinel)

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        swap_parent_then_commit,
    )

    with pytest.raises(OSError, match="destination directory changed"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == sentinel
    assert not (moved_root / "output.ged").exists()

    target.unlink()
    release_root.rmdir()
    for retained in tuple(moved_root.iterdir()):
        if retained.is_dir():
            retained.rmdir()
        else:
            retained.unlink()
    moved_root.rmdir()


def test_quarantine_chmod_interruption_removes_the_owned_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_chmod = Path.chmod
    interrupted = False

    def chmod_then_interrupt(path: Path, mode: int, **kwargs: Any) -> None:
        nonlocal interrupted
        original_chmod(path, mode, **kwargs)
        if path.name.startswith(".ancestry-publish-quarantine-") and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(Path, "chmod", chmod_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_quarantine_identity_interruption_cleans_the_registered_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_identity = publication_module._identity
    interrupted = False

    def interrupt_first_quarantine_identity(path: Path) -> Any:
        nonlocal interrupted
        if path.name.startswith(".ancestry-publish-quarantine-") and not interrupted:
            interrupted = True
            raise error_type("fictional quarantine identity interruption")
        return original_identity(path)

    monkeypatch.setattr(
        publication_module,
        "_identity",
        interrupt_first_quarantine_identity,
    )

    with pytest.raises(error_type, match="quarantine identity interruption"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert interrupted
    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_candidate_copy_return_interruption_uses_registered_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_copy = publication_module._copy_regular_no_clobber

    def copy_then_interrupt(
        source: Any,
        destination: Path,
        *,
        owner: Any = None,
    ) -> Any:
        original_copy(source, destination, owner=owner)
        raise error_type("fictional candidate return interruption")

    monkeypatch.setattr(
        publication_module,
        "_copy_regular_no_clobber",
        copy_then_interrupt,
    )

    with pytest.raises(error_type, match="candidate return interruption"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_prepare_return_interruption_in_publish_closes_registered_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_prepare = publication_module._prepare_regular_install
    retained_descriptors: list[int] = []

    def prepare_then_interrupt(source: Any, prepared: Any) -> None:
        original_prepare(source, prepared)
        assert prepared.descriptor is not None
        retained_descriptors.append(prepared.descriptor)
        raise error_type("fictional prepare return interruption")

    monkeypatch.setattr(
        publication_module,
        "_prepare_regular_install",
        prepare_then_interrupt,
    )

    with pytest.raises(error_type, match="prepare return interruption"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert len(retained_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(retained_descriptors[0])
    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_prepare_return_interruption_in_restore_closes_registered_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    source_path = tmp_path / "recovery.ged"
    source_path.write_bytes(b"old\n")
    source = publication_module._OwnedPath(
        source_path,
        publication_module._identity(source_path),
        hashlib.sha256(b"old\n").digest(),
    )
    target = tmp_path / "output.ged"
    original_prepare = publication_module._prepare_regular_install
    retained_descriptors: list[int] = []

    def prepare_then_interrupt(owned: Any, prepared: Any) -> None:
        original_prepare(owned, prepared)
        assert prepared.descriptor is not None
        retained_descriptors.append(prepared.descriptor)
        raise error_type("fictional restore prepare interruption")

    monkeypatch.setattr(
        publication_module,
        "_prepare_regular_install",
        prepare_then_interrupt,
    )

    with pytest.raises(error_type, match="restore prepare interruption"):
        publication_module._install_no_clobber(source, target)

    assert source_path.read_bytes() == b"old\n"
    assert not target.exists()
    assert len(retained_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(retained_descriptors[0])
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_backup_return_interruption_uses_artifact_registered_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_backup = publication_module._backup_target
    backup_paths: list[Path] = []

    def backup_then_interrupt(artifact: Any) -> None:
        original_backup(artifact)
        assert artifact.backup is not None
        backup_paths.append(artifact.backup.path)
        raise error_type("fictional backup return interruption")

    monkeypatch.setattr(
        publication_module,
        "_backup_target",
        backup_then_interrupt,
    )

    with pytest.raises(error_type, match="backup return interruption"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"old\n"
    assert len(backup_paths) == 1
    assert not backup_paths[0].exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_displacement_reservation_return_interruption_removes_empty_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_reserve = publication_module._reserve_displacement
    reservation_paths: list[Path] = []

    def reserve_then_interrupt(target_path: Path, lifecycle: Any) -> None:
        original_reserve(target_path, lifecycle)
        assert lifecycle.reservation is not None
        reservation_paths.append(lifecycle.reservation.path)
        raise error_type("fictional displacement return interruption")

    monkeypatch.setattr(
        publication_module,
        "_reserve_displacement",
        reserve_then_interrupt,
    )

    with pytest.raises(error_type, match="displacement return interruption"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"old\n"
    assert len(reservation_paths) == 1
    assert not reservation_paths[0].exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_interrupt_after_native_commit_is_fully_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_commit = publication_module._commit_prepared_namespace

    def commit_then_interrupt(prepared: Any, destination: Path) -> None:
        original_commit(prepared, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_namespace",
        commit_then_interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_interrupt_after_commit_helper_return_uses_recorded_artifact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_commit = publication_module._commit_prepared_install
    interrupted = False

    def commit_then_interrupt(
        prepared: Any,
        destination: Path,
        *,
        artifact: Any = None,
        restoration: Any = None,
    ) -> Any:
        nonlocal interrupted
        result = original_commit(
            prepared,
            destination,
            artifact=artifact,
            restoration=restoration,
        )
        if artifact is not None and not interrupted:
            assert artifact.published is not None
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(
        publication_module,
        "_commit_prepared_install",
        commit_then_interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"old\n"
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_whole_success_cleanup_restarts_after_an_inter_artifact_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_target = tmp_path / "first.ged"
    second_target = tmp_path / "second.md"
    first_target.write_bytes(b"old first\n")
    second_target.write_bytes(b"old second\n")
    first_staged = _staged_bytes(first_target, b"new first\n")
    second_staged = _staged_bytes(second_target, b"new second\n")
    original_cleanup = publication_module._cleanup_after_commit
    cleanup_calls = 0
    interrupted = False

    def interrupt_between_artifacts(action: Any) -> None:
        nonlocal cleanup_calls, interrupted
        original_cleanup(action)
        cleanup_calls += 1
        if cleanup_calls == 3 and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(
        publication_module,
        "_cleanup_after_commit",
        interrupt_between_artifacts,
    )

    publish_staged_bundle(
        (
            (first_staged, first_target),
            (second_staged, second_target),
        ),
        replace=os.replace,
    )

    assert interrupted
    assert cleanup_calls >= 9
    assert first_target.read_bytes() == b"new first\n"
    assert second_target.read_bytes() == b"new second\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize(
    ("cleanup_kind", "error_type"),
    (
        ("unlink", KeyboardInterrupt),
        ("restore", SystemExit),
        ("source", KeyboardInterrupt),
    ),
)
def test_whole_rollback_sweep_retries_secondary_base_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_kind: str,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    interrupted = False

    if cleanup_kind == "unlink":
        original_unlink = publication_module._unlink_if_owned

        def interrupt_unlink_once(path: Path, expected: Any, **kwargs: Any) -> bool:
            nonlocal interrupted
            if path == target and not interrupted:
                interrupted = True
                raise error_type("fictional rollback unlink interruption")
            return original_unlink(path, expected, **kwargs)

        monkeypatch.setattr(
            publication_module,
            "_unlink_if_owned",
            interrupt_unlink_once,
        )
    elif cleanup_kind == "restore":
        original_restore = publication_module._restore_original

        def interrupt_restore_once(artifact: Any) -> Any:
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise error_type("fictional rollback restore interruption")
            return original_restore(artifact)

        monkeypatch.setattr(
            publication_module,
            "_restore_original",
            interrupt_restore_once,
        )
    else:
        original_source_cleanup = publication_module._cleanup_artifact_source

        def interrupt_source_once(artifact: Any) -> None:
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise error_type("fictional rollback source interruption")
            original_source_cleanup(artifact)

        monkeypatch.setattr(
            publication_module,
            "_cleanup_artifact_source",
            interrupt_source_once,
        )

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert interrupted
    assert target.read_bytes() == b"old\n"
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_restore_helper_return_interruption_reconciles_registered_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_install = publication_module._install_no_clobber
    restored_identities: list[Any] = []
    interrupted = False

    def install_then_interrupt(
        source: Any,
        target_path: Path,
        *,
        restoration: Any = None,
    ) -> Any:
        nonlocal interrupted
        installed = original_install(
            source,
            target_path,
            restoration=restoration,
        )
        if restoration is not None and not interrupted:
            assert restoration.restored is not None
            restored_identities.append(restoration.restored.identity)
            interrupted = True
            raise error_type("fictional restore return interruption")
        return installed

    monkeypatch.setattr(
        publication_module,
        "_install_no_clobber",
        install_then_interrupt,
    )

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert interrupted
    assert target.read_bytes() == b"old\n"
    assert len(restored_identities) == 1
    assert restored_identities[0].pristine(publication_module._identity(target))
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_multi_artifact_rollback_restarts_after_partial_secondary_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_target = tmp_path / "first.ged"
    second_target = tmp_path / "second.md"
    first_target.write_bytes(b"old first\n")
    second_target.write_bytes(b"old second\n")
    first_staged = _staged_bytes(first_target, b"new first\n")
    second_staged = _staged_bytes(second_target, b"new second\n")
    original_unlink = publication_module._unlink_if_owned
    original_restore = publication_module._restore_original
    original_source_cleanup = publication_module._cleanup_artifact_source
    interrupted = {"unlink": False, "restore": False, "source": False}

    def interrupt_first_target_unlink(
        path: Path,
        expected: Any,
        **kwargs: Any,
    ) -> bool:
        if path == first_target and not interrupted["unlink"]:
            interrupted["unlink"] = True
            raise KeyboardInterrupt("fictional multi-artifact unlink interruption")
        return original_unlink(path, expected, **kwargs)

    def interrupt_first_restore(artifact: Any) -> Any:
        if not interrupted["restore"]:
            interrupted["restore"] = True
            raise SystemExit("fictional multi-artifact restore interruption")
        return original_restore(artifact)

    def interrupt_first_source_cleanup(artifact: Any) -> None:
        if not interrupted["source"]:
            interrupted["source"] = True
            raise KeyboardInterrupt("fictional multi-artifact source interruption")
        original_source_cleanup(artifact)

    monkeypatch.setattr(
        publication_module,
        "_unlink_if_owned",
        interrupt_first_target_unlink,
    )
    monkeypatch.setattr(
        publication_module,
        "_restore_original",
        interrupt_first_restore,
    )
    monkeypatch.setattr(
        publication_module,
        "_cleanup_artifact_source",
        interrupt_first_source_cleanup,
    )

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            (
                (first_staged, first_target),
                (second_staged, second_target),
            ),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert all(interrupted.values())
    assert first_target.read_bytes() == b"old first\n"
    assert second_target.read_bytes() == b"old second\n"
    assert not first_staged.exists()
    assert not second_staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_namespace_commit_is_fsynced_before_descriptor_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_commit = publication_module._commit_prepared_namespace
    original_fsync = publication_module._fsync_directory_capability
    original_verify = publication_module._verify_committed_install
    events: list[str] = []

    def record_commit(prepared: Any, destination: Path) -> None:
        original_commit(prepared, destination)
        events.append("commit")

    def record_fsync(capability: Any) -> None:
        events.append("fsync")
        original_fsync(capability)

    def record_verify(prepared: Any, destination: Path) -> Any:
        events.append("verify")
        return original_verify(prepared, destination)

    monkeypatch.setattr(publication_module, "_commit_prepared_namespace", record_commit)
    monkeypatch.setattr(publication_module, "_fsync_directory_capability", record_fsync)
    monkeypatch.setattr(publication_module, "_verify_committed_install", record_verify)

    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert events[:3] == ["commit", "fsync", "verify"]
    assert target.read_bytes() == b"new\n"


def test_post_commit_directory_fsync_failure_rolls_back_the_exact_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    staged = _staged_bytes(target, b"new\n")
    original_fsync = publication_module._fsync_directory_capability
    calls = 0

    def fail_first_fsync(capability: Any) -> None:
        nonlocal calls
        calls += 1
        original_fsync(capability)
        if calls == 1:
            raise OSError("fictional destination fsync failure")

    monkeypatch.setattr(
        publication_module,
        "_fsync_directory_capability",
        fail_first_fsync,
    )

    with pytest.raises(OSError, match="destination fsync failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert calls >= 2
    assert not target.exists()
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_displacement_directory_fsync_failure_restores_the_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_fsync = publication_module._fsync_directory_capability
    calls = 0

    def fail_first_fsync(capability: Any) -> None:
        nonlocal calls
        calls += 1
        original_fsync(capability)
        if calls == 1:
            raise OSError("fictional displacement fsync failure")

    monkeypatch.setattr(
        publication_module,
        "_fsync_directory_capability",
        fail_first_fsync,
    )

    with pytest.raises(OSError, match="displacement fsync failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert calls >= 2
    assert target.read_bytes() == b"old\n"
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_windows_skips_unsupported_directory_flush_but_keeps_file_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.ged"
    payload = b"sealed payload\n"
    source_path.write_bytes(payload)
    source = publication_module._OwnedPath(
        source_path,
        publication_module._identity(source_path),
        hashlib.sha256(payload).digest(),
    )
    destination = tmp_path / "private-copy"
    original_fsync = publication_module.os.fsync
    flushed: list[int] = []

    def record_fsync(descriptor: int) -> None:
        flushed.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(publication_module, "_PLATFORM", "win32")
    monkeypatch.setattr(publication_module.os, "fsync", record_fsync)

    publication_module._fsync_directory_capability(
        publication_module._DirectoryCapability(
            tmp_path,
            -1,
            publication_module._identity(tmp_path),
        )
    )
    assert flushed == []

    copied = publication_module._copy_regular_no_clobber(source, destination)

    assert copied.path.read_bytes() == payload
    assert len(flushed) == 1
    destination.unlink()


def test_windows_handle_transfer_closes_the_native_owner_on_open_failure() -> None:
    native_handle = object()
    closed: list[object] = []
    fake_msvcrt = Mock()
    fake_msvcrt.open_osfhandle.side_effect = OSError("fictional transfer failure")

    with pytest.raises(OSError, match="transfer failure"):
        publication_module._windows_transfer_handle_to_descriptor(
            native_handle,
            42,
            closed.append,
            fake_msvcrt,
        )

    assert closed == [native_handle]


def test_windows_handle_transfer_closes_the_crt_owner_after_late_interrupt(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"payload\n")
    descriptor = os.open(candidate, os.O_RDONLY)

    class _InterruptingDescriptor:
        conversions = 0

        def __int__(self) -> int:
            self.conversions += 1
            if self.conversions == 1:
                raise KeyboardInterrupt
            return descriptor

    fake_msvcrt = Mock()
    fake_msvcrt.open_osfhandle.return_value = _InterruptingDescriptor()
    closed_native: list[object] = []

    with pytest.raises(KeyboardInterrupt):
        publication_module._windows_transfer_handle_to_descriptor(
            object(),
            42,
            closed_native.append,
            fake_msvcrt,
        )

    assert closed_native == []
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_windows_prepared_commit_binds_source_and_destination_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = Mock(descriptor=73)
    prepared = Mock(descriptor=41, destination=destination)
    calls: list[tuple[int, Any, str]] = []

    monkeypatch.setattr(publication_module, "_PLATFORM", "win32")
    monkeypatch.setattr(
        publication_module,
        "_directory_capability_matches_path",
        lambda _capability: True,
    )
    monkeypatch.setattr(
        publication_module,
        "_windows_rename_descriptor_no_replace",
        lambda source, parent, name: calls.append((source, parent, name)),
    )

    publication_module._commit_prepared_namespace(
        prepared,
        Path("release") / "output.ged",
    )

    assert calls == [(41, destination, "output.ged")]


def test_windows_candidate_verification_uses_a_delete_shared_native_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = tmp_path / "private-install"
    payload = b"sealed candidate\n"
    candidate_path.write_bytes(payload)
    candidate = publication_module._OwnedPath(
        candidate_path,
        publication_module._identity(candidate_path),
        hashlib.sha256(payload).digest(),
    )
    opened: list[Path] = []

    def open_delete_shared(path: Path) -> int:
        opened.append(path)
        return os.open(path, os.O_RDONLY)

    monkeypatch.setattr(publication_module, "_PLATFORM", "win32")
    monkeypatch.setattr(
        publication_module,
        "_windows_open_shared_descriptor",
        open_delete_shared,
    )

    prepared = publication_module._PreparedRegularInstall(candidate_path)
    publication_module._open_verified_install_candidate(candidate, prepared)
    descriptor = prepared.descriptor
    assert descriptor is not None
    os.close(descriptor)
    prepared.descriptor = None

    assert opened == [candidate_path]


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("cleanup_kind", ("backup", "source"))
def test_late_base_exception_cleanup_cannot_turn_a_valid_commit_into_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
    cleanup_kind: str,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    interrupted = False

    if cleanup_kind == "backup":
        original_cleanup = publication_module._cleanup_owned

        def interrupt_once(item: Any) -> None:
            nonlocal interrupted
            if item is not None and not interrupted:
                interrupted = True
                raise error_type("fictional late cleanup interruption")
            original_cleanup(item)

        monkeypatch.setattr(publication_module, "_cleanup_owned", interrupt_once)
    else:
        original_source_cleanup = publication_module._cleanup_artifact_source

        def interrupt_source_once(artifact: Any) -> None:
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise error_type("fictional late cleanup interruption")
            original_source_cleanup(artifact)

        monkeypatch.setattr(
            publication_module,
            "_cleanup_artifact_source",
            interrupt_source_once,
        )

    publish_staged_bundle(((staged, target),), replace=os.replace)

    assert interrupted
    assert target.read_bytes() == b"new\n"
    assert not staged.exists()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_portable_copy_install_rolls_back_an_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")

    def deny_hardlink(
        _source: str | Path,
        _destination: str | Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        del follow_symlinks
        raise OSError("fictional hardlink denial")

    monkeypatch.setattr(publication_module.os, "link", deny_hardlink)

    with pytest.raises(RuntimeError, match="validation failure"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    assert target.read_bytes() == b"old\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_partial_backup_copy_is_removed_using_its_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.ged"
    target.write_bytes(b"old target bytes\n")
    staged = _staged_bytes(target, b"new target bytes\n")
    original_write = publication_module.os.write
    writes = 0

    def fail_after_partial_write(descriptor: int, payload: Any) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return original_write(descriptor, memoryview(payload)[:1])
        raise OSError("fictional backup write failure")

    monkeypatch.setattr(publication_module.os, "write", fail_after_partial_write)

    with pytest.raises(OSError, match="backup write failure"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"old target bytes\n"
    assert not list(tmp_path.glob(".ancestry-publish-backup-*"))
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_existing_fifo_target_is_rejected_before_any_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform.")
    target = tmp_path / "output.ged"
    os.mkfifo(target)
    staged = _staged_bytes(target, b"new\n")
    calls: list[tuple[str | Path, str | Path]] = []
    original_link = publication_module.os.link

    def record_link(
        source: str | Path,
        destination: str | Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        calls.append((source, destination))
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(publication_module.os, "link", record_link)

    with pytest.raises(OSError, match="regular files or symbolic links"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert calls == []
    assert publication_module.cleanup_staged_path(staged)
    target.unlink()


def test_hardlink_fallback_uses_nonblocking_open_and_rejects_fifo_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("FIFO nonblocking open is unavailable on this platform.")
    target = tmp_path / "output.ged"
    target.write_bytes(b"old\n")
    staged = _staged_bytes(target, b"new\n")
    original_open = publication_module.os.open
    observed_flags: list[int] = []
    raced = False

    def deny_hardlinks(
        _source: str | Path,
        _destination: str | Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        del follow_symlinks
        raise OSError("fictional hardlink denial")

    def swap_before_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        if Path(path) == target and not raced:
            observed_flags.append(flags)
            target.unlink()
            os.mkfifo(target)
            raced = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publication_module.os, "link", deny_hardlinks)
    monkeypatch.setattr(publication_module.os, "open", swap_before_open)

    with pytest.raises(OSError, match="changed"):
        publish_staged_bundle(((staged, target),), replace=os.replace)

    assert len(observed_flags) == 1
    assert observed_flags[0] & os.O_NONBLOCK
    assert target.is_fifo()
    target.unlink()


def test_symlink_restore_fallback_never_clobbers_an_appearing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = tmp_path / "victim.ged"
    victim.write_bytes(b"victim\n")
    target = tmp_path / "output.ged"
    target.symlink_to(victim)
    staged = _staged_bytes(target, b"new\n")
    original_link = publication_module.os.link
    original_symlink = publication_module.os.symlink
    raced = False

    def deny_symlink_hardlinks(
        source: str | Path,
        destination: str | Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        if Path(source).is_symlink():
            raise OSError("fictional symlink hardlink denial")
        original_link(source, destination, follow_symlinks=follow_symlinks)

    def create_concurrent_target_before_restore(
        source: str | bytes,
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target_is_directory: bool = False,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal raced
        if Path(destination) == target and not raced:
            target.write_bytes(b"concurrent\n")
            raced = True
        original_symlink(
            source,
            destination,
            target_is_directory=target_is_directory,
            dir_fd=dir_fd,
        )

    monkeypatch.setattr(publication_module.os, "link", deny_symlink_hardlinks)
    monkeypatch.setattr(
        publication_module.os,
        "symlink",
        create_concurrent_target_before_restore,
    )

    with pytest.raises(OSError, match="could not be restored safely"):
        publish_staged_bundle(
            ((staged, target),),
            replace=os.replace,
            validate_after=lambda: (_ for _ in ()).throw(
                RuntimeError("fictional validation failure")
            ),
        )

    recovery = list(tmp_path.glob(".ancestry-publish-backup-*")) + list(
        tmp_path.glob(".ancestry-publish-displaced-*")
    )
    assert raced
    assert target.read_bytes() == b"concurrent\n"
    assert victim.read_bytes() == b"victim\n"
    assert len(recovery) == 2
    assert all(path.is_symlink() for path in recovery)
    for path in recovery:
        path.unlink()


def test_rootsmagic_special_character_uri_uses_only_the_verified_database(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "fictional ?#% É.rmtree"
    _write_tree(tree)
    output = tmp_path / "output.ged"
    reader = RootsMagicReader([tmp_path])

    assert "PersonTable" in reader.schema(tree)
    assert reader.query(tree, "SELECT PersonID FROM PersonTable ORDER BY PersonID").rows == (
        (1,),
        (2,),
    )
    result = RootsMagicExporter(reader).export(tree, output, living="include")
    assert result.output_path.exists()

    llm = Mock()
    llm.generate.return_value = Mock(
        parsed={"sql": "SELECT PersonID FROM PersonTable ORDER BY PersonID"},
        provider_id="fictional",
        model="fixture",
    )
    service = RootsMagicService(
        AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path / "data",
            family_tree_dirs=[tmp_path],
        ),
        llm,
    )
    assert service.query_question(
        tree,
        "Who is present?",
        provider_id="fictional",
        model="fixture",
    ).rows == ((1,), (2,))


@pytest.mark.parametrize("sidecar_suffix", ("-wal", "-shm"))
def test_rootsmagic_sidecars_fail_closed_before_provider_and_output(
    tmp_path: Path,
    sidecar_suffix: str,
) -> None:
    tree = tmp_path / "tree.rmtree"
    _write_tree(tree)
    Path(f"{tree}{sidecar_suffix}").write_bytes(b"fictional sidecar")
    output = tmp_path / "output.ged"
    output.write_bytes(b"sentinel\n")
    llm = Mock()
    service = RootsMagicService(
        AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path / "data",
            family_tree_dirs=[tmp_path],
        ),
        llm,
    )

    with pytest.raises(AncestryError) as query_error:
        service.query_question(
            tree,
            "Who is present?",
            provider_id="fictional",
            model="fixture",
        )
    with pytest.raises(AncestryError) as export_error:
        service.export(tree, output)

    assert query_error.value.code == "ROOTSMAGIC_WAL_ACTIVE"
    assert export_error.value.code == "ROOTSMAGIC_WAL_ACTIVE"
    assert llm.generate.call_count == 0
    assert output.read_bytes() == b"sentinel\n"


def test_rootsmagic_sidecar_created_during_provider_call_fails_closed(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "tree.rmtree"
    _write_tree(tree)
    llm = Mock()

    def create_sidecar(_request: object, _consent: object) -> Mock:
        Path(f"{tree}-wal").write_bytes(b"fictional sidecar")
        return Mock(
            parsed={"sql": "SELECT PersonID FROM PersonTable"},
            provider_id="fictional",
            model="fixture",
        )

    llm.generate.side_effect = create_sidecar
    service = RootsMagicService(
        AppConfig(
            config_path=tmp_path / "config.toml",
            data_dir=tmp_path / "data",
            family_tree_dirs=[tmp_path],
        ),
        llm,
    )

    with pytest.raises(FileIngressError) as raised:
        service.query_question(
            tree,
            "Who is present?",
            provider_id="fictional",
            model="fixture",
        )

    assert raised.value.code == "FILE_INPUT_CHANGED"
    assert llm.generate.call_count == 1


def test_rootsmagic_missing_paths_use_sanitized_stable_errors(tmp_path: Path) -> None:
    reader = RootsMagicReader([tmp_path])
    explicit = tmp_path / "private-missing-tree.rmtree"

    with pytest.raises(FileIngressError) as explicit_error:
        reader.resolve_tree(explicit)
    with pytest.raises(AncestryError) as relative_error:
        reader.resolve_tree("private-relative-name")

    assert explicit_error.value.code == "FILE_INPUT_UNREADABLE"
    assert relative_error.value.code == "ROOTSMAGIC_TREE_NOT_FOUND"
    assert str(explicit) not in explicit_error.value.render()
    assert "private-relative-name" not in relative_error.value.render()
