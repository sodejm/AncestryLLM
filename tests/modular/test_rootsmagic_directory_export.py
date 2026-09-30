"""Verify atomic new-directory RootsMagic exports with fictional records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ancestryllm.application._rootsmagic_directory_export as directory_export_module
from ancestryllm.application._rootsmagic_directory_export import RootsMagicDirectoryExporter
from ancestryllm.application._rootsmagic_workbench import source_digest
from ancestryllm.application.mutations import MutationState
from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationToken,
    bind_cancellation_token,
)
from ancestryllm.core.directory_mutation import DirectoryMutation
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.mutation import LocalMutationCoordinator
from ancestryllm.rootsmagic.reader import RootsMagicReader, sha256_file

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fictional_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "fictional-private.rmtree"
    connection = sqlite3.connect(tree)
    connection.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Surname TEXT, Given TEXT, IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);
        CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
        INSERT INTO PersonTable VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1), (4, 1, 0), (5, 0, 0);
        INSERT INTO NameTable VALUES
            (1, 1, 'Example', 'Alex', 1),
            (2, 2, 'Example', 'Blair', 1),
            (3, 3, 'Confidential', 'Linden', 1),
            (4, 4, 'Example', 'Dana', 1),
            (5, 5, 'Elsewhere', 'Emery', 1);
        INSERT INTO FamilyTable VALUES (10, 1, 2), (11, 3, 4);
        INSERT INTO ChildTable VALUES (10, 3);
        """
    )
    connection.commit()
    connection.close()
    return tree


def _exporter(tmp_path: Path) -> RootsMagicDirectoryExporter:
    return RootsMagicDirectoryExporter(RootsMagicReader([tmp_path]))


def _coordinator(tmp_path: Path) -> LocalMutationCoordinator:
    return LocalMutationCoordinator(tmp_path / "coordination")


def test_export_rejects_parent_replaced_while_mapping(
    tmp_path: Path, fictional_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "selected-parent"
    parent.mkdir()
    moved = tmp_path / "moved-parent"
    exporter = _exporter(tmp_path)
    original_map = exporter._map_snapshot

    def replace_parent(*args, **kwargs):
        mapped = original_map(*args, **kwargs)
        parent.rename(moved)
        parent.mkdir()
        return mapped

    monkeypatch.setattr(exporter, "_map_snapshot", replace_parent)
    with _coordinator(tmp_path) as coordinator, pytest.raises(AncestryError) as captured:
        exporter.export(
            fictional_tree, parent / "export", root_person_id="1", coordinator=coordinator
        )
    assert captured.value.code == "ROOTSMAGIC_EXPORT_PARENT_INVALID"
    assert list(parent.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_export_result_is_serializable_opaque_and_digest_consistent(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "opaque-export"
    with _coordinator(tmp_path) as coordinator:
        result = _exporter(tmp_path).export(
            fictional_tree, target, root_person_id="1", coordinator=coordinator
        )
    serialized = result.to_json()
    assert type(result).from_json(serialized) == result
    payload = json.loads(serialized)["value"]
    assert set(payload) == {"manifest", "gedcom", "report", "state"}
    assert payload["state"] == "committed"
    assert str(tmp_path) not in serialized
    assert "_path" not in serialized
    identifiers = set()
    for field, name in (
        ("manifest", "manifest.json"),
        ("gedcom", "tree.ged"),
        ("report", "report.md"),
    ):
        descriptor = payload[field]
        content = (target / name).read_bytes()
        assert descriptor["artifact_id"].startswith("art_")
        identifiers.add(descriptor["artifact_id"])
        assert descriptor["status"] == "ready"
        assert descriptor["sha256"] == hashlib.sha256(content).hexdigest()
        assert descriptor["size_bytes"] == len(content)
    assert len(identifiers) == 3


def test_export_rejects_parent_replaced_before_worker_entry(
    tmp_path: Path, fictional_tree: Path
) -> None:
    parent = tmp_path / "selected-parent"
    parent.mkdir()
    original = parent.stat()
    moved = tmp_path / "moved-parent"
    parent.rename(moved)
    parent.mkdir()
    with _coordinator(tmp_path) as coordinator, pytest.raises(AncestryError) as captured:
        _exporter(tmp_path).export(
            fictional_tree,
            parent / "export",
            root_person_id="1",
            coordinator=coordinator,
            expected_parent=(original.st_dev, original.st_ino),
        )
    assert captured.value.code == "ROOTSMAGIC_EXPORT_PARENT_INVALID"
    assert list(parent.iterdir()) == []
    assert list(moved.iterdir()) == []


@pytest.mark.parametrize("phase", ["plan_staging", "committing"])
def test_export_rejects_parent_replaced_immediately_before_staging_or_publication(
    tmp_path: Path, fictional_tree: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    parent = tmp_path / "selected-parent"
    parent.mkdir()
    moved = tmp_path / "moved-parent"
    original = getattr(DirectoryMutation, phase)

    def replace_parent(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        parent.rename(moved)
        parent.mkdir()
        (parent / "sentinel").write_text("unrelated replacement directory", encoding="utf-8")
        return result

    monkeypatch.setattr(DirectoryMutation, phase, replace_parent)
    with _coordinator(tmp_path) as coordinator, pytest.raises(AncestryError) as captured:
        _exporter(tmp_path).export(
            fictional_tree, parent / "export", root_person_id="1", coordinator=coordinator
        )
    # Recovery must not clean through a changed parent or publish in its replacement.
    assert captured.value.code == "MUTATION_REAUTHORIZATION_REQUIRED"
    assert {item.name for item in parent.iterdir()} == {"sentinel"}
    assert (parent / "sentinel").read_text(encoding="utf-8") == "unrelated replacement directory"
    assert not (moved / "export").exists()
    stages = list(moved.glob(".ancestry-export-*"))
    assert len(stages) == (1 if phase == "committing" else 0)


def _open_fictional_wal_tree(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    tree = tmp_path / "fictional-active-wal.rmtree"
    writer = sqlite3.connect(tree)
    assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY,
            OwnerID INTEGER,
            Surname TEXT,
            Given TEXT,
            IsPrimary INTEGER
        );
        INSERT INTO PersonTable VALUES (1, 0, 0);
        INSERT INTO NameTable VALUES (1, 1, 'Example', 'Wal', 1);
        """
    )
    writer.commit()
    return tree, writer


def test_default_export_is_rooted_private_and_digest_consistent(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "portable-export"
    source_hash = sha256_file(fictional_tree)
    callback_calls = 0

    def verify_source() -> None:
        nonlocal callback_calls
        callback_calls += 1
        assert not target.exists()
        stages = list(tmp_path.glob(".ancestry-export-*"))
        assert len(stages) == 1
        assert {path.name for path in stages[0].iterdir()} == {
            "tree.ged",
            "report.md",
            "manifest.json",
        }

    with _coordinator(tmp_path) as coordinator:
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            coordinator=coordinator,
            source_ref="rootsmagic-session:fictional",
            source_fingerprint="aggregate-fingerprint",
            verify_source=verify_source,
        )

    assert result.state is MutationState.COMMITTED
    assert callback_calls == 1
    assert {path.name for path in target.iterdir()} == {
        "tree.ged",
        "report.md",
        "manifest.json",
    }
    gedcom = (target / "tree.ged").read_text(encoding="utf-8")
    assert "0 @I1@ INDI" in gedcom
    assert "0 @I2@ INDI" in gedcom
    assert "0 @I3@ INDI" in gedcom
    assert "Linden /Confidential/" not in gedcom
    assert "Dana /Example/" in gedcom
    assert "0 @I4@ INDI" not in gedcom
    assert "0 @I5@ INDI" not in gedcom
    assert "Emery /Elsewhere/" not in gedcom
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == {
        "fingerprint": "aggregate-fingerprint",
        "name": fictional_tree.name,
        "reference": "rootsmagic-session:fictional",
        "sqlite_snapshot": "verified standalone database",
    }
    for name in ("tree.ged", "report.md"):
        payload = (target / name).read_bytes()
        assert manifest["files"][name] == {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    serialized = (target / "manifest.json").read_text(encoding="utf-8")
    report = (target / "report.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert str(tmp_path) not in report
    assert fictional_tree.name in report
    assert sha256_file(fictional_tree) == source_hash
    assert not list(tmp_path.glob(".ancestry-export-*"))


def test_export_rejects_missing_root_without_publishing(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "missing-root"
    before = sha256_file(fictional_tree)
    with _coordinator(tmp_path) as coordinator, pytest.raises(AncestryError) as captured:
        _exporter(tmp_path).export(
            fictional_tree, target, root_person_id="999999", coordinator=coordinator
        )
    assert captured.value.code == "ROOTSMAGIC_EXPORT_ROOT_NOT_FOUND"
    assert not target.exists()
    assert sha256_file(fictional_tree) == before


def test_export_rejects_excluded_living_root_without_publishing(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "excluded-living-root"
    before = sha256_file(fictional_tree)
    with _coordinator(tmp_path) as coordinator, pytest.raises(AncestryError) as captured:
        _exporter(tmp_path).export(
            fictional_tree, target, root_person_id="3", living="exclude", coordinator=coordinator
        )
    assert captured.value.code == "ROOTSMAGIC_EXPORT_ROOT_EXCLUDED"
    assert not target.exists()
    assert not list(tmp_path.glob(".ancestry-export-*"))
    assert sha256_file(fictional_tree) == before


@pytest.mark.parametrize("living", ["redact", "include"])
def test_export_preserves_living_root_under_supported_inclusion_policies(
    tmp_path: Path, fictional_tree: Path, living: str
) -> None:
    before = sha256_file(fictional_tree)
    target = tmp_path / "included-living-root"
    with _coordinator(tmp_path) as coordinator:
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="3",
            living=living,
            coordinator=coordinator,
        )
    assert result.state is MutationState.COMMITTED
    gedcom = (target / "tree.ged").read_text(encoding="utf-8")
    assert "0 @I3@ INDI" in gedcom
    assert ("Linden /Confidential/" in gedcom) is (living == "include")
    assert ("Living /Private/" in gedcom) is (living == "redact")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert (
        manifest["files"]["tree.ged"]["sha256"]
        == hashlib.sha256((target / "tree.ged").read_bytes()).hexdigest()
    )
    assert sha256_file(fictional_tree) == before


def test_descendant_scope_honors_generation_limit_and_living_policy(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "descendants"

    with _coordinator(tmp_path) as coordinator:
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            scope="descendants",
            generations=1,
            living="include",
            coordinator=coordinator,
        )

    assert result.state is MutationState.COMMITTED
    gedcom = (target / "tree.ged").read_text(encoding="utf-8")
    assert "0 @I1@ INDI" in gedcom
    assert "0 @I2@ INDI" in gedcom
    assert "Linden /Confidential/" in gedcom
    assert "Blair /Example/" not in gedcom
    assert "Dana /Example/" not in gedcom
    assert "Emery /Elsewhere/" not in gedcom


def test_publication_guard_linearizes_source_verification_and_commit(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "guarded"
    events: list[str] = []

    @contextmanager
    def publication_guard() -> Iterator[None]:
        events.append("guard-enter")
        assert not target.exists()
        yield
        assert target.is_dir()
        events.append("guard-exit")

    def verify_source() -> None:
        events.append("verify")
        assert events == ["guard-enter", "verify"]
        assert not target.exists()

    with _coordinator(tmp_path) as coordinator:
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            coordinator=coordinator,
            verify_source=verify_source,
            publication_guard=publication_guard,
        )

    assert result.state is MutationState.COMMITTED
    assert events == ["guard-enter", "verify", "guard-exit"]


def test_root_is_required_and_source_alias_is_rejected(
    tmp_path: Path, fictional_tree: Path
) -> None:
    exporter = _exporter(tmp_path)

    with pytest.raises(AncestryError) as missing:
        exporter.export(fictional_tree, tmp_path / "missing-root", root_person_id="")
    with pytest.raises(AncestryError) as alias:
        exporter.export(fictional_tree, fictional_tree, root_person_id="1")

    assert missing.value.code == "ROOTSMAGIC_EXPORT_ROOT_REQUIRED"
    assert alias.value.code == "ROOTSMAGIC_EXPORT_SOURCE_ALIAS"


def test_existing_export_target_is_never_reassigned(tmp_path: Path, fictional_tree: Path) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_text("fictional prior data", encoding="utf-8")

    with pytest.raises(AncestryError) as raised:
        _exporter(tmp_path).export(fictional_tree, target, root_person_id="1")

    assert raised.value.code == "ROOTSMAGIC_EXPORT_TARGET_EXISTS"
    assert sentinel.read_text(encoding="utf-8") == "fictional prior data"


def test_dangling_target_symlink_is_never_followed_or_reassigned(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "claimed-target"
    referent = tmp_path / "missing-referent"
    target.symlink_to(referent, target_is_directory=True)

    with pytest.raises(AncestryError) as raised:
        _exporter(tmp_path).export(fictional_tree, target, root_person_id="1")

    assert raised.value.code == "ROOTSMAGIC_EXPORT_TARGET_EXISTS"
    assert target.is_symlink()
    assert target.readlink() == referent
    assert not referent.exists()


def test_valid_wal_generation_and_companions_are_unchanged(tmp_path: Path) -> None:
    tree, writer = _open_fictional_wal_tree(tmp_path)
    try:
        source_paths = (tree, Path(f"{tree}-wal"), Path(f"{tree}-shm"))
        assert all(path.exists() for path in source_paths)
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
            for path in source_paths
        }
        reader = RootsMagicReader([tmp_path])
        source_fingerprint = reader.fingerprint_source(tree)
        aggregate_fingerprint = source_digest(source_fingerprint)

        target = tmp_path / "wal-export"
        with _coordinator(tmp_path) as coordinator:
            result = RootsMagicDirectoryExporter(reader).export(
                tree,
                target,
                root_person_id="1",
                coordinator=coordinator,
                source_ref="rootsmagic-session:fictional-wal",
                source_fingerprint=aggregate_fingerprint,
                verify_source=lambda: reader.verify_source(tree, source_fingerprint),
            )

        assert result.state is MutationState.COMMITTED
        assert "1 NAME Wal /Example/" in (target / "tree.ged").read_text(encoding="utf-8")
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"] == {
            "fingerprint": aggregate_fingerprint,
            "name": tree.name,
            "reference": "rootsmagic-session:fictional-wal",
            "sqlite_snapshot": "verified main database plus WAL generation",
        }
        assert {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
            for path in source_paths
        } == before
    finally:
        writer.close()


def test_destination_conflict_is_preserved_until_explicit_recovery(
    tmp_path: Path, fictional_tree: Path
) -> None:
    target = tmp_path / "raced-target"

    def create_conflicting_target() -> None:
        target.mkdir()
        (target / "sentinel").write_text("fictional concurrent data", encoding="utf-8")

    with _coordinator(tmp_path) as coordinator:
        with pytest.raises(AncestryError) as raised:
            _exporter(tmp_path).export(
                fictional_tree,
                target,
                root_person_id="1",
                coordinator=coordinator,
                verify_source=create_conflicting_target,
            )

        assert raised.value.code == "MUTATION_RECOVERY_REQUIRED"
        assert (target / "sentinel").read_text(encoding="utf-8") == ("fictional concurrent data")
        assert len(list(tmp_path.glob(".ancestry-export-*"))) == 1

    (target / "sentinel").unlink()
    target.rmdir()
    with _coordinator(tmp_path) as coordinator:
        DirectoryMutation.reconcile(target, coordinator)

    assert not target.exists()
    assert not list(tmp_path.glob(".ancestry-export-*"))


def test_cancellation_before_commit_publishes_nothing(tmp_path: Path, fictional_tree: Path) -> None:
    target = tmp_path / "cancelled"
    token = CancellationToken()

    def cancel() -> None:
        token.request()

    with (
        _coordinator(tmp_path) as coordinator,
        bind_cancellation_token(token),
        pytest.raises(CancellationError),
    ):
        _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            coordinator=coordinator,
            verify_source=cancel,
        )

    assert not target.exists()
    assert not list(tmp_path.glob(".ancestry-export-*"))


def test_wal_precommit_cancellation_preserves_source_and_target(tmp_path: Path) -> None:
    tree, writer = _open_fictional_wal_tree(tmp_path)
    try:
        source_paths = (tree, Path(f"{tree}-wal"), Path(f"{tree}-shm"))
        assert all(path.exists() for path in source_paths)
        before = {path: path.read_bytes() for path in source_paths}
        reader = RootsMagicReader([tmp_path])
        source_fingerprint = reader.fingerprint_source(tree)
        aggregate_fingerprint = source_digest(source_fingerprint)
        target = tmp_path / "cancelled-wal-export"
        token = CancellationToken()

        def cancel_at_precommit() -> None:
            reader.verify_source(tree, source_fingerprint)
            stages = list(tmp_path.glob(".ancestry-export-*"))
            assert len(stages) == 1
            manifest = json.loads((stages[0] / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["source"]["fingerprint"] == aggregate_fingerprint
            token.request()

        with (
            _coordinator(tmp_path) as coordinator,
            bind_cancellation_token(token),
            pytest.raises(CancellationError),
        ):
            RootsMagicDirectoryExporter(reader).export(
                tree,
                target,
                root_person_id="1",
                coordinator=coordinator,
                source_ref="rootsmagic-session:cancelled-fictional-wal",
                source_fingerprint=aggregate_fingerprint,
                verify_source=cancel_at_precommit,
            )

        assert not target.exists()
        assert not list(tmp_path.glob(".ancestry-export-*"))
        assert {path: path.read_bytes() for path in source_paths} == before
    finally:
        writer.close()


def test_cancellation_after_rename_returns_committed_result(
    tmp_path: Path,
    fictional_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "committed"
    token = CancellationToken()
    rename = directory_export_module._exclusive_rename_directory

    def rename_then_cancel(source: Path, destination: Path) -> None:
        rename(source, destination)
        token.request()

    monkeypatch.setattr(
        directory_export_module,
        "_exclusive_rename_directory",
        rename_then_cancel,
    )
    with _coordinator(tmp_path) as coordinator, bind_cancellation_token(token):
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            coordinator=coordinator,
        )

    assert result.state is MutationState.COMMITTED
    assert (target / "tree.ged").is_file()


def test_failed_publication_is_cleaned_and_same_target_can_retry(
    tmp_path: Path,
    fictional_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "retryable"
    rename = directory_export_module._exclusive_rename_directory

    def fail_rename(_source: Path, _destination: Path) -> None:
        raise OSError("fictional publication failure")

    with _coordinator(tmp_path) as coordinator:
        monkeypatch.setattr(directory_export_module, "_exclusive_rename_directory", fail_rename)
        with pytest.raises(OSError, match="fictional publication failure"):
            _exporter(tmp_path).export(
                fictional_tree,
                target,
                root_person_id="1",
                coordinator=coordinator,
            )
        assert not target.exists()
        assert not list(tmp_path.glob(".ancestry-export-*"))

        monkeypatch.setattr(directory_export_module, "_exclusive_rename_directory", rename)
        result = _exporter(tmp_path).export(
            fictional_tree,
            target,
            root_person_id="1",
            coordinator=coordinator,
        )

    assert result.state is MutationState.COMMITTED
