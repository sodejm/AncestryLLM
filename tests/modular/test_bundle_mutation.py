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
