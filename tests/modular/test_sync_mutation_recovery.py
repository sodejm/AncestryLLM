"""Exercise journal recovery through the real fictional sync command."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ancestryllm.core.directory_mutation import DirectoryMutation
from ancestryllm.core.mutation import LocalMutationCoordinator
from ancestryllm.gedcom.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gedcom_incremental"


def _update(root: Path) -> list[str]:
    return [
        "update",
        "--master",
        str(FIXTURES / "baseline-master.ged"),
        "--initialize-manifest",
        "--snapshot",
        f"fictional:ancestry={FIXTURES / 'ancestry-snapshot-v1.ged'}",
        "--release-root",
        str(root),
        "--no-quality-report",
    ]


def _terminate_sync(namespace: str, arguments: list[str], boundary: str, occurrence: int) -> None:
    from ancestryllm.core import mutation
    from ancestryllm.gedcom import sync_publication

    seen = 0

    def checkpoint(self: DirectoryMutation, reached: str) -> None:
        nonlocal seen
        if reached == boundary:
            seen += 1
            if seen == occurrence:
                os._exit(37)

    original = sync_publication._remove_published_staging_marker

    def finalize(*args: object, **kwargs: object) -> None:
        if boundary == "published":
            os._exit(37)
        original(*args, **kwargs)
        if boundary == "finalized":
            os._exit(37)

    with (
        patch.object(mutation, "coordinator_namespace", return_value=Path(namespace)),
        patch.object(DirectoryMutation, "_checkpoint", checkpoint),
        patch.object(sync_publication, "_remove_published_staging_marker", finalize),
    ):
        run_sync(arguments, raise_errors=True)


@pytest.mark.parametrize("operation", ["update", "rebase"])
@pytest.mark.parametrize(
    ("boundary", "occurrence"),
    [
        (boundary, 1)
        for boundary in [
            "staging_planned",
            "staging_created",
            "member_created",
            "member_written",
            "prepared",
            "committing",
            "published",
            "finalized",
            "committed",
        ]
    ]
    + [
        (boundary, occurrence)
        for boundary in ("member_created", "member_written")
        for occurrence in range(2, 6)
    ],
)
def test_sync_restart_preserves_complete_release(
    tmp_path: Path, operation: str, boundary: str, occurrence: int
) -> None:
    root = tmp_path / "releases"
    root.mkdir()
    unrelated = root / "unrelated.txt"
    unrelated.write_bytes(b"preserved")
    arguments = _update(root)
    if operation == "rebase":
        source = tmp_path / "source"
        assert run_sync(_update(source), raise_errors=True) == 0
        previous = next(source.glob("g0001-*"))
        arguments = [
            "rebase",
            "--master",
            str(previous / "master.ged"),
            "--manifest",
            str(previous / "manifest.json"),
            "--release-root",
            str(root),
            "--reason",
            "Fictional recovery test",
        ]
    namespace = tmp_path / "journal"
    process = multiprocessing.get_context("spawn").Process(
        target=_terminate_sync,
        args=(str(namespace), arguments, boundary, occurrence),
    )
    process.start()
    process.join(20)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Sync did not reach its requested crash boundary")
    assert process.exitcode == 37
    with LocalMutationCoordinator(namespace) as coordinator:
        DirectoryMutation.reconcile_root(root, coordinator)
        DirectoryMutation.reconcile_root(root, coordinator)
        assert coordinator.interrupted((DirectoryMutation._root_scope(root, coordinator),)) == ()
        with coordinator._transaction() as database:
            outcomes = database.execute(
                "SELECT state,outcome FROM operations JOIN directory_publications USING(operation_id)"
            ).fetchall()
        assert len(outcomes) == 1
        assert outcomes[0]["outcome"] is not None
        committed = boundary in {"published", "finalized", "committed"}
        assert outcomes[0]["state"] == ("committed" if committed else "aborted")
    releases = list(root.glob("g*"))
    assert len(releases) == int(committed)
    if committed:
        release = releases[0]
        assert sorted(child.name for child in release.iterdir()) == [
            "manifest.json",
            "master.ged",
            "quality.md",
            "rollback.json",
            "update.md",
        ]
        manifest = json.loads((release / "manifest.json").read_bytes())
        for name, digest in manifest["artifact_checksums"].items():
            assert hashlib.sha256((release / name).read_bytes()).hexdigest() == digest
    assert unrelated.read_bytes() == b"preserved"
    assert not list(root.glob(".gedcom-*"))


@pytest.mark.parametrize("operation", ["update", "rebase"])
def test_sync_committed_outcome_wins_over_late_cancellation(
    tmp_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.core.cancellation import CancellationToken, bind_cancellation_token
    from ancestryllm.gedcom import sync_publication
    from ancestryllm.gedcom.sync import execute_sync

    root = tmp_path / "releases"
    arguments = _update(root)
    if operation == "rebase":
        source = tmp_path / "source"
        assert run_sync(_update(source), raise_errors=True) == 0
        previous = next(source.glob("g0001-*"))
        arguments = [
            "rebase",
            "--master",
            str(previous / "master.ged"),
            "--manifest",
            str(previous / "manifest.json"),
            "--release-root",
            str(root),
            "--reason",
            "Fictional cancellation test",
        ]
    token = CancellationToken()
    original = sync_publication._remove_published_staging_marker

    def cancel_after_commit(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        token.request()

    monkeypatch.setattr(sync_publication, "_remove_published_staging_marker", cancel_after_commit)
    with bind_cancellation_token(token):
        result = execute_sync(arguments, raise_errors=True)
    assert token.requested
    assert result.committed
    assert result.exit_code == 0
    assert len(result.artifacts) == 5
    assert all(path.is_file() for path in result.artifacts)
