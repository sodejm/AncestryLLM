"""Verify the shared privacy-safe desktop diagnostic contract and retention."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest

from ancestryllm.observability.structured_diagnostics import (
    DESKTOP_DIAGNOSTIC_CODES,
    DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
    DesktopDiagnosticComponent,
    DesktopDiagnosticSeverity,
    DesktopDiagnosticWriter,
    create_desktop_diagnostic_event,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def _schema() -> Mapping[str, Any]:
    return json.loads(
        (Path(__file__).parents[1] / "schemas" / "desktop-diagnostic-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


def test_event_matches_shared_versioned_schema() -> None:
    event = create_desktop_diagnostic_event(
        run_id=RUN_ID,
        app_version="0.7.0-dev.1",
        code="PYTHON_CORE_BOOTSTRAP_STARTED",
        severity=DesktopDiagnosticSeverity.INFO,
        component=DesktopDiagnosticComponent.PYTHON_CORE,
        metadata={"retry_count": 2, "degraded": False},
        now=datetime(2026, 8, 19, 12, 34, 56, 789000, tzinfo=UTC),
    )

    assert event.schema_version == DESKTOP_DIAGNOSTIC_SCHEMA_VERSION
    assert event.as_document() == {
        "schema_version": DESKTOP_DIAGNOSTIC_SCHEMA_VERSION,
        "timestamp": "2026-08-19T12:34:56.789Z",
        "run_id": RUN_ID,
        "code": "PYTHON_CORE_BOOTSTRAP_STARTED",
        "severity": "info",
        "component": "python-core",
        "app_version": "0.7.0-dev.1",
        "metadata": {"retry_count": 2, "degraded": False},
    }
    jsonschema.Draft202012Validator(_schema()).validate(event.as_document())


def test_event_code_catalog_matches_schema_and_rejects_unknown_codes() -> None:
    assert set(_schema()["properties"]["code"]["enum"]) == DESKTOP_DIAGNOSTIC_CODES
    with pytest.raises(ValueError, match="invalid diagnostic code"):
        create_desktop_diagnostic_event(
            run_id=RUN_ID,
            app_version="0.7.0",
            code="UNREVIEWED_EVENT_CODE",
            severity=DesktopDiagnosticSeverity.INFO,
            component=DesktopDiagnosticComponent.PYTHON_CORE,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"family_name": "Fictional Family"},
        {"prompt": "Summarize this family tree"},
        {"path": "/Users/canary/private-tree.rmtree"},
        {"token": "canary-secret-token"},
        {"detail": "arbitrary detail"},
    ],
    ids=["genealogy-content", "prompt", "raw-path", "secret", "free-form-text"],
)
def test_privacy_canaries_are_rejected(metadata: Mapping[str, object]) -> None:
    with pytest.raises(ValueError):
        create_desktop_diagnostic_event(
            run_id=RUN_ID,
            app_version="0.7.0",
            code="SIDECAR_BOOTSTRAP_STARTED",
            severity=DesktopDiagnosticSeverity.ERROR,
            component=DesktopDiagnosticComponent.DESKTOP_SIDECAR,
            metadata=metadata,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "run_id",
    [
        "not-a-uuid",
        "123e4567-e89b-12d3-a456-426614174000",
        "123E4567-E89B-42D3-A456-426614174000",
    ],
)
def test_malformed_launch_correlation_identifiers_are_rejected(run_id: str) -> None:
    with pytest.raises(ValueError, match="invalid diagnostic run identifier"):
        create_desktop_diagnostic_event(
            run_id=run_id,
            app_version="0.7.0",
            code="PYTHON_CORE_BOOTSTRAP_STARTED",
            severity=DesktopDiagnosticSeverity.ERROR,
            component=DesktopDiagnosticComponent.PYTHON_CORE,
        )


def test_event_larger_than_component_file_bound_is_refused(tmp_path: Path) -> None:
    writer = DesktopDiagnosticWriter(
        directory=tmp_path,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.PYTHON_CORE,
        max_bytes=32,
    )

    assert not writer.write("PYTHON_CORE_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.WARNING)
    assert list(tmp_path.iterdir()) == []


def test_rejected_privacy_canaries_are_never_persisted(tmp_path: Path) -> None:
    writer = DesktopDiagnosticWriter(
        directory=tmp_path,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.DESKTOP_SIDECAR,
    )
    canaries = [
        "Fictional Family",
        "Summarize this family tree",
        "/Users/canary/private-tree.rmtree",
        "canary-secret-token",
    ]

    for key, canary in zip(("family_name", "prompt", "path", "token"), canaries, strict=True):
        assert not writer.write(
            "SIDECAR_BOOTSTRAP_STARTED",
            DesktopDiagnosticSeverity.ERROR,
            {key: canary},  # type: ignore[dict-item]
        )
    assert writer.write("SIDECAR_SERVER_READY", DesktopDiagnosticSeverity.INFO)

    persisted = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert all(canary not in persisted for canary in canaries)


def test_rotation_is_bounded_by_bytes_and_file_count(tmp_path: Path) -> None:
    writer = DesktopDiagnosticWriter(
        directory=tmp_path,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.PYTHON_CORE,
        max_bytes=330,
        max_files=3,
    )

    for sequence in range(12):
        assert writer.write(
            "PYTHON_CORE_READY",
            DesktopDiagnosticSeverity.INFO,
            {"sequence": sequence},
        )

    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["python-core.jsonl", "python-core.jsonl.1", "python-core.jsonl.2"]
    assert all((tmp_path / filename).stat().st_size <= 330 for filename in files)
    assert "Fictional Family" not in "".join(
        (tmp_path / filename).read_text(encoding="utf-8") for filename in files
    )


def test_validation_and_filesystem_failures_are_non_blocking(tmp_path: Path) -> None:
    invalid_directory = tmp_path / "not-a-directory"
    invalid_directory.write_text("occupied", encoding="utf-8")
    writer = DesktopDiagnosticWriter(
        directory=invalid_directory,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.DESKTOP_SIDECAR,
    )

    assert not writer.write("SIDECAR_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.INFO)
    assert not writer.write("bad code", DesktopDiagnosticSeverity.INFO)
    assert not writer.clear()


def test_writer_refuses_symlink_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "diagnostics-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    writer = DesktopDiagnosticWriter(
        directory=link,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.PYTHON_CORE,
    )

    assert not writer.write("PYTHON_CORE_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.INFO)
    assert not writer.clear()
    assert list(target.iterdir()) == []


def test_writer_refuses_symlink_active_file(tmp_path: Path) -> None:
    target = tmp_path / "outside.jsonl"
    target.write_text("untouched", encoding="utf-8")
    active = tmp_path / "python-core.jsonl"
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")
    writer = DesktopDiagnosticWriter(
        directory=tmp_path,
        run_id=RUN_ID,
        app_version="0.7.0",
        component=DesktopDiagnosticComponent.PYTHON_CORE,
    )

    assert not writer.write("PYTHON_CORE_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.INFO)
    assert not writer.clear()
    assert target.read_text(encoding="utf-8") == "untouched"
