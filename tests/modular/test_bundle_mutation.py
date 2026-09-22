"""Recovery of related outputs after an actual publisher process terminates."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from ancestryllm.core.mutation import LocalMutationCoordinator


def _terminate_bundle(namespace: str, directory: str, boundary: str, occurrence: int = 1) -> None:
    from ancestryllm.core import mutation, publication
    from ancestryllm.core.bundle_mutation import BundleMutation

    mutation.coordinator_namespace = lambda: Path(namespace)

    reached_count = 0

    def terminate(self: BundleMutation, reached: str) -> None:
        nonlocal reached_count
        if reached == boundary:
            reached_count += 1
            if reached_count == occurrence:
                os._exit(31)

    BundleMutation._checkpoint = terminate
    pairs = []
    for name in ("tree.ged", "report.json"):
        target = Path(directory) / name
        stage = publication.staging_path(target)
        publication.write_staged_bytes(stage, b"new fictional " + name.encode())
        pairs.append((stage, target))
    publication.publish_staged_bundle(pairs, replace=os.replace)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("occurrence", [1, 2])
@pytest.mark.parametrize(
    "boundary",
    ["prepared", "backed_up", "displacing", "displaced", "installing", "installed", "validated"],
)
def test_bundle_restart_restores_complete_set(
    tmp_path: Path, boundary: str, existing: bool, occurrence: int
) -> None:
    from ancestryllm.core.bundle_mutation import BundleMutation

    if not existing and boundary in {"displacing", "displaced"}:
        pytest.skip("Absent outputs need no displacement")
    if occurrence == 2 and boundary in {"prepared", "backed_up", "validated"}:
        pytest.skip("This boundary applies once to the complete bundle")
    targets = tuple(tmp_path / name for name in ("tree.ged", "report.json"))
    if existing:
        for target in targets:
            target.write_bytes(b"old fictional " + target.name.encode())
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserved")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_bundle, args=(str(namespace), str(tmp_path), boundary, occurrence)
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher never reached the requested boundary")
    assert process.exitcode == 31
    with LocalMutationCoordinator(namespace) as coordinator:
        BundleMutation.reconcile(targets, coordinator)
        assert coordinator.interrupted(targets) == ()
        # A second recovery must be inert.
        BundleMutation.reconcile(targets, coordinator)
    for target in targets:
        if boundary == "validated":
            assert target.read_bytes() == b"new fictional " + target.name.encode()
        elif existing:
            assert target.read_bytes() == b"old fictional " + target.name.encode()
        else:
            assert not target.exists()
    assert unrelated.read_bytes() == b"preserved"
    assert not list(tmp_path.glob(".ancestry-publish-*"))
    for file in namespace.rglob("*"):
        if file.is_file():
            contents = file.read_bytes()
            assert b"tree.ged" not in contents
            assert b"fictional" not in contents
            assert str(tmp_path).encode() not in contents


@pytest.mark.parametrize("boundary", ["installed", "validated"])
@pytest.mark.parametrize("change", ["modified", "replaced"])
def test_bundle_recovery_preserves_changed_destinations(
    tmp_path: Path, boundary: str, change: str
) -> None:
    from ancestryllm.core.bundle_mutation import BundleMutation
    from ancestryllm.core.errors import AncestryError

    targets = tuple(tmp_path / name for name in ("tree.ged", "report.json"))
    for target in targets:
        target.write_bytes(b"old fictional " + target.name.encode())
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_bundle,
        args=(str(namespace), str(tmp_path), boundary, 2 if boundary == "installed" else 1),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher never reached the requested boundary")
    assert process.exitcode == 31
    changed = targets[1]
    if change == "replaced":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"unrelated replacement")
        replacement.replace(changed)
    else:
        changed.write_bytes(b"independently modified")
    before = {path: path.read_bytes() for path in targets}
    with LocalMutationCoordinator(namespace) as coordinator:
        with pytest.raises(AncestryError, match="requires recovery"):
            BundleMutation.reconcile(targets, coordinator)
        assert len(coordinator.interrupted(targets)) == 1
    assert {path: path.read_bytes() for path in targets} == before


@pytest.mark.parametrize("boundary", ["candidate", "displaced", "installed"])
def test_expired_bundle_lease_rolls_back_complete_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from ancestryllm.core import mutation, publication
    from ancestryllm.core.bundle_mutation import BundleMutation
    from ancestryllm.core.errors import AncestryError

    namespace = tmp_path / "journal"
    monkeypatch.setattr(mutation, "coordinator_namespace", lambda: namespace)
    original_clock = mutation._now_ms
    expired = False

    def checkpoint(self: BundleMutation, reached: str) -> None:
        nonlocal expired
        if reached == boundary:
            expired = True

    monkeypatch.setattr(BundleMutation, "_checkpoint", checkpoint)
    monkeypatch.setattr(mutation, "_now_ms", lambda: original_clock() + (600_000 if expired else 0))
    pairs = []
    for name in ("tree.ged", "report.json"):
        target = tmp_path / name
        target.write_bytes(b"old fictional")
        stage = publication.staging_path(target)
        publication.write_staged_bytes(stage, b"new fictional")
        pairs.append((stage, target))
    with pytest.raises(AncestryError) as error:
        publication.publish_staged_bundle(pairs, replace=os.replace)
    assert error.value.code == "MUTATION_LEASE_EXPIRED"
    assert all(target.read_bytes() == b"old fictional" for _, target in pairs)
    with LocalMutationCoordinator(namespace) as coordinator:
        assert coordinator.interrupted(target for _, target in pairs) == ()
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def _terminate_copy(namespace: str, directory: str, phase: str, boundary: str) -> None:
    from ancestryllm.core import mutation, publication

    mutation.coordinator_namespace = lambda: Path(namespace)
    copying = False
    restoring = False
    original_copy = publication._copy_regular_no_clobber
    original_write = os.write
    original_quarantine = publication._create_private_quarantine

    def copy(source, target, **kwargs):
        nonlocal copying
        selected = (
            "backup" if "-backup-" in target.name else ("restoration" if restoring else "candidate")
        )
        copying = selected == phase
        owner = kwargs.get("owner")

        def completed(owned):
            if copying and boundary == "copied":
                os._exit(32)
            if owner is not None:
                owner(owned)

        kwargs["owner"] = completed
        try:
            return original_copy(source, target, **kwargs)
        finally:
            copying = False

    def write(fd, data):
        result = original_write(fd, data)
        if copying and boundary == "mid_copy":
            os._exit(32)
        return result

    def quarantine(target, prepared):
        original_quarantine(target, prepared)
        if (
            prepared.artifact is not None
            and boundary == "quarantine"
            and ("restoration" if restoring else "candidate") == phase
        ):
            os._exit(32)

    def reject():
        nonlocal restoring
        restoring = True
        raise OSError("Injected post-publication validation failure")

    publication._copy_regular_no_clobber = copy
    publication._create_private_quarantine = quarantine
    os.write = write
    pairs = []
    for name in ("tree.ged", "report.json"):
        target = Path(directory) / name
        stage = publication.staging_path(target)
        publication.write_staged_bytes(stage, b"n" * (2 * 1024 * 1024))
        pairs.append((stage, target))
    publication.publish_staged_bundle(
        pairs, replace=os.replace, validate_after=reject if phase == "restoration" else None
    )


@pytest.mark.parametrize("phase", ["backup", "candidate", "restoration"])
@pytest.mark.parametrize("boundary", ["quarantine", "mid_copy", "copied"])
def test_interrupted_copy_cleans_only_owned_private_files(tmp_path, phase, boundary):
    from ancestryllm.core.bundle_mutation import BundleMutation

    if phase == "backup" and boundary == "quarantine":
        pytest.skip("Backups do not create a quarantine directory")
    targets = tuple(tmp_path / name for name in ("tree.ged", "report.json"))
    for target in targets:
        target.write_bytes(b"o" * (2 * 1024 * 1024))
    unrelated = tmp_path / ".ancestry-publish-unrelated"
    unrelated.write_bytes(b"preserve this independent file")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_copy, args=(str(namespace), str(tmp_path), phase, boundary)
    )
    process.start()
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher never reached the copy boundary")
    assert process.exitcode == 32
    with LocalMutationCoordinator(namespace) as coordinator:
        BundleMutation.reconcile(targets, coordinator)
        BundleMutation.reconcile(targets, coordinator)
        assert coordinator.interrupted(targets) == ()
    assert all(target.read_bytes() == b"o" * (2 * 1024 * 1024) for target in targets)
    assert unrelated.read_bytes() == b"preserve this independent file"
    assert list(tmp_path.glob(".ancestry-publish-*")) == [unrelated]


@pytest.mark.parametrize("replacement", ["file", "hardlink", "parent"])
def test_interrupted_copy_preserves_changed_ownership(tmp_path: Path, replacement: str) -> None:
    from ancestryllm.core.bundle_mutation import BundleMutation, _Record
    from ancestryllm.core.errors import AncestryError

    targets = tuple(tmp_path / name for name in ("tree.ged", "report.json"))
    for target in targets:
        target.write_bytes(b"old fictional")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_copy,
        args=(str(namespace), str(tmp_path), "candidate", "mid_copy"),
    )
    process.start()
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher never reached the copy boundary")
    assert process.exitcode == 32
    with LocalMutationCoordinator(namespace) as coordinator:
        with coordinator._transaction() as database:
            record = _Record.from_json(
                database.execute("SELECT record FROM bundle_publications").fetchone()[0]
            )
        partial = record.entries[0].partial_files[0].restore(targets[0]).path
        if replacement == "file":
            partial.rename(tmp_path / "preserved-partial")
            partial.write_bytes(b"unrelated replacement")
        elif replacement == "hardlink":
            os.link(partial, tmp_path / "unrelated-link")
        else:
            partial.parent.rename(tmp_path / "preserved-parent")
            partial.parent.mkdir()
            partial.write_bytes(b"unrelated replacement")
        expected = partial.read_bytes()
        with pytest.raises(AncestryError) as error:
            BundleMutation.reconcile(targets, coordinator)
        assert error.value.code == "MUTATION_RECOVERY_REQUIRED"
        assert coordinator.interrupted(targets)
        assert partial.read_bytes() == expected


def _terminate_symlink_restoration(namespace, directory):
    from ancestryllm.core import mutation, publication

    mutation.coordinator_namespace = lambda: Path(namespace)
    original = publication._install_no_clobber

    def restore(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("restoration") is not None:
            os._exit(38)
        return result

    def reject():
        raise OSError("Injected validation failure")

    publication._install_no_clobber = restore
    target = Path(directory) / "tree.ged"
    stage = publication.staging_path(target)
    publication.write_staged_bytes(stage, b"fictional replacement")
    publication.publish_staged_bundle([(stage, target)], replace=os.replace, validate_after=reject)


def test_restart_after_symlink_restoration(tmp_path):
    from ancestryllm.core.bundle_mutation import BundleMutation

    original = tmp_path / "original.ged"
    original.write_bytes(b"fictional original")
    target = tmp_path / "tree.ged"
    try:
        target.symlink_to(original.name)
    except OSError:
        pytest.skip("Creating symbolic links requires platform privileges")
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_symlink_restoration, args=(str(namespace), str(tmp_path))
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Publisher did not reach symlink restoration")
    assert process.exitcode == 38
    with LocalMutationCoordinator(namespace) as coordinator:
        BundleMutation.reconcile((target,), coordinator)
        BundleMutation.reconcile((target,), coordinator)
        assert coordinator.interrupted((target,)) == ()
    assert target.is_symlink()
    assert target.readlink() == Path(original.name)
    assert original.read_bytes() == b"fictional original"
    assert not list(tmp_path.glob(".ancestry-publish-*"))
