"""Tests for incremental genealogy release workflows and safety invariants."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import ancestryllm.gedcom.engine as engine
import ancestryllm.gedcom.incremental as incremental
from ancestryllm.core.errors import AncestryError, FileIngressError, ProviderError
from ancestryllm.core.ingress import (
    FileFingerprint,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
    TextLine,
)
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.gedcom import (
    parser as gedcom_parser,
)
from ancestryllm.gedcom import (
    sync_algorithms,
    sync_cli,
    sync_contracts,
    sync_operations,
    sync_publication,
)
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.gedcom.sync import execute_sync, run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gedcom_incremental"


def test_legacy_module_facades_reexport_supported_owner_contracts() -> None:
    assert engine.GedcomRecord is gedcom_parser.GedcomRecord
    assert incremental.SyncError is sync_contracts.SyncError
    assert incremental.execute_command is sync_operations.execute_command


def test_structured_sync_execution_is_terminal_neutral_and_legacy_rendering_matches(
    capsys,
) -> None:
    result = execute_sync([])

    assert result.exit_code == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
    assert result.output == ""
    assert "ERROR [SYNC_CONFIGURATION]" in result.error
    assert capsys.readouterr() == ("", "")

    assert run_sync([]) == result.exit_code
    rendered = capsys.readouterr()
    assert rendered.out == result.output
    assert rendered.err == result.error


def test_incremental_argument_errors_use_stable_typed_contract() -> None:
    parser = sync_cli.PlainEnglishArgumentParser(prog="ancestry gedcom update")
    with pytest.raises(sync_contracts.SyncError) as raised:
        parser.error("missing --master")
    assert raised.value.code == "SYNC_CONFIGURATION"
    assert raised.value.exit_code == 2
    assert "missing --master" not in raised.value.render()


@pytest.mark.parametrize("raise_errors", (False, True))
def test_incremental_argparse_errors_redact_private_argument_values(
    tmp_path: Path,
    capsys,
    raise_errors: bool,
) -> None:
    private_value = tmp_path / "private-family-tree.ged"
    arguments = [
        "update",
        "--master",
        "master.ged",
        "--initialize-manifest",
        "--snapshot",
        "fictional-main:other=snapshot.ged",
        "--release-root",
        "releases",
        "--no-quality-report",
        "--unsupported-private-option",
        str(private_value),
    ]

    if raise_errors:
        with pytest.raises(AncestryError) as raised:
            run_sync(arguments, raise_errors=True)
        rendered = raised.value.render() + repr(raised.value.details)
    else:
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
        rendered = capsys.readouterr().err

    assert "SYNC_CONFIGURATION" in rendered
    assert str(private_value) not in rendered


@pytest.mark.parametrize("raise_errors", (False, True))
@pytest.mark.parametrize(
    "descriptor_factory",
    (
        lambda private_path: str(private_path),
        lambda private_path: f"{private_path}:other={FIXTURES / 'ancestry-snapshot-v1.ged'}",
        lambda private_path: (
            f"fictional-main:{private_path}={FIXTURES / 'ancestry-snapshot-v1.ged'}"
        ),
    ),
)
def test_malformed_snapshot_errors_redact_private_descriptor_values(
    tmp_path: Path,
    capsys,
    raise_errors: bool,
    descriptor_factory: Callable[[Path], str],
) -> None:
    private_value = tmp_path / "private-family-tree.ged"
    arguments = [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--snapshot",
        descriptor_factory(private_value),
        "--release-root",
        str(tmp_path / "releases"),
        "--no-quality-report",
    ]

    if raise_errors:
        with pytest.raises(AncestryError) as raised:
            run_sync(arguments, raise_errors=True)
        rendered = raised.value.render() + repr(raised.value.details)
    else:
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
        rendered = capsys.readouterr().err

    assert "SYNC_CONFIGURATION" in rendered
    assert str(private_value) not in rendered


@pytest.mark.parametrize("raise_errors", (False, True))
@pytest.mark.parametrize(
    "path_target",
    (
        "update-master",
        "update-manifest",
        "update-release-root",
        "update-snapshot",
        "rebase-master",
        "rebase-manifest",
        "rebase-release-root",
    ),
)
def test_sync_unexpandable_paths_use_file_ingress_error_contract(
    tmp_path: Path,
    capsys,
    raise_errors: bool,
    path_target: str,
) -> None:
    unexpandable = "~ancestryllm_user_that_does_not_exist_76/private-tree.ged"
    if path_target.startswith("update"):
        arguments = [
            "update",
            "--master",
            str(FIXTURES / "baseline-master.ged"),
            "--initialize-manifest",
            "--snapshot",
            _snapshot("ancestry-main", "ancestry", 1),
            "--release-root",
            str(tmp_path / "releases"),
            "--no-quality-report",
        ]
        if path_target == "update-master":
            arguments[arguments.index("--master") + 1] = unexpandable
        elif path_target == "update-manifest":
            arguments.remove("--initialize-manifest")
            arguments[arguments.index("--snapshot") : arguments.index("--snapshot")] = [
                "--manifest",
                unexpandable,
            ]
        elif path_target == "update-release-root":
            arguments[arguments.index("--release-root") + 1] = unexpandable
        else:
            arguments[arguments.index("--snapshot") + 1] = f"fictional-main:other={unexpandable}"
    else:
        arguments = [
            "rebase",
            "--master",
            str(FIXTURES / "baseline-master.ged"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--release-root",
            str(tmp_path / "releases"),
            "--reason",
            "Fictional path normalization regression",
        ]
        if path_target == "rebase-master":
            arguments[arguments.index("--master") + 1] = unexpandable
        elif path_target == "rebase-manifest":
            arguments[arguments.index("--manifest") + 1] = unexpandable
        else:
            arguments[arguments.index("--release-root") + 1] = unexpandable

    if raise_errors:
        with pytest.raises(FileIngressError) as raised:
            run_sync(arguments, raise_errors=True)
        assert raised.value.code == "FILE_INPUT_UNREADABLE"
        assert raised.value.exit_code == 2
        rendered = raised.value.render()
    else:
        assert run_sync(arguments) == 2
        rendered = capsys.readouterr().err

    assert "FILE_INPUT_UNREADABLE" in rendered
    assert unexpandable not in rendered


def test_incremental_normalization_boundary_returns_strings() -> None:
    assert sync_algorithms._normal_value("DATE", "July 15, 1850", engine) == "15 JUL 1850"
    assert sync_algorithms._normal_value("CTRY", "USA", engine) == "united states"


def _snapshot(name: str, vendor: str, version: int) -> str:
    return f"{name}:{vendor}={FIXTURES / f'{vendor}-snapshot-v{version}.ged'}"


def _initialize_release(releases: Path) -> Path:
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                _snapshot("ancestry-main", "ancestry", 1),
                "--snapshot",
                _snapshot("myheritage-main", "myheritage", 1),
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--exported-at",
                "myheritage-main=2025-02-03",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    return next(releases.glob("g0001-*"))


def _update_args(releases: Path, bundle: Path, *snapshots: str) -> list[str]:
    return [
        "update",
        "--master",
        str(bundle / "master.ged"),
        "--manifest",
        str(bundle / "manifest.json"),
        "--release-root",
        str(releases),
        "--no-quality-report",
        *(item for snapshot in snapshots for item in ("--snapshot", snapshot)),
    ]


def _initialize_with_snapshots(releases: Path, *snapshots: str) -> Path:
    arguments = [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--release-root",
        str(releases),
        "--no-quality-report",
    ]
    for snapshot in snapshots:
        source_id = snapshot.partition(":")[0]
        arguments.extend(("--snapshot", snapshot, "--exported-at", f"{source_id}=2025-01-15"))
    assert run_sync(arguments) == 0
    return next(releases.glob("g0001-*"))


def _reverse_level_zero_body(source: Path, destination: Path) -> None:
    records: list[list[str]] = []
    for line in source.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith("0 "):
            records.append([])
        records[-1].append(line)
    assert records[0][0].startswith("0 HEAD")
    assert records[-1][0].startswith("0 TRLR")
    destination.write_text(
        "".join(
            line
            for record in [records[0], *reversed(records[1:-1]), records[-1]]
            for line in record
        ),
        encoding="utf-8",
    )


def test_initial_allocation_is_independent_of_snapshot_argument_order(tmp_path: Path) -> None:
    forward = _initialize_with_snapshots(
        tmp_path / "forward",
        _snapshot("ancestry-main", "ancestry", 2),
        _snapshot("myheritage-main", "myheritage", 2),
    )
    reverse = _initialize_with_snapshots(
        tmp_path / "reverse",
        _snapshot("myheritage-main", "myheritage", 2),
        _snapshot("ancestry-main", "ancestry", 2),
    )

    assert (forward / "master.ged").read_bytes() == (reverse / "master.ged").read_bytes()
    forward_manifest = json.loads((forward / "manifest.json").read_text(encoding="utf-8"))
    reverse_manifest = json.loads((reverse / "manifest.json").read_text(encoding="utf-8"))
    for field in ("person_bindings", "record_aliases", "blocks", "next_ids"):
        assert forward_manifest[field] == reverse_manifest[field]


def test_initial_allocation_is_independent_of_snapshot_record_order(tmp_path: Path) -> None:
    reordered = tmp_path / "fictional-reordered-snapshot.ged"
    _reverse_level_zero_body(FIXTURES / "ancestry-snapshot-v2.ged", reordered)
    canonical = _initialize_with_snapshots(
        tmp_path / "canonical",
        _snapshot("ancestry-main", "ancestry", 2),
    )
    reversed_records = _initialize_with_snapshots(
        tmp_path / "reordered",
        f"ancestry-main:ancestry={reordered}",
    )

    assert (canonical / "master.ged").read_bytes() == (reversed_records / "master.ged").read_bytes()
    canonical_manifest = json.loads((canonical / "manifest.json").read_text(encoding="utf-8"))
    reordered_manifest = json.loads(
        (reversed_records / "manifest.json").read_text(encoding="utf-8")
    )
    for field in ("person_bindings", "record_aliases", "next_ids"):
        assert canonical_manifest[field] == reordered_manifest[field]


def test_duplicate_citation_multiplicity_is_preserved_loss_minimally(
    tmp_path: Path,
) -> None:
    bundle = _initialize_with_snapshots(
        tmp_path / "releases",
        _snapshot("myheritage-main", "myheritage", 1),
    )

    master = (bundle / "master.ged").read_text(encoding="utf-8")
    assert master.count("3 PAGE Family Bible, leaf 3\n") == 2


def test_updating_one_origin_preserves_the_other_active_origin(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    prior_myheritage = first_manifest["active_snapshots"]["myheritage-main"]

    assert (
        run_sync(
            _update_args(
                releases,
                first,
                _snapshot("ancestry-main", "ancestry", 2),
            )
        )
        == 0
    )

    second = next(releases.glob("g0002-*"))
    manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["active_snapshots"]["myheritage-main"] == prior_myheritage
    assert (
        manifest["active_snapshots"]["ancestry-main"]
        != (first_manifest["active_snapshots"]["ancestry-main"])
    )
    assert len(manifest["snapshots"]) == 3
    master = (second / "master.ged").read_text(encoding="utf-8")
    assert "3 PAGE Family Bible, leaf 3\n" in master
    assert "1 _MHID myheritage-fictional-elara-v1\n" in master


def test_cancellation_during_incremental_publication_finishes_complete_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    publication_started = threading.Event()
    allow_publication = threading.Event()
    original_publish = sync_publication._publish_directory_no_clobber

    def pause_release_publication(
        staging_name: str,
        destination_name: str,
        release_root,
    ) -> None:
        if destination_name.startswith("g0002-"):
            publication_started.set()
            assert allow_publication.wait(2)
        original_publish(staging_name, destination_name, release_root)

    monkeypatch.setattr(
        sync_publication,
        "_publish_directory_no_clobber",
        pause_release_publication,
    )
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        job = manager.submit(
            "incremental sync",
            lambda: run_sync(
                _update_args(
                    releases,
                    first,
                    _snapshot("ancestry-main", "ancestry", 2),
                )
            ),
        )
        assert publication_started.wait(2)
        pending = manager.cancel(job.job_id)
        assert pending.cancellation_pending is True
        allow_publication.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        allow_publication.set()
        manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    second = next(releases.glob("g0002-*"))
    assert {path.name for path in second.iterdir()} == {
        "master.ged",
        "manifest.json",
        "update.md",
        "quality.md",
        "rollback.json",
    }
    assert not list(releases.glob(".gedcom-sync-*"))


def _new_publication_args(tmp_path: Path, operation: str, release_root: Path) -> list[str]:
    if operation == "update":
        return [
            "update",
            "--master",
            str(FIXTURES / "baseline-master.ged"),
            "--initialize-manifest",
            "--snapshot",
            _snapshot("ancestry-main", "ancestry", 1),
            "--release-root",
            str(release_root),
            "--no-quality-report",
        ]
    source_releases = tmp_path / "source-releases"
    first = _initialize_release(source_releases)
    edited = tmp_path / "fictional-rebase-master.ged"
    edited.write_bytes((first / "master.ged").read_bytes())
    return [
        "rebase",
        "--master",
        str(edited),
        "--manifest",
        str(first / "manifest.json"),
        "--release-root",
        str(release_root),
        "--reason",
        "Fictional publication regression",
    ]


def _assert_cleanup_residue(release_root: Path, operation: str) -> None:
    """Assert the platform's identity-safe failed-publication cleanup contract."""

    prefix = ".gedcom-sync-" if operation == "update" else ".gedcom-rebase-"
    residues = list(release_root.glob(f"{prefix}*"))
    if os.name == "nt":
        assert not residues
        return
    assert len(residues) == 1
    assert residues[0].is_dir()
    assert not any(residues[0].iterdir())


def _assert_candidate_cleanup_residue(parent: Path) -> None:
    candidates = list(parent.glob(".ancestryllm-release-root-*"))
    if os.name == "nt":
        assert not candidates
        return
    assert len(candidates) == 1
    assert candidates[0].is_dir()
    assert not any(candidates[0].iterdir())


@pytest.fixture
def simulated_windows_capabilities(monkeypatch):
    shared_marker_descriptors: set[int] = set()
    state = {
        "published_renames": 0,
        "fail_published_marker": False,
        "marker_delete_failures": 0,
    }
    original_rename = sync_publication._exclusive_rename_directory

    def open_shared_marker(path: Path, *, create: bool) -> int:
        flags = os.O_RDWR
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        shared_marker_descriptors.add(descriptor)
        return descriptor

    def open_delete_descriptor(path: Path, *, directory: bool) -> int:
        flags = os.O_RDONLY
        if directory and hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags)

    def mark_descriptor_for_deletion(descriptor: int) -> None:
        path = sync_publication._held_file_path(descriptor)
        value = os.fstat(descriptor)
        if (
            state["fail_published_marker"]
            and state["marker_delete_failures"] == 0
            and path.name.startswith(".ancestryllm-staging-")
            and path.parent.name.startswith("g")
        ):
            state["marker_delete_failures"] += 1
            raise PermissionError("fictional Windows marker deletion failure")
        if stat.S_ISDIR(value.st_mode):
            os.rmdir(path)
        else:
            os.unlink(path)

    def rename_with_share_delete_assertion(
        source: Path,
        destination: Path,
        *,
        source_dir_fd: int | None = None,
        destination_dir_fd: int | None = None,
    ) -> None:
        source_path = Path(source)
        if source_path.name.startswith((".gedcom-sync-", ".gedcom-rebase-")):
            held_marker = False
            for descriptor in shared_marker_descriptors:
                try:
                    marker_path = sync_publication._held_file_path(descriptor)
                except (OSError, sync_contracts.SyncError, ValueError):
                    continue
                if marker_path.parent == source_path:
                    held_marker = True
                    break
            assert held_marker, "publication must retain a share-delete marker"
            state["published_renames"] += 1
        original_rename(
            source,
            destination,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
        )

    monkeypatch.setattr(sync_publication, "_uses_windows_capability_handles", lambda: True)
    monkeypatch.setattr(sync_publication, "_open_windows_shared_marker", open_shared_marker)
    monkeypatch.setattr(
        sync_publication,
        "_open_windows_delete_descriptor",
        open_delete_descriptor,
    )
    monkeypatch.setattr(
        sync_publication,
        "_windows_mark_descriptor_for_deletion",
        mark_descriptor_for_deletion,
    )
    monkeypatch.setattr(
        sync_publication,
        "_exclusive_rename_directory",
        rename_with_share_delete_assertion,
    )
    return state


def _write_ambiguous_person(path: Path, *, surname: str, birth: str) -> None:
    path.write_text(
        "0 HEAD\n"
        "1 GEDC\n"
        "2 VERS 5.5.5\n"
        "1 CHAR UTF-8\n"
        "0 @I1@ INDI\n"
        f"1 NAME John /{surname}/\n"
        "1 BIRT\n"
        f"2 DATE {birth}\n"
        "2 PLAC Boston, Massachusetts, USA\n"
        "0 TRLR\n",
        encoding="utf-8",
    )


class _ReplaceAfterHashPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def sha256(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        expected: FileSnapshot | None = None,
    ) -> tuple[str, FileSnapshot]:
        result = super().sha256(path, kind, expected=expected)
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True
        return result


class _ReplaceAfterJsonPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def read_json(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        require_object: bool = False,
        expected: FileSnapshot | None = None,
    ) -> object:
        value = super().read_json(
            path,
            kind,
            require_object=require_object,
            expected=expected,
        )
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True
        return value


class _ReplaceAfterTextReadPolicy(FileIngressPolicy):
    def __init__(self, target: Path, replacement: Path) -> None:
        super().__init__()
        self.target = target.absolute()
        self.replacement = replacement
        self.replaced = False

    def iter_text_line_items(
        self,
        path: str | Path,
        kind: FileKind,
        *,
        count_lines_as_records: bool = True,
        expected: FileSnapshot | None = None,
    ) -> Iterator[TextLine]:
        yield from super().iter_text_line_items(
            path,
            kind,
            count_lines_as_records=count_lines_as_records,
            expected=expected,
        )
        if Path(path).absolute() == self.target and not self.replaced:
            os.replace(self.replacement, self.target)
            self.replaced = True


class _LateInputFailurePolicy(FileIngressPolicy):
    def verify(
        self,
        path: str | Path,
        kind: FileKind,
        expected: FileFingerprint,
    ) -> None:
        del path, expected
        raise FileIngressError(
            "FILE_INPUT_CHANGED",
            f"The {kind.value} input changed while it was being consumed.",
        )


class _LateCopyFailurePolicy(FileIngressPolicy):
    def copy_to(
        self,
        path: str | Path,
        destination: str | Path,
        kind: FileKind,
        *,
        expected: FileFingerprint,
    ) -> None:
        del path, destination, expected
        raise FileIngressError(
            "FILE_INPUT_IO",
            f"The {kind.value} input could not be copied safely.",
        )


def _replace_copy(path: Path, old: bytes, new: bytes) -> Path:
    replacement = path.with_name(path.name + ".replacement")
    payload = path.read_bytes()
    assert len(old) == len(new)
    assert old in payload
    replacement.write_bytes(payload.replace(old, new, 1))
    assert replacement.stat().st_size == path.stat().st_size
    return replacement


def test_update_rejects_snapshot_replaced_after_verified_hash(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    master.write_bytes((FIXTURES / "baseline-master.ged").read_bytes())
    snapshot = tmp_path / "snapshot.ged"
    snapshot.write_bytes((FIXTURES / "ancestry-snapshot-v1.ged").read_bytes())
    replacement = _replace_copy(snapshot, b"Mira", b"Zira")
    releases = tmp_path / "releases"
    policy = _ReplaceAfterHashPolicy(snapshot, replacement)

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            f"fictional:other={snapshot}",
            "--release-root",
            str(releases),
            "--no-quality-report",
        ],
        policy,
    )

    assert status == 2
    assert not list(releases.glob("g*-*"))
    assert not list(releases.glob(".gedcom-sync-*"))


def test_update_rejects_master_replaced_after_verified_hash(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    master.write_bytes((FIXTURES / "baseline-master.ged").read_bytes())
    replacement = _replace_copy(master, b"Mira", b"Zira")
    releases = tmp_path / "releases"
    policy = _ReplaceAfterHashPolicy(master, replacement)

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            _snapshot("ancestry-main", "ancestry", 1),
            "--release-root",
            str(releases),
            "--no-quality-report",
        ],
        policy,
    )

    assert status == 2
    assert not list(releases.glob("g*-*"))


def test_incremental_provider_is_not_called_when_snapshot_changes_after_parse(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")
    replacement = _replace_copy(snapshot, b"Smyth", b"Jmyth")
    policy = _ReplaceAfterTextReadPolicy(snapshot, replacement)
    provider_calls: list[tuple[str, str]] = []

    def resolver_factory(_provider: str, _model: str, _consent: str | None):
        def resolve(left, right):
            provider_calls.append((left.pointer, right.pointer))
            return {}

        return resolve

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            f"fictional-main:other={snapshot}",
            "--release-root",
            str(releases),
            "--no-quality-report",
            "--provider",
            "fictional",
            "--model",
            "fixture",
        ],
        policy,
        resolver_factory=resolver_factory,
    )

    assert status == 2
    assert policy.replaced
    assert provider_calls == []
    assert not list(releases.glob("g*-*"))
    assert not list(releases.glob(".gedcom-sync-*"))


def test_incremental_provider_postcheck_rejects_mutation_during_adjudication(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")
    replacement = _replace_copy(snapshot, b"Smyth", b"Jmyth")
    provider_calls: list[tuple[str, str]] = []

    def resolver_factory(_provider: str, _model: str, _consent: str | None):
        def resolve(left, right):
            provider_calls.append((left.pointer, right.pointer))
            os.replace(replacement, snapshot)
            return {}

        return resolve

    status = run_sync(
        [
            "update",
            "--master",
            str(master),
            "--initialize-manifest",
            "--snapshot",
            f"fictional-main:other={snapshot}",
            "--release-root",
            str(releases),
            "--no-quality-report",
            "--provider",
            "fictional",
            "--model",
            "fixture",
        ],
        resolver_factory=resolver_factory,
    )

    assert status == 2
    assert len(provider_calls) == 1
    assert not list(releases.glob("g*-*"))
    assert not list(releases.glob(".gedcom-sync-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_sync_rejects_manifest_replaced_after_parse(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    manifest = first / "manifest.json"
    replacement = _replace_copy(manifest, b'"generation": 1', b'"generation": 9')
    policy = _ReplaceAfterJsonPolicy(manifest, replacement)
    if operation == "update":
        arguments = _update_args(
            releases,
            first,
            _snapshot("ancestry-main", "ancestry", 2),
        )
    else:
        edited = tmp_path / "edited.ged"
        edited.write_bytes((first / "master.ged").read_bytes())
        arguments = [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(manifest),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional manifest race regression",
        ]

    assert run_sync(arguments, policy) == 2
    assert len(list(releases.glob("g*-*"))) == 1


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    (
        ("generation", []),
        ("snapshots", {"broken": []}),
        (
            "blocks",
            {
                "@I1@": {
                    "hash": {
                        "tag": "NAME",
                        "observations": "not-a-list",
                        "protected": [],
                    }
                }
            },
        ),
        ("manual_tombstones", [{"person": "@I1@"}]),
    ),
)
def test_malformed_manifest_is_stable_and_never_publishes_or_writes_failure_artifacts(
    tmp_path: Path,
    capsys,
    field_name: str,
    malformed_value: object,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    payload = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    payload[field_name] = malformed_value
    malformed = tmp_path / "private-malformed-manifest.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    arguments = _update_args(
        releases,
        first,
        _snapshot("ancestry-main", "ancestry", 2),
    )
    arguments[arguments.index("--manifest") + 1] = str(malformed)

    assert run_sync(arguments) != 0
    rendered = capsys.readouterr().err
    assert "MANIFEST_INVALID" in rendered
    assert str(malformed) not in rendered
    with pytest.raises(AncestryError) as raised:
        GedcomService().sync(arguments)

    assert raised.value.code == "MANIFEST_INVALID"
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-sync-*"))
    assert not list(releases.glob("failed-update-*"))


@pytest.mark.parametrize(
    "corruption",
    (
        "active-source-mismatch",
        "missing-active-source",
        "snapshot-key",
        "snapshot-source-id",
        "snapshot-vendor",
        "mixed-vendor-history",
        "observed-at",
        "unknown-observation",
    ),
)
def test_manifest_rejects_broken_snapshot_identity_and_provenance(
    tmp_path: Path,
    capsys,
    corruption: str,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    payload = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    active_id = payload["active_snapshots"]["ancestry-main"]
    active_snapshot = payload["snapshots"][active_id]

    if corruption == "active-source-mismatch":
        payload["active_snapshots"]["fictional-source"] = payload["active_snapshots"].pop(
            "ancestry-main"
        )
    elif corruption == "missing-active-source":
        payload["active_snapshots"].pop("ancestry-main")
    elif corruption == "snapshot-key":
        malformed_id = "ancestry-main:00000000000000000000"
        payload["snapshots"][malformed_id] = payload["snapshots"].pop(active_id)
        payload["snapshots"][malformed_id]["snapshot_id"] = malformed_id
        payload["active_snapshots"]["ancestry-main"] = malformed_id
    elif corruption == "snapshot-source-id":
        active_snapshot["source_id"] = "Private/Source"
    elif corruption == "snapshot-vendor":
        active_snapshot["vendor"] = "private-vendor"
    elif corruption == "mixed-vendor-history":
        second = dict(active_snapshot)
        second["sha256"] = "0" * 64
        second["snapshot_id"] = "ancestry-main:" + "0" * 20
        second["vendor"] = "other"
        payload["snapshots"][second["snapshot_id"]] = second
    elif corruption == "observed-at":
        active_snapshot["observed_at"] = "not-a-timestamp"
    else:
        block = next(entry for entries in payload["blocks"].values() for entry in entries.values())
        block["observations"] = ["ancestry-main:" + "f" * 20]

    malformed = tmp_path / f"private-{corruption}-manifest.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    arguments = _update_args(
        releases,
        first,
        _snapshot("ancestry-main", "ancestry", 2),
    )
    arguments[arguments.index("--manifest") + 1] = str(malformed)

    assert run_sync(arguments) == sync_contracts.EXIT_CODES["MANIFEST_INVALID"]
    rendered = capsys.readouterr().err
    assert "MANIFEST_INVALID" in rendered
    assert str(malformed) not in rendered
    with pytest.raises(AncestryError) as raised:
        run_sync(arguments, raise_errors=True)
    assert raised.value.code == "MANIFEST_INVALID"
    assert str(malformed) not in raised.value.render()
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-sync-*"))
    assert not list(releases.glob("failed-update-*"))


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    (
        ("release-generation-gap", "MANIFEST_INVALID"),
        ("release-master-fingerprint", "MANIFEST_INVALID"),
        ("parent-generation", "MANIFEST_INVALID"),
        ("artifact-fingerprint", "MANIFEST_MASTER_MISMATCH"),
    ),
)
def test_manifest_rejects_incoherent_release_lineage_and_artifact_fingerprints(
    tmp_path: Path,
    capsys,
    corruption: str,
    expected_code: str,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    payload = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    if corruption == "release-generation-gap":
        payload["releases"][0]["generation"] = 2
    elif corruption == "release-master-fingerprint":
        payload["releases"][0]["master_sha256"] = "0" * 64
    elif corruption == "parent-generation":
        payload["parent_release"]["generation"] = 9
    else:
        payload["artifact_checksums"]["update.md"] = "0" * 64
    malformed = first / f"private-{corruption}-manifest.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    arguments = _update_args(
        releases,
        first,
        _snapshot("ancestry-main", "ancestry", 2),
    )
    arguments[arguments.index("--manifest") + 1] = str(malformed)

    assert run_sync(arguments) == sync_contracts.EXIT_CODES[expected_code]
    assert expected_code in capsys.readouterr().err
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-sync-*"))


@pytest.mark.parametrize("raise_errors", (False, True))
def test_update_rejects_vendor_identity_change_for_existing_source(
    tmp_path: Path,
    capsys,
    raise_errors: bool,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    arguments = _update_args(
        releases,
        first,
        f"ancestry-main:other={FIXTURES / 'ancestry-snapshot-v1.ged'}",
    )

    if raise_errors:
        with pytest.raises(AncestryError) as raised:
            run_sync(arguments, raise_errors=True)
        rendered = raised.value.render()
    else:
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
        rendered = capsys.readouterr().err

    assert "SYNC_CONFIGURATION" in rendered
    assert "vendor" in rendered.lower()
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-sync-*"))
    assert not list(releases.glob("failed-update-*"))


def test_rebase_rejects_tampered_manifest_parent_before_indexing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    prior_master = first / "master.ged"
    edited = tmp_path / "edited.ged"
    edited.write_bytes(prior_master.read_bytes())
    replacement = _replace_copy(prior_master, b"Mira", b"Zira")
    os.replace(replacement, prior_master)
    index = Mock(side_effect=AssertionError("indexing must not run"))
    monkeypatch.setattr(sync_operations, "_master_block_index", index)

    status = run_sync(
        [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(first / "manifest.json"),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional rebase hash-gate regression",
        ]
    )

    assert status == sync_contracts.EXIT_CODES["MANIFEST_MASTER_MISMATCH"]
    assert "MANIFEST_MASTER_MISMATCH" in capsys.readouterr().err
    index.assert_not_called()
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-sync-*"))
    assert not list(releases.glob("failed-update-*"))


def test_rebase_rejects_master_replaced_after_parse_before_copy(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited = tmp_path / "edited.ged"
    edited.write_bytes((first / "master.ged").read_bytes())
    replacement = _replace_copy(edited, b"Mira", b"Zira")
    policy = _ReplaceAfterTextReadPolicy(edited, replacement)

    status = run_sync(
        [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(first / "manifest.json"),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional master race regression",
        ],
        policy,
    )

    assert status == 2
    assert len(list(releases.glob("g*-*"))) == 1


def test_incremental_initialization_is_offline_and_publishes_atomic_bundle(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    with (
        patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "must-not-be-read", "GEMINI_API_KEY": "must-not-be-read"},
        ),
        patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network forbidden"),
        ),
    ):
        status = run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--exported-at",
                "ancestry-main=2025-01-15",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
    assert status == 0
    bundles = list(releases.glob("g0001-*"))
    assert len(bundles) == 1
    assert {path.name for path in bundles[0].iterdir()} == {
        "master.ged",
        "manifest.json",
        "update.md",
        "quality.md",
        "rollback.json",
    }
    manifest = json.loads((bundles[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 1
    assert "LLM provider: `none` (offline deterministic)" in (bundles[0] / "update.md").read_text(
        encoding="utf-8"
    )


def test_incremental_update_uses_the_injected_modular_resolver(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")
    selections: list[tuple[str, str, str | None]] = []
    pairs: list[tuple[str, str]] = []

    def resolver_factory(provider: str, model: str, consent: str | None):
        selections.append((provider, model, consent))

        def resolve(left, right):
            pairs.append((left.pointer, right.pointer))
            return {
                "is_duplicate": True,
                "confidence": 0.95,
                "reasoning": "Fictional records agree.",
                "preferred_values": {},
                "_provider": provider,
                "_model": model,
            }

        return resolve

    assert (
        run_sync(
            [
                "update",
                "--master",
                str(master),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={snapshot}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "ollama",
                "--model",
                "fixture-model",
                "--consent",
                "fixture-consent",
            ],
            resolver_factory=resolver_factory,
        )
        == 0
    )

    bundle = next(releases.glob("g0001-*"))
    assert selections == [("ollama", "fixture-model", "fixture-consent")]
    assert len(pairs) == 1
    assert (bundle / "master.ged").read_text(encoding="utf-8").count(" INDI") == 1
    report = (bundle / "update.md").read_text(encoding="utf-8")
    assert "LLM provider: `ollama` (explicit opt-in)" in report
    assert "LLM ollama/fixture-model" in report


def test_incremental_provider_failure_propagates_without_publishing(tmp_path: Path) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    releases = tmp_path / "releases"
    _write_ambiguous_person(master, surname="Smith", birth="1850")
    _write_ambiguous_person(snapshot, surname="Smyth", birth="1851")

    def resolver_factory(_provider: str, _model: str, _consent: str | None):
        def fail(_left, _right):
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                "The ollama request timed out before output began.",
                details={"error_type": "TimeoutError"},
            )

        return fail

    with pytest.raises(ProviderError) as raised:
        run_sync(
            [
                "update",
                "--master",
                str(master),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={snapshot}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "ollama",
                "--model",
                "fixture-model",
            ],
            resolver_factory=resolver_factory,
        )

    assert raised.value.code == "PROVIDER_TIMEOUT"
    assert not list(releases.glob("g*-*"))


def test_incremental_rejects_the_retired_direct_backend_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(FIXTURES / "baseline-master.ged"),
                "--initialize-manifest",
                "--snapshot",
                _snapshot("ancestry-main", "ancestry", 1),
                "--release-root",
                str(tmp_path / "releases"),
                "--no-quality-report",
                "--ai-backend",
                "ollama",
            ]
        )
        == 2
    )


def test_incremental_active_snapshot_is_idempotent(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first_args = [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--snapshot",
        f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
        "--release-root",
        str(releases),
        "--no-quality-report",
    ]
    assert run_sync(first_args) == 0
    bundle = next(releases.glob("g0001-*"))
    assert (
        run_sync(
            [
                "update",
                "--master",
                str(bundle / "master.ged"),
                "--manifest",
                str(bundle / "manifest.json"),
                "--snapshot",
                f"ancestry-main:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
                "--release-root",
                str(releases),
                "--no-quality-report",
            ]
        )
        == 0
    )
    assert len(list(releases.glob("g*-*"))) == 1


def test_incremental_replaces_changed_snapshots_and_preserves_snapshot_history(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert (
        run_sync(
            _update_args(
                releases,
                first,
                _snapshot("ancestry-main", "ancestry", 2),
                _snapshot("myheritage-main", "myheritage", 2),
            )
        )
        == 0
    )

    second = next(releases.glob("g0002-*"))
    manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 2
    assert len(manifest["snapshots"]) == 4
    assert manifest["active_snapshots"]["ancestry-main"] in manifest["snapshots"]
    assert manifest["active_snapshots"]["myheritage-main"] in manifest["snapshots"]
    assert manifest["snapshots"][manifest["active_snapshots"]["ancestry-main"]]["path"].endswith(
        "ancestry-snapshot-v2.ged"
    )

    master = (second / "master.ged").read_text(encoding="utf-8")
    assert "0 @I100@ INDI" in master
    assert "Ilyan /Shore/" in master
    assert "@AX-15@" not in master
    assert "School librarian" not in master
    assert "Cedar Bay, Vermont, United States" in master


def test_rebase_requires_confirmation_then_tombstones_manual_deletions(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited_master = tmp_path / "fictional-manual-deletion.ged"
    edited_master.write_text(
        (first / "master.ged").read_text(encoding="utf-8").replace("1 OCCU Cartographer\n", ""),
        encoding="utf-8",
    )
    rebase_args = [
        "rebase",
        "--master",
        str(edited_master),
        "--manifest",
        str(first / "manifest.json"),
        "--release-root",
        str(releases),
        "--reason",
        "Fictional manual correction for regression coverage",
    ]

    def forbidden_resolver_factory(_provider: str, _model: str, _consent: str | None) -> None:
        raise AssertionError("rebase must never construct a provider resolver")

    assert run_sync(rebase_args, resolver_factory=forbidden_resolver_factory) == 6
    assert len(list(releases.glob("g*-*"))) == 1

    assert (
        run_sync(
            [*rebase_args, "--accept-manual-deletions"],
            resolver_factory=forbidden_resolver_factory,
        )
        == 0
    )
    rebased = next(releases.glob("g0002-*"))
    rebase_manifest = json.loads((rebased / "manifest.json").read_text(encoding="utf-8"))
    assert rebase_manifest["manual_tombstones"]
    assert "Cartographer" not in (rebased / "master.ged").read_text(encoding="utf-8")

    assert run_sync(_update_args(releases, rebased, _snapshot("ancestry-main", "ancestry", 2))) == 0
    updated = next(releases.glob("g0003-*"))
    assert "Cartographer" not in (updated / "master.ged").read_text(encoding="utf-8")
    assert "intentional manual deletion" in (updated / "update.md").read_text(encoding="utf-8")


def test_changed_fact_cannot_bypass_a_manual_tombstone(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    edited_master = tmp_path / "fictional-manual-deletion.ged"
    edited_master.write_text(
        (first / "master.ged").read_text(encoding="utf-8").replace("1 OCCU Cartographer\n", ""),
        encoding="utf-8",
    )
    assert (
        run_sync(
            [
                "rebase",
                "--master",
                str(edited_master),
                "--manifest",
                str(first / "manifest.json"),
                "--release-root",
                str(releases),
                "--reason",
                "Fictional occupation removal",
                "--accept-manual-deletions",
            ]
        )
        == 0
    )
    rebased = next(releases.glob("g0002-*"))
    changed_snapshot = tmp_path / "fictional-changed-occupation.ged"
    changed_snapshot.write_text(
        (FIXTURES / "ancestry-snapshot-v2.ged")
        .read_text(encoding="utf-8")
        .replace("1 OCCU Cartographer\n", "1 OCCU Surveyor\n"),
        encoding="utf-8",
    )

    assert (
        run_sync(
            _update_args(
                releases,
                rebased,
                f"ancestry-main:ancestry={changed_snapshot}",
            )
        )
        == 0
    )
    updated = next(releases.glob("g0003-*"))
    assert "Surveyor" not in (updated / "master.ged").read_text(encoding="utf-8")
    assert "intentional manual deletion" in (updated / "update.md").read_text(encoding="utf-8")


def test_explicit_manual_revival_retires_the_matching_tombstone(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    deleted_master = tmp_path / "fictional-deleted-occupation.ged"
    deleted_master.write_text(
        (first / "master.ged").read_text(encoding="utf-8").replace("1 OCCU Cartographer\n", ""),
        encoding="utf-8",
    )
    common_rebase = [
        "--manifest",
        str(first / "manifest.json"),
        "--release-root",
        str(releases),
        "--reason",
        "Fictional reviewed correction",
    ]
    assert (
        run_sync(
            [
                "rebase",
                "--master",
                str(deleted_master),
                *common_rebase,
                "--accept-manual-deletions",
            ]
        )
        == 0
    )
    rebased = next(releases.glob("g0002-*"))
    revived_master = tmp_path / "fictional-revived-occupation.ged"
    revived_master.write_text(
        (rebased / "master.ged")
        .read_text(encoding="utf-8")
        .replace(
            "1 NAME Rowan /Vale/\n2 GIVN Rowan\n2 SURN Vale\n",
            "1 NAME Rowan /Vale/\n2 GIVN Rowan\n2 SURN Vale\n1 OCCU Cartographer\n",
        ),
        encoding="utf-8",
    )

    assert (
        run_sync(
            [
                "rebase",
                "--master",
                str(revived_master),
                "--manifest",
                str(rebased / "manifest.json"),
                "--release-root",
                str(releases),
                "--reason",
                "Fictional explicit restoration",
            ]
        )
        == 0
    )
    revived = next(releases.glob("g0003-*"))
    manifest = json.loads((revived / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manual_tombstones"] == []
    assert "Cartographer" in (revived / "master.ged").read_text(encoding="utf-8")


def test_manual_deletion_authorization_applies_only_to_the_reviewed_rebase(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    first_edit = tmp_path / "fictional-first-reviewed-deletion.ged"
    first_edit.write_text(
        (first / "master.ged")
        .read_text(encoding="utf-8")
        .replace(
            "1 OCCU Cartographer\n",
            "",
        ),
        encoding="utf-8",
    )
    assert (
        run_sync(
            [
                "rebase",
                "--master",
                str(first_edit),
                "--manifest",
                str(first / "manifest.json"),
                "--release-root",
                str(releases),
                "--reason",
                "Fictional first reviewed deletion",
                "--accept-manual-deletions",
            ]
        )
        == 0
    )

    rebased = next(releases.glob("g0002-*"))
    second_edit = tmp_path / "fictional-second-unreviewed-deletion.ged"
    second_master = (rebased / "master.ged").read_text(encoding="utf-8")
    assert "1 OCCU School librarian\n" in second_master
    second_edit.write_text(
        second_master.replace("1 OCCU School librarian\n", ""),
        encoding="utf-8",
    )

    assert (
        run_sync(
            [
                "rebase",
                "--master",
                str(second_edit),
                "--manifest",
                str(rebased / "manifest.json"),
                "--release-root",
                str(releases),
                "--reason",
                "Fictional second unreviewed deletion",
            ]
        )
        == sync_contracts.EXIT_CODES["SYNC_UNSAFE_REMOVAL"]
    )
    assert len(list(releases.glob("g*-*"))) == 2


def test_update_writes_rollback_metadata_and_cleans_up_interrupted_publish(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)

    assert run_sync(_update_args(releases, first, _snapshot("ancestry-main", "ancestry", 2))) == 0
    second = next(releases.glob("g0002-*"))
    rollback = json.loads((second / "rollback.json").read_text(encoding="utf-8"))
    assert rollback["current_generation"] == 2
    assert rollback["previous"]["generation"] == 1
    assert rollback["previous"]["master"]["path"] == str(first / "master.ged")
    assert (first / "master.ged").is_file()

    with patch.object(
        sync_publication,
        "_publish_directory_no_clobber",
        side_effect=OSError("disk full"),
    ):
        assert (
            run_sync(_update_args(releases, second, _snapshot("myheritage-main", "myheritage", 2)))
            == 7
        )

    assert sorted(path.name[:5] for path in releases.glob("g*-*")) == ["g0001", "g0002"]
    _assert_cleanup_residue(releases, "update")


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_sync_publish_never_replaces_a_concurrent_final_directory(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"{operation}-releases"
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_publish = sync_publication._publish_directory_no_clobber
    concurrent_destination: Path | None = None

    def publish_after_concurrent_claim(
        staging_name: str,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
    ) -> None:
        nonlocal concurrent_destination
        concurrent_destination = (
            sync_publication._capability_current_path(release_root) / destination_name
        )
        concurrent_destination.mkdir()
        try:
            original_publish(staging_name, destination_name, release_root)
        except Exception:
            (concurrent_destination / "concurrent-sentinel.txt").write_text(
                "preserve concurrent owner",
                encoding="utf-8",
            )
            raise

    with patch.object(
        sync_publication,
        "_publish_directory_no_clobber",
        side_effect=publish_after_concurrent_claim,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert concurrent_destination is not None
    assert (concurrent_destination / "concurrent-sentinel.txt").read_text(
        encoding="utf-8"
    ) == "preserve concurrent owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_sync_detects_release_root_swap_during_final_rename_without_touching_foreign_root(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
) -> None:
    releases = tmp_path / f"{operation}-selected-releases"
    original_sentinel = releases / "original-sentinel.txt"
    if preexisting:
        releases.mkdir()
        original_sentinel.write_text("preserve original owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    moved_root = tmp_path / f"{operation}-moved-original"
    foreign_sentinel = releases / "foreign-sentinel.txt"
    original_rename = sync_publication._exclusive_rename_directory
    swapped = False

    def rename_after_root_swap(
        source: Path,
        destination: Path,
        *,
        source_dir_fd: int | None = None,
        destination_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if (
            not swapped
            and source.name.startswith(".gedcom-")
            and destination.name.startswith("g")
            and source_dir_fd is not None
        ):
            swapped = True
            os.rename(releases, moved_root)
            releases.mkdir()
            foreign_sentinel.write_text("preserve foreign owner", encoding="utf-8")
        original_rename(
            source,
            destination,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
        )

    with patch.object(
        sync_publication,
        "_exclusive_rename_directory",
        side_effect=rename_after_root_swap,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert swapped
    assert foreign_sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert not list(releases.glob("g*-*"))
    assert not list(releases.glob(".gedcom-*"))
    if preexisting:
        assert (moved_root / original_sentinel.name).read_text(
            encoding="utf-8"
        ) == "preserve original owner"
    _assert_cleanup_residue(moved_root, operation)


@pytest.mark.parametrize(
    ("inode", "changed_offset"),
    ((0, 0), (None, 1)),
)
def test_staging_cleanup_rejects_untrusted_or_reused_directory_identity(
    tmp_path: Path,
    inode: int | None,
    changed_offset: int,
) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    release_capability = sync_publication._open_directory_capability(releases, owned=False)
    (
        staging,
        staging_name,
        descriptor,
        expected,
        marker_name,
        marker_descriptor,
        marker_identity,
    ) = sync_publication._create_staging_directory(release_capability, ".gedcom-sync-")
    assert descriptor is not None
    moved_owned = tmp_path / "moved-owned-staging"
    os.rename(staging, moved_owned)
    staging.mkdir()
    sentinel = staging / "foreign-sentinel.txt"
    sentinel.write_text("preserve replacement", encoding="utf-8")
    os.link(moved_owned / marker_name, staging / marker_name)
    real_lstat = os.lstat

    def untrusted_lstat(path: str | os.PathLike[str]):
        value = real_lstat(path)
        if Path(path) != staging:
            return value
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=expected.device,
            st_ino=inode if inode is not None else expected.inode,
            st_ctime_ns=expected.changed_ns + changed_offset,
            st_birthtime_ns=expected.birth_ns,
        )

    with patch.object(sync_publication.os, "lstat", side_effect=untrusted_lstat):
        sync_publication._cleanup_staging_directory(
            release_capability,
            staging_name,
            descriptor,
            expected,
            marker_name,
            marker_descriptor,
            marker_identity,
        )

    sync_publication._close_capability_quietly(release_capability)
    assert sentinel.read_text(encoding="utf-8") == "preserve replacement"


def test_windows_marker_handle_uses_delete_sharing_for_create_and_reopen() -> None:
    calls: list[dict[str, int | Path]] = []
    handles = iter((101, 102))
    descriptors = iter((201, 202))

    def create_handle(
        path: Path,
        *,
        access: int,
        share: int,
        creation: int,
        flags: int,
    ) -> int:
        calls.append(
            {
                "path": path,
                "access": access,
                "share": share,
                "creation": creation,
                "flags": flags,
            }
        )
        return next(handles)

    with (
        patch.object(
            sync_publication,
            "_windows_create_file_handle",
            side_effect=create_handle,
        ),
        patch.object(
            sync_publication,
            "_windows_descriptor_from_handle",
            side_effect=lambda _handle, _flags: next(descriptors),
        ),
    ):
        assert sync_publication._open_windows_shared_marker(Path("marker"), create=True) == 201
        assert sync_publication._open_windows_shared_marker(Path("marker"), create=False) == 202

    assert len(calls) == 2
    for call in calls:
        assert int(call["access"]) & 0x80000000
        assert int(call["access"]) & 0x40000000
        assert int(call["access"]) & 0x00010000
        assert call["share"] == 0x00000001 | 0x00000002 | 0x00000004
        assert int(call["flags"]) & 0x00200000
    assert calls[0]["creation"] == 1
    assert calls[1]["creation"] == 3


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_simulated_windows_publication_keeps_share_delete_markers_open(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
    simulated_windows_capabilities,
) -> None:
    releases = tmp_path / f"windows-{operation}-releases"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)

    assert run_sync(arguments) == 0

    assert simulated_windows_capabilities["published_renames"] >= 1
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".gedcom-*"))
    assert not list(releases.glob(".ancestryllm-*"))
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_simulated_windows_marker_failure_rolls_back_and_cleans(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
    simulated_windows_capabilities,
) -> None:
    releases = tmp_path / f"windows-rollback-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    simulated_windows_capabilities["fail_published_marker"] = True

    assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert simulated_windows_capabilities["marker_delete_failures"] == 1
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
        assert sorted(path.name for path in releases.iterdir()) == [sentinel.name]
    else:
        assert not releases.exists()


def test_staging_cleanup_stops_after_a_late_directory_swap(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    release_capability = sync_publication._open_directory_capability(releases, owned=False)
    (
        staging,
        staging_name,
        descriptor,
        expected,
        marker_name,
        marker_descriptor,
        marker_identity,
    ) = sync_publication._create_staging_directory(release_capability, ".gedcom-sync-")
    assert descriptor is not None
    moved_owned = tmp_path / "moved-owned-staging"
    sentinel = staging / "foreign-sentinel.txt"
    original_listdir = os.listdir
    swapped = False

    def swap_before_preflight(path):
        nonlocal swapped
        if not swapped and path == descriptor:
            swapped = True
            os.rename(staging, moved_owned)
            staging.mkdir()
            sentinel.write_text("preserve foreign owner", encoding="utf-8")
        return original_listdir(path)

    with patch.object(sync_publication.os, "listdir", side_effect=swap_before_preflight):
        sync_publication._cleanup_staging_directory(
            release_capability,
            staging_name,
            descriptor,
            expected,
            marker_name,
            marker_descriptor,
            marker_identity,
        )

    sync_publication._close_capability_quietly(release_capability)
    assert swapped
    assert sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert (moved_owned / marker_name).is_file()


def test_capability_tree_cleanup_stops_after_a_late_directory_swap(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "owned-candidate"
    candidate.mkdir()
    capability = sync_publication._open_directory_capability(candidate, owned=True)
    assert capability.descriptor is not None
    descriptor = capability.descriptor
    marker_name = capability.marker_name
    moved_owned = tmp_path / "moved-owned-candidate"
    sentinel = candidate / "foreign-sentinel.txt"
    original_listdir = os.listdir
    swapped = False

    def swap_before_preflight(path):
        nonlocal swapped
        if not swapped and path == descriptor:
            swapped = True
            os.rename(candidate, moved_owned)
            candidate.mkdir()
            sentinel.write_text("preserve foreign owner", encoding="utf-8")
        return original_listdir(path)

    with patch.object(sync_publication.os, "listdir", side_effect=swap_before_preflight):
        sync_publication._cleanup_capability_tree(capability)

    assert swapped
    assert sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert (moved_owned / marker_name).is_file()


def test_failure_contract_preserves_existing_sentinel_and_creates_no_report(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    sentinel = releases / "failed-update-20260726T120000Z.md"
    sentinel.write_text("preserve existing report", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, "update", releases)

    with patch.object(sync_publication, "_write_bytes", side_effect=OSError("disk full")):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert sentinel.read_text(encoding="utf-8") == "preserve existing report"
    assert sentinel.name in {path.name for path in releases.iterdir()}


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_missing_release_parent_is_rejected_without_creating_output(
    tmp_path: Path,
    capsys,
    operation: str,
) -> None:
    missing_parent = tmp_path / "private-missing-parent"
    releases = missing_parent / "releases"
    arguments = _new_publication_args(tmp_path, operation, releases)

    assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    rendered = capsys.readouterr().err
    assert "SYNC_OUTPUT" in rendered
    assert str(missing_parent) not in rendered
    assert not missing_parent.exists()
    assert not list(tmp_path.rglob(".ancestryllm-release-root-*"))
    assert not list(tmp_path.rglob("failed-update-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_late_sync_failure_leaves_only_safe_empty_residue(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"new-{operation}-releases"
    arguments = _new_publication_args(tmp_path, operation, releases)

    with patch.object(sync_publication, "_write_bytes", side_effect=OSError("disk full")):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_late_input_failure_leaves_only_safe_empty_residue(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"late-input-{operation}-releases"
    arguments = _new_publication_args(tmp_path, operation, releases)

    assert (
        run_sync(arguments, _LateInputFailurePolicy())
        == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
    )

    _assert_cleanup_residue(releases, operation)


def test_late_rebase_copy_failure_leaves_only_safe_empty_residue(tmp_path: Path) -> None:
    releases = tmp_path / "late-copy-rebase-releases"
    arguments = _new_publication_args(tmp_path, "rebase", releases)

    assert (
        run_sync(arguments, _LateCopyFailurePolicy())
        == sync_contracts.EXIT_CODES["SYNC_CONFIGURATION"]
    )

    _assert_cleanup_residue(releases, "rebase")


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_late_sync_failure_preserves_preexisting_release_root_and_sentinel(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"existing-{operation}-releases"
    releases.mkdir()
    sentinel = releases / "concurrent-sentinel.txt"
    sentinel.write_text("preserve preexisting owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)

    with patch.object(sync_publication, "_write_bytes", side_effect=OSError("disk full")):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert sentinel.read_text(encoding="utf-8") == "preserve preexisting owner"
    assert releases.is_dir()
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_marker_delete_failure_rolls_back_before_reporting_failure(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
) -> None:
    releases = tmp_path / f"marker-failure-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_remove = sync_publication._remove_published_staging_marker
    marker_failures = 0

    def fail_published_marker_once(
        marker_name: str,
        marker_identity: sync_publication._DirectoryIdentity,
        directory_descriptor: int | None,
        directory_identity: sync_publication._DirectoryIdentity,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
        transaction: sync_publication._PublicationTransactionState,
    ) -> BaseException | None:
        nonlocal marker_failures
        if marker_failures == 0:
            marker_failures += 1
            assert transaction.marker_descriptor is not None
            return PermissionError("fictional marker deletion failure")
        return original_remove(
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            destination_name,
            release_root,
            transaction,
        )

    with patch.object(
        sync_publication,
        "_remove_published_staging_marker",
        side_effect=fail_published_marker_once,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert marker_failures == 1
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_marker_fstat_failure_rolls_back_and_cleans(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
) -> None:
    releases = tmp_path / f"marker-fstat-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_fstat = sync_publication.os.fstat
    marker_failures = 0

    def fail_published_marker_fstat_once(descriptor: int):
        nonlocal marker_failures
        try:
            selected = sync_publication._held_file_path(descriptor)
        except (OSError, sync_contracts.SyncError, ValueError):
            selected = Path()
        if (
            marker_failures == 0
            and selected.name.startswith(".ancestryllm-staging-")
            and selected.parent.name.startswith("g")
        ):
            marker_failures += 1
            raise OSError("fictional marker fstat failure")
        return original_fstat(descriptor)

    with patch.object(
        sync_publication.os,
        "fstat",
        side_effect=fail_published_marker_fstat_once,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert marker_failures == 1
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_marker_delete_failure_retains_descriptor_through_rollback(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
) -> None:
    releases = tmp_path / f"marker-reopen-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_remove = sync_publication._remove_published_staging_marker
    original_rollback = sync_publication._rollback_published_directory
    marker_failures = 0
    retained_descriptor: int | None = None
    retained_during_rollback = False

    def fail_published_marker_once(
        marker_name: str,
        marker_identity: sync_publication._DirectoryIdentity,
        directory_descriptor: int | None,
        directory_identity: sync_publication._DirectoryIdentity,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
        transaction: sync_publication._PublicationTransactionState,
    ) -> BaseException | None:
        nonlocal marker_failures, retained_descriptor
        if marker_failures == 0:
            marker_failures += 1
            retained_descriptor = transaction.marker_descriptor
            assert retained_descriptor is not None
            assert os.fstat(retained_descriptor).st_ino > 0
            return PermissionError("fictional marker deletion failure")
        return original_remove(
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            destination_name,
            release_root,
            transaction,
        )

    def assert_retained_then_rollback(
        staging_name: str,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
    ) -> bool:
        nonlocal retained_during_rollback
        assert retained_descriptor is not None
        assert os.fstat(retained_descriptor).st_ino > 0
        assert sync_publication._held_file_path(retained_descriptor).parent.name == destination_name
        retained_during_rollback = True
        return original_rollback(staging_name, destination_name, release_root)

    with (
        patch.object(
            sync_publication,
            "_remove_published_staging_marker",
            side_effect=fail_published_marker_once,
        ),
        patch.object(
            sync_publication,
            "_rollback_published_directory",
            side_effect=assert_retained_then_rollback,
        ),
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert marker_failures == 1
    assert retained_during_rollback
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_moved_destination_and_failed_rollback_never_claims_commit(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"moved-destination-{operation}"
    releases.mkdir()
    sentinel = releases / "prior-owner.txt"
    sentinel.write_text("preserve prior owner", encoding="utf-8")
    moved_release = tmp_path / f"moved-owned-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)
    marker_descriptor: int | None = None
    original_remove = sync_publication._remove_published_staging_marker
    remove_attempts = 0

    def fail_first_marker_remove(
        marker_name: str,
        marker_identity: sync_publication._DirectoryIdentity,
        directory_descriptor: int | None,
        directory_identity: sync_publication._DirectoryIdentity,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
        transaction: sync_publication._PublicationTransactionState,
    ) -> BaseException | None:
        nonlocal marker_descriptor, remove_attempts
        remove_attempts += 1
        if remove_attempts == 1:
            marker_descriptor = transaction.marker_descriptor
            assert marker_descriptor is not None
            return PermissionError("fictional marker deletion failure")
        return original_remove(
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            destination_name,
            release_root,
            transaction,
        )

    def move_destination_and_fail_rollback(
        _staging_name: str,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
    ) -> bool:
        assert marker_descriptor is not None
        assert os.fstat(marker_descriptor).st_ino > 0
        physical_root = sync_publication._capability_current_path(release_root)
        os.rename(physical_root / destination_name, moved_release)
        return False

    with (
        patch.object(
            sync_publication,
            "_remove_published_staging_marker",
            side_effect=fail_first_marker_remove,
        ),
        patch.object(
            sync_publication,
            "_rollback_published_directory",
            side_effect=move_destination_and_fail_rollback,
        ),
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert remove_attempts == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    assert sorted(path.name for path in releases.iterdir()) == [sentinel.name]
    if os.name == "nt":
        assert not moved_release.exists()
    else:
        assert moved_release.is_dir()
        assert len(list(moved_release.glob(".ancestryllm-staging-*"))) == 1


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("retry_succeeds", (False, True))
def test_failed_rollback_requires_clean_marker_removal_before_commit(
    tmp_path: Path,
    capsys,
    operation: str,
    retry_succeeds: bool,
) -> None:
    releases = tmp_path / f"rollback-false-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_remove = sync_publication._remove_published_staging_marker
    remove_attempts = 0

    def controlled_marker_remove(
        marker_name: str,
        marker_identity: sync_publication._DirectoryIdentity,
        directory_descriptor: int | None,
        directory_identity: sync_publication._DirectoryIdentity,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
        transaction: sync_publication._PublicationTransactionState,
    ) -> BaseException | None:
        nonlocal remove_attempts
        remove_attempts += 1
        if remove_attempts == 1 or not retry_succeeds:
            assert transaction.marker_descriptor is not None
            return PermissionError("fictional persistent marker deletion failure")
        return original_remove(
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            destination_name,
            release_root,
            transaction,
        )

    with (
        patch.object(
            sync_publication,
            "_remove_published_staging_marker",
            side_effect=controlled_marker_remove,
        ),
        patch.object(
            sync_publication,
            "_rollback_published_directory",
            return_value=False,
        ),
    ):
        result = run_sync(arguments)

    assert remove_attempts == 2
    bundles = list(releases.glob("g*-*"))
    markers = list(bundles[0].glob(".ancestryllm-staging-*")) if bundles else []
    if retry_succeeds:
        assert len(bundles) == 1
        assert result == 0
        assert not markers
    else:
        assert result == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]
        if os.name == "nt":
            assert not bundles
        else:
            assert len(bundles) == 1
            assert len(markers) == 1
        rendered = capsys.readouterr().err
        assert "SYNC_PUBLICATION_INCOMPLETE" in rendered
        assert "incomplete generation directory" in rendered
        assert "No release files were changed" not in rendered


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink interruption boundary")
@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_unlink_interruption_after_marker_removal_recovers_committed_success(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"unlink-boundary-{operation}"
    releases.mkdir()
    sentinel = releases / "prior-owner.txt"
    sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_unlink = sync_publication.os.unlink
    original_remove = sync_publication._remove_published_staging_marker
    interrupted = False

    def unlink_then_interrupt(*args, **kwargs) -> None:
        nonlocal interrupted
        selected = Path(args[0])
        directory_descriptor = kwargs.get("dir_fd")
        if (
            not interrupted
            and selected.name.startswith(".ancestryllm-staging-")
            and isinstance(directory_descriptor, int)
            and sync_publication._held_file_path(directory_descriptor).name.startswith("g")
        ):
            original_unlink(*args, **kwargs)
            interrupted = True
            raise interruption("fictional post-unlink interruption")
        original_unlink(*args, **kwargs)

    def remove_with_interrupted_unlink(*args, **kwargs):
        with patch.object(
            sync_publication.os,
            "unlink",
            side_effect=unlink_then_interrupt,
        ):
            return original_remove(*args, **kwargs)

    with patch.object(
        sync_publication,
        "_remove_published_staging_marker",
        side_effect=remove_with_interrupted_unlink,
    ):
        assert run_sync(arguments) == 0

    assert interrupted
    assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    bundles = list(releases.glob("g*-*"))
    assert len(bundles) == 1
    assert not list(bundles[0].glob(".ancestryllm-staging-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_release_root_swap_at_marker_removal_rolls_back_in_held_root(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"marker-root-swap-{operation}"
    releases.mkdir()
    original_sentinel = releases / "original-sentinel.txt"
    original_sentinel.write_text("preserve original owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    moved_root = tmp_path / f"marker-root-moved-{operation}"
    foreign_sentinel = releases / "foreign-sentinel.txt"
    original_remove = sync_publication._remove_published_staging_marker
    swapped = False

    def swap_then_remove(
        marker_name: str,
        marker_identity: sync_publication._DirectoryIdentity,
        directory_descriptor: int | None,
        directory_identity: sync_publication._DirectoryIdentity,
        destination_name: str,
        release_root: sync_publication._DirectoryCapability,
        transaction: sync_publication._PublicationTransactionState,
    ) -> BaseException | None:
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(releases, moved_root)
            releases.mkdir()
            foreign_sentinel.write_text("preserve foreign owner", encoding="utf-8")
        return original_remove(
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            destination_name,
            release_root,
            transaction,
        )

    with patch.object(
        sync_publication,
        "_remove_published_staging_marker",
        side_effect=swap_then_remove,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert swapped
    assert foreign_sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert not list(releases.glob("g*-*"))
    assert (moved_root / original_sentinel.name).read_text(
        encoding="utf-8"
    ) == "preserve original owner"
    assert not list(moved_root.glob("g*-*"))
    residues = list(moved_root.glob(".gedcom-*"))
    if os.name == "nt":
        assert not residues
    else:
        assert len(residues) == 1
        assert not any(residues[0].iterdir())


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_release_root_swap_during_staging_mkdir_uses_held_root_for_cleanup(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"mkdir-root-swap-{operation}"
    releases.mkdir()
    original_sentinel = releases / "original-sentinel.txt"
    original_sentinel.write_text("preserve original owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    moved_root = tmp_path / f"mkdir-root-moved-{operation}"
    foreign_sentinel = releases / "foreign-sentinel.txt"
    original_mkdir = sync_publication.os.mkdir
    swapped = False

    def mkdir_then_swap(*args, **kwargs) -> None:
        nonlocal swapped
        selected = Path(args[0])
        original_mkdir(*args, **kwargs)
        if not swapped and selected.name.startswith((".gedcom-sync-", ".gedcom-rebase-")):
            swapped = True
            os.rename(releases, moved_root)
            releases.mkdir()
            foreign_sentinel.write_text("preserve foreign owner", encoding="utf-8")

    with patch.object(sync_publication.os, "mkdir", side_effect=mkdir_then_swap):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert swapped
    assert foreign_sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert not list(releases.glob(".gedcom-*"))
    assert (moved_root / original_sentinel.name).read_text(
        encoding="utf-8"
    ) == "preserve original owner"
    residues = list(moved_root.glob(".gedcom-*"))
    if os.name == "nt":
        assert not residues
    else:
        assert len(residues) == 1
        assert not any(residues[0].iterdir())


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_release_root_candidate_mkdir_interruption_is_bounded_and_explicit(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"candidate-mkdir-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_mkdir = sync_publication.os.mkdir
    interrupted = False

    def mkdir_then_interrupt(*args, **kwargs) -> None:
        nonlocal interrupted
        selected = Path(args[0])
        original_mkdir(*args, **kwargs)
        if not interrupted and selected.name.startswith(".ancestryllm-release-root-"):
            interrupted = True
            raise interruption("fictional release-root mkdir interruption")

    with (
        patch.object(sync_publication.os, "mkdir", side_effect=mkdir_then_interrupt),
        pytest.raises(interruption),
    ):
        run_sync(arguments)

    assert interrupted
    assert not releases.exists()
    candidates = list(tmp_path.glob(".ancestryllm-release-root-*"))
    assert len(candidates) == 1
    assert not any(candidates[0].iterdir())


@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_windows_candidate_mkdir_interruption_preserves_foreign_replacement(
    tmp_path: Path,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / "windows-ambiguous-candidate"
    original_mkdir = sync_publication.os.mkdir
    moved_owned = tmp_path / "windows-moved-owned-candidate"
    candidate: Path | None = None

    def create_swap_and_interrupt(*args, **kwargs) -> None:
        nonlocal candidate
        selected = Path(args[0])
        original_mkdir(*args, **kwargs)
        if selected.name.startswith(".ancestryllm-release-root-"):
            candidate = selected
            os.rename(selected, moved_owned)
            original_mkdir(selected)
            raise interruption("fictional Windows post-mkdir interruption")

    with (
        patch.object(
            sync_publication,
            "_uses_windows_capability_handles",
            return_value=True,
        ),
        patch.object(
            sync_publication.os,
            "mkdir",
            side_effect=create_swap_and_interrupt,
        ),
        pytest.raises(interruption),
    ):
        sync_publication._ensure_release_root(releases)

    assert candidate is not None
    assert candidate.is_dir()
    assert not any(candidate.iterdir())
    assert moved_owned.is_dir()
    assert not any(moved_owned.iterdir())
    assert not releases.exists()


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_release_root_candidate_collision_preserves_foreign_directory(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"candidate-collision-{operation}"
    collision_hex = "a" * 32
    foreign_candidate = tmp_path / f".ancestryllm-release-root-{collision_hex}"
    foreign_candidate.mkdir()
    sentinel = foreign_candidate / "foreign-sentinel.txt"
    sentinel.write_text("preserve foreign owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_uuid4 = sync_publication.uuid.uuid4
    calls = 0

    def collide_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(hex=collision_hex)
        return original_uuid4()

    with patch.object(sync_publication.uuid, "uuid4", side_effect=collide_once):
        assert run_sync(arguments) == 0

    assert calls > 1
    assert sentinel.read_text(encoding="utf-8") == "preserve foreign owner"
    assert len(list(releases.glob("g*-*"))) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace deletion fails closed")
def test_flat_cleanup_never_removes_late_empty_foreign_replacement(tmp_path: Path) -> None:
    releases = tmp_path / "cleanup-root"
    releases.mkdir()
    release_capability = sync_publication._open_directory_capability(releases, owned=False)
    (
        staging,
        staging_name,
        descriptor,
        expected,
        marker_name,
        marker_descriptor,
        marker_identity,
    ) = sync_publication._create_staging_directory(release_capability, ".gedcom-sync-")
    moved_owned = tmp_path / "moved-owned-staging"
    original_stat = sync_publication.os.stat
    matching_stats = 0

    def swap_after_final_validation(*args, **kwargs):
        nonlocal matching_stats
        value = original_stat(*args, **kwargs)
        if args[0] == staging_name and kwargs.get("dir_fd") == release_capability.descriptor:
            matching_stats += 1
            if matching_stats == 2:
                os.rename(staging, moved_owned)
                staging.mkdir()
        return value

    with patch.object(
        sync_publication.os,
        "stat",
        side_effect=swap_after_final_validation,
    ):
        sync_publication._cleanup_staging_directory(
            release_capability,
            staging_name,
            descriptor,
            expected,
            marker_name,
            marker_descriptor,
            marker_identity,
        )

    sync_publication._close_capability_quietly(release_capability)
    assert matching_stats == 2
    assert staging.is_dir()
    assert not any(staging.iterdir())
    assert moved_owned.is_dir()
    assert not any(moved_owned.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX capability marker retry")
@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_clean_commit_retries_release_capability_marker_removal(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / f"capability-retry-{operation}"
    releases.mkdir()
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_unlink = sync_publication.os.unlink
    original_close = sync_publication._close_capability_quietly
    attempts = 0

    def fail_capability_marker_once(*args, **kwargs) -> None:
        nonlocal attempts
        selected = Path(args[0])
        if selected.name.startswith(".ancestryllm-capability-"):
            attempts += 1
            if attempts == 1:
                raise PermissionError("fictional one-shot capability marker failure")
        original_unlink(*args, **kwargs)

    def close_with_marker_retry(
        capability: sync_publication._DirectoryCapability,
    ) -> None:
        with patch.object(
            sync_publication.os,
            "unlink",
            side_effect=fail_capability_marker_once,
        ):
            original_close(capability)

    with patch.object(
        sync_publication,
        "_close_capability_quietly",
        side_effect=close_with_marker_retry,
    ):
        assert run_sync(arguments) == 0

    assert attempts == 2
    assert len(list(releases.glob("g*-*"))) == 1
    assert not list(releases.glob(".ancestryllm-capability-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
@pytest.mark.parametrize("phase", ("mkdir", "open", "write", "fsync", "verify"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_staging_creation_interruption_leaves_only_empty_residue(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
    phase: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"interrupted-{phase}-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    triggered = False

    def inject() -> None:
        nonlocal triggered
        triggered = True
        raise interruption(f"fictional {phase} interruption")

    def is_staging_marker(descriptor: int) -> bool:
        try:
            return sync_publication._held_file_path(descriptor).name.startswith(
                ".ancestryllm-staging-"
            )
        except (OSError, sync_contracts.SyncError, ValueError):
            return False

    original_open = sync_publication._open_plain_directory_entry_descriptor
    original_mkdir = sync_publication.os.mkdir
    original_write = sync_publication.os.write
    original_fsync = sync_publication.os.fsync
    original_verify = sync_publication._require_selected_capability

    def interrupt_mkdir(
        path: str | os.PathLike[str],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        selected = Path(path)
        if not triggered and selected.name.startswith((".gedcom-sync-", ".gedcom-rebase-")):
            original_mkdir(path, mode, dir_fd=dir_fd)
            inject()
        original_mkdir(path, mode, dir_fd=dir_fd)

    def interrupt_open(name: str, parent_descriptor: int) -> int:
        if not triggered and Path(name).name.startswith((".gedcom-sync-", ".gedcom-rebase-")):
            inject()
        return original_open(name, parent_descriptor)

    def interrupt_write(descriptor: int, payload: bytes) -> int:
        if not triggered and is_staging_marker(descriptor):
            inject()
        return original_write(descriptor, payload)

    def interrupt_fsync(descriptor: int) -> None:
        if not triggered and is_staging_marker(descriptor):
            inject()
        original_fsync(descriptor)

    def interrupt_verify(capability: sync_publication._DirectoryCapability) -> None:
        original_verify(capability)
        if triggered:
            return
        physical_root = sync_publication._capability_current_path(capability)
        if any(
            path.name.startswith((".gedcom-sync-", ".gedcom-rebase-"))
            for path in physical_root.iterdir()
        ):
            inject()

    target = {
        "mkdir": patch.object(sync_publication.os, "mkdir", side_effect=interrupt_mkdir),
        "open": patch.object(
            sync_publication,
            "_open_plain_directory_entry_descriptor",
            side_effect=interrupt_open,
        ),
        "write": patch.object(sync_publication.os, "write", side_effect=interrupt_write),
        "fsync": patch.object(sync_publication.os, "fsync", side_effect=interrupt_fsync),
        "verify": patch.object(
            sync_publication,
            "_require_selected_capability",
            side_effect=interrupt_verify,
        ),
    }[phase]
    with target, pytest.raises(interruption):
        run_sync(arguments)

    assert triggered
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_interruption_between_rename_and_marker_finalization_rolls_back(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"publish-interrupted-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    finalized = False

    def interrupt_finalization(*_args, **_kwargs):
        nonlocal finalized
        finalized = True
        raise interruption("fictional post-rename interruption")

    with (
        patch.object(
            sync_publication,
            "_finalize_published_directory",
            side_effect=interrupt_finalization,
        ),
        pytest.raises(interruption),
    ):
        run_sync(arguments)

    assert finalized
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_interruption_after_transaction_return_preserves_committed_success(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"return-interrupted-{operation}"
    releases.mkdir()
    sentinel = releases / "prior-owner.txt"
    sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_publish = sync_publication._publish_and_finalize_directory
    interrupted = False

    def commit_then_interrupt(*args, **kwargs):
        nonlocal interrupted
        error = original_publish(*args, **kwargs)
        transaction = args[-1]
        assert error is None
        assert transaction.committed
        interrupted = True
        raise interruption("fictional return-to-caller interruption")

    with patch.object(
        sync_publication,
        "_publish_and_finalize_directory",
        side_effect=commit_then_interrupt,
    ):
        assert run_sync(arguments) == 0

    assert interrupted
    assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    bundles = list(releases.glob("g*-*"))
    assert len(bundles) == 1
    assert not list(bundles[0].glob(".ancestryllm-staging-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize(
    "close_target",
    ("marker", "staging-directory", "release-root"),
)
def test_raw_close_interruption_after_commit_preserves_success(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
    close_target: str,
) -> None:
    releases = tmp_path / f"close-interrupted-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_close = sync_publication.os.close
    interrupted = False

    def interrupt_marker_close_once(descriptor: int) -> None:
        nonlocal interrupted
        try:
            selected = sync_publication._held_file_path(descriptor)
            value = os.fstat(descriptor)
        except (OSError, sync_contracts.SyncError, ValueError):
            selected = Path()
            value = None
        selected_target = (
            (
                close_target == "marker"
                and selected.name.startswith(".ancestryllm-staging-")
                and selected.parent.name.startswith("g")
            )
            or (
                close_target == "staging-directory"
                and value is not None
                and stat.S_ISDIR(value.st_mode)
                and selected.name.startswith("g")
            )
            or (
                close_target == "release-root"
                and value is not None
                and stat.S_ISDIR(value.st_mode)
                and selected == releases
            )
        )
        if not interrupted and selected_target:
            interrupted = True
            raise interruption("fictional raw close interruption")
        original_close(descriptor)

    with patch.object(
        sync_publication.os,
        "close",
        side_effect=interrupt_marker_close_once,
    ):
        assert run_sync(arguments) == 0

    assert interrupted
    bundles = list(releases.glob("g*-*"))
    assert len(bundles) == 1
    assert not list(bundles[0].glob(".ancestryllm-staging-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_candidate_identity_interruption_leaves_only_empty_candidate(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"candidate-identity-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_identity = sync_publication._directory_identity
    interrupted = False

    def interrupt_candidate_identity(path: Path):
        nonlocal interrupted
        if not interrupted and path.name.startswith(".ancestryllm-release-root-"):
            interrupted = True
            raise interruption("fictional candidate identity interruption")
        return original_identity(path)

    with (
        patch.object(
            sync_publication,
            "_directory_identity",
            side_effect=interrupt_candidate_identity,
        ),
        pytest.raises(interruption),
    ):
        run_sync(arguments)

    assert interrupted
    assert not releases.exists()
    _assert_candidate_cleanup_residue(tmp_path)


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("preexisting", (False, True))
def test_persistent_staging_identity_failure_leaves_only_empty_residue(
    tmp_path: Path,
    operation: str,
    preexisting: bool,
) -> None:
    releases = tmp_path / f"persistent-identity-{operation}"
    sentinel = releases / "prior-owner.txt"
    if preexisting:
        releases.mkdir()
        sentinel.write_text("preserve prior owner", encoding="utf-8")
    arguments = _new_publication_args(tmp_path, operation, releases)
    original_stat = sync_publication.os.stat
    failures = 0

    def fail_staging_identity(*args, **kwargs):
        nonlocal failures
        if Path(args[0]).name.startswith((".gedcom-sync-", ".gedcom-rebase-")):
            failures += 1
            raise OSError("fictional persistent staging identity failure")
        return original_stat(*args, **kwargs)

    with patch.object(
        sync_publication.os,
        "stat",
        side_effect=fail_staging_identity,
    ):
        assert run_sync(arguments) == sync_contracts.EXIT_CODES["SYNC_OUTPUT"]

    assert failures >= 1
    if preexisting:
        assert sentinel.read_text(encoding="utf-8") == "preserve prior owner"
    _assert_cleanup_residue(releases, operation)


@pytest.mark.parametrize("phase", ("open", "write", "fsync", "verify"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_directory_capability_interruption_leaves_only_empty_candidate(
    tmp_path: Path,
    phase: str,
    interruption: type[BaseException],
) -> None:
    candidate = tmp_path / f"interrupted-capability-{phase}"
    candidate.mkdir()
    triggered = False

    def inject() -> None:
        nonlocal triggered
        triggered = True
        raise interruption(f"fictional capability {phase} interruption")

    original_open_marker = sync_publication._open_held_marker
    original_write = sync_publication.os.write
    original_fsync = sync_publication.os.fsync
    original_fstat = sync_publication.os.fstat

    def interrupt_open_marker(
        path: Path,
        *,
        create: bool,
        directory_descriptor: int | None = None,
    ) -> int:
        if not triggered and create:
            inject()
        return original_open_marker(
            path,
            create=create,
            directory_descriptor=directory_descriptor,
        )

    def interrupt_write(descriptor: int, payload: bytes) -> int:
        if not triggered:
            inject()
        return original_write(descriptor, payload)

    def interrupt_fsync(descriptor: int) -> None:
        if not triggered:
            inject()
        original_fsync(descriptor)

    def interrupt_verify(descriptor: int):
        if not triggered:
            try:
                selected = sync_publication._held_file_path(descriptor)
            except (OSError, sync_contracts.SyncError, ValueError):
                selected = Path()
            if selected.name.startswith(".ancestryllm-capability-"):
                inject()
        return original_fstat(descriptor)

    target = {
        "open": patch.object(
            sync_publication,
            "_open_held_marker",
            side_effect=interrupt_open_marker,
        ),
        "write": patch.object(sync_publication.os, "write", side_effect=interrupt_write),
        "fsync": patch.object(sync_publication.os, "fsync", side_effect=interrupt_fsync),
        "verify": patch.object(sync_publication.os, "fstat", side_effect=interrupt_verify),
    }[phase]
    with target, pytest.raises(interruption):
        sync_publication._open_directory_capability(candidate, owned=True)

    assert triggered
    if os.name == "nt":
        assert not candidate.exists()
    else:
        assert candidate.is_dir()
        assert not any(candidate.iterdir())


@pytest.mark.parametrize("operation", ("update", "rebase"))
def test_committed_release_survives_status_output_failure(
    tmp_path: Path,
    operation: str,
) -> None:
    releases = tmp_path / "releases"
    first = _initialize_release(releases)
    if operation == "update":
        arguments = _update_args(
            releases,
            first,
            _snapshot("ancestry-main", "ancestry", 2),
        )
    else:
        edited = tmp_path / "fictional-edited-master.ged"
        edited.write_bytes((first / "master.ged").read_bytes())
        arguments = [
            "rebase",
            "--master",
            str(edited),
            "--manifest",
            str(first / "manifest.json"),
            "--release-root",
            str(releases),
            "--reason",
            "Fictional post-commit output regression",
        ]

    with patch("builtins.print", side_effect=BrokenPipeError("status stream closed")):
        assert run_sync(arguments) == 0

    bundles = sorted(releases.glob("g*-*"))
    assert len(bundles) == 2
    manifest = json.loads((bundles[-1] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 2
    assert not list(releases.glob("failed-update-*"))


@pytest.mark.parametrize("operation", ("update", "rebase"))
@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_committed_release_survives_status_interruption(
    tmp_path: Path,
    operation: str,
    interruption: type[BaseException],
) -> None:
    releases = tmp_path / f"status-interrupted-{operation}"
    arguments = _new_publication_args(tmp_path, operation, releases)

    with patch(
        "builtins.print",
        side_effect=interruption("fictional status interruption"),
    ):
        assert run_sync(arguments) == 0

    bundles = list(releases.glob("g*-*"))
    assert len(bundles) == 1
    assert not list(bundles[0].glob(".ancestryllm-*"))
