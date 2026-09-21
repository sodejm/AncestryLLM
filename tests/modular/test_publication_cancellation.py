"""Cancellation boundaries for the central staged-publication transaction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import ancestryllm.core.publication as publication_module
from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationState,
    CancellationToken,
    bind_cancellation_token,
)


def _staged(target: Path, payload: bytes) -> Path:
    staged = publication_module.staging_path(target)
    token = publication_module.write_staged_bytes(staged, payload)
    publication_module.claim_staged_path(staged, token)
    return staged


def test_cancellation_before_publication_preserves_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "tree.ged"
    target.write_bytes(b"existing sentinel\n")
    staged = _staged(target, b"replacement\n")
    token = CancellationToken()
    token.request()

    with bind_cancellation_token(token), pytest.raises(CancellationError):
        publication_module.publish_staged_bundle(((staged, target),), replace=os.replace)

    assert target.read_bytes() == b"existing sentinel\n"
    assert staged.read_bytes() == b"replacement\n"


def test_cancellation_during_publication_reports_pending_and_commits_complete_bundle(
    tmp_path: Path,
) -> None:
    first = tmp_path / "tree.ged"
    second = tmp_path / "manifest.json"
    first.write_bytes(b"old tree\n")
    second.write_bytes(b"old manifest\n")
    first_staged = _staged(first, b"new tree\n")
    second_staged = _staged(second, b"new manifest\n")
    token = CancellationToken()
    states: list[CancellationState] = []
    token.subscribe(states.append)
    requested = False

    def request_after_first_install(source: str | Path, destination: str | Path) -> None:
        nonlocal requested
        if Path(source) == Path(destination) and Path(destination) == first and not requested:
            requested = True
            token.request()
        elif Path(source) != Path(destination):
            Path(source).replace(destination)

    with bind_cancellation_token(token), pytest.raises(CancellationError):
        publication_module.publish_staged_bundle(
            ((first_staged, first), (second_staged, second)),
            replace=request_after_first_install,
        )

    assert requested
    assert any(
        state.pending and state.deferred_by == "publishing output bundle" for state in states
    )
    assert first.read_bytes() == b"new tree\n"
    assert second.read_bytes() == b"new manifest\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_publication_failure_wins_when_it_races_cancellation(tmp_path: Path) -> None:
    target = tmp_path / "tree.ged"
    target.write_bytes(b"existing sentinel\n")
    staged = _staged(target, b"replacement\n")
    token = CancellationToken()

    def fail_after_request(source: str | Path, destination: str | Path) -> None:
        if Path(source) == Path(destination):
            token.request()
            raise OSError("fictional publication failure")
        Path(source).replace(destination)

    with (
        bind_cancellation_token(token),
        pytest.raises(OSError, match="fictional publication failure"),
    ):
        publication_module.publish_staged_bundle(
            ((staged, target),),
            replace=fail_after_request,
        )

    assert target.read_bytes() == b"existing sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("restore", [False, True])
def test_copy_preserves_times_through_held_descriptor_without_fd_utime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore: bool
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_bytes(b"fictional recovery payload\n")
    os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_001_000_000_000))
    source_stat = source.stat()
    calls: list[int] = []

    def set_windows_times(descriptor: int, accessed_ns: int, modified_ns: int) -> None:
        calls.append(descriptor)
        assert os.fstat(descriptor).st_ino == target.stat().st_ino
        assert (accessed_ns, modified_ns) == (source_stat.st_atime_ns, source_stat.st_mtime_ns)
        os.utime(target, ns=(accessed_ns, modified_ns))

    monkeypatch.setattr(publication_module.os, "supports_fd", set())
    monkeypatch.setattr(publication_module.sys, "platform", "win32")
    monkeypatch.setattr(
        publication_module, "_windows_set_descriptor_times", set_windows_times, raising=False
    )
    identity = publication_module._identity(source)
    if restore:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            assert publication_module._restore_open_descriptor(descriptor, identity, target)
        finally:
            os.close(descriptor)
    else:
        publication_module._copy_regular_no_clobber(
            publication_module._OwnedPath(source, identity), target
        )
    assert len(calls) == 1
    assert target.stat().st_mtime_ns == source_stat.st_mtime_ns
    assert target.read_bytes() == source.read_bytes()
