"""Transport-neutral GEDCOM service contract coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.dto import (
    ArtifactAccess,
    ArtifactGrantRef,
    ArtifactStatus,
    ProviderSelection,
)
from ancestryllm.application.operations import (
    GedcomMergeRequest,
    GedcomQualityRequest,
    GedcomSubtreeRequest,
    GedcomSyncRequest,
    GedcomSyncSnapshot,
)
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.llm.policy import ConsentGrant

GEDCOM_MEDIA_TYPE = "text/vnd.gedcom"


def _write_person(
    path: Path,
    *,
    pointer: str,
    given_name: str,
    birth_date: str,
) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR AncestryLLM-Fictional-Contract",
                "1 GEDC",
                "2 VERS 5.5.5",
                "1 CHAR UTF-8",
                f"0 {pointer} INDI",
                f"1 NAME {given_name} /Example/",
                "1 BIRT",
                f"2 DATE {birth_date}",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


def _input_grant(
    registry: _ArtifactRegistry,
    path: Path,
    *,
    operation: str,
    media_type: str = GEDCOM_MEDIA_TYPE,
    artifact_type: str = "gedcom",
) -> ArtifactGrantRef:
    return registry.grant_input(
        path,
        operation=operation,
        media_type=media_type,
        artifact_type=artifact_type,
    )


def _output_grant(
    registry: _ArtifactRegistry,
    path: Path,
    *,
    operation: str,
    media_type: str,
    artifact_type: str,
) -> ArtifactGrantRef:
    return registry.grant_output(
        path,
        operation=operation,
        media_type=media_type,
        artifact_type=artifact_type,
    )


def _offline_provider() -> ProviderSelection:
    return ProviderSelection(provider_id="none", consent_id="unused-consent")


def _unexpected_consent_lookup(
    calls: list[str],
    consent_id: str,
) -> ConsentGrant:
    calls.append(consent_id)
    raise AssertionError("offline provider requested a consent lookup")


def _unresolved_grant(
    operation: str,
    access: ArtifactAccess,
    marker: str,
) -> ArtifactGrantRef:
    return ArtifactGrantRef(
        grant_id=f"grt_{marker * 32}",
        operation=operation,
        access=access,
    )


@pytest.mark.parametrize(
    "service_request",
    (
        GedcomMergeRequest(
            inputs=(_unresolved_grant("gedcom.merge", ArtifactAccess.READ, "a"),),
            output=_unresolved_grant("gedcom.merge", ArtifactAccess.WRITE, "b"),
            quality_report=_unresolved_grant(
                "gedcom.merge",
                ArtifactAccess.WRITE,
                "c",
            ),
            root_person_ref="@I1@",
            gedcom_version="5.5.5",
            provider=_offline_provider(),
            similarity_threshold=70,
        ),
        GedcomMergeRequest(
            inputs=(
                _unresolved_grant("gedcom.merge", ArtifactAccess.READ, "a"),
                _unresolved_grant("gedcom.merge", ArtifactAccess.READ, "b"),
            ),
            output=_unresolved_grant("gedcom.merge", ArtifactAccess.WRITE, "c"),
            quality_report=_unresolved_grant(
                "gedcom.merge",
                ArtifactAccess.WRITE,
                "d",
            ),
            root_person_ref=" ",
            gedcom_version="5.5.5",
            provider=_offline_provider(),
            similarity_threshold=70,
        ),
        GedcomMergeRequest(
            inputs=(
                _unresolved_grant("gedcom.merge", ArtifactAccess.READ, "a"),
                _unresolved_grant("gedcom.merge", ArtifactAccess.READ, "b"),
            ),
            output=_unresolved_grant("gedcom.merge", ArtifactAccess.WRITE, "c"),
            quality_report=_unresolved_grant(
                "gedcom.merge",
                ArtifactAccess.WRITE,
                "d",
            ),
            root_person_ref="@I1@",
            gedcom_version="5.5.6",
            provider=_offline_provider(),
            similarity_threshold=101,
        ),
        GedcomSubtreeRequest(
            source=_unresolved_grant("gedcom.subtree", ArtifactAccess.READ, "a"),
            output=_unresolved_grant("gedcom.subtree", ArtifactAccess.WRITE, "b"),
            root_person_ref="@I1@",
            scope="siblings",
            generations=-1,
            gedcom_version="5.5.6",
        ),
        GedcomQualityRequest(
            source=_unresolved_grant("gedcom.quality", ArtifactAccess.READ, "a"),
            output=_unresolved_grant("gedcom.quality", ArtifactAccess.WRITE, "b"),
            root_person_ref=None,
            provider=_offline_provider(),
        ),
    ),
)
def test_service_contract_rejects_invalid_requests_before_resolving_grants(
    service_request: GedcomMergeRequest | GedcomSubtreeRequest | GedcomQualityRequest,
) -> None:
    service = GedcomService()

    with pytest.raises(DomainFailure) as caught:
        if isinstance(service_request, GedcomMergeRequest):
            service.execute_merge(service_request)
        elif isinstance(service_request, GedcomSubtreeRequest):
            service.execute_subtree(service_request)
        else:
            service.execute_quality(service_request)

    assert caught.value.code is DomainFailureCode.INVALID_REQUEST
    assert caught.value.details == ()


def test_merge_contract_is_opaque_deterministic_and_network_free(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "quality.md"
    _write_person(
        first,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        second,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()
    consent_lookups: list[str] = []
    request = GedcomMergeRequest(
        inputs=(
            _input_grant(registry, first, operation="gedcom.merge"),
            _input_grant(registry, second, operation="gedcom.merge"),
        ),
        output=_output_grant(
            registry,
            output,
            operation="gedcom.merge",
            media_type=GEDCOM_MEDIA_TYPE,
            artifact_type="gedcom",
        ),
        quality_report=_output_grant(
            registry,
            report,
            operation="gedcom.merge",
            media_type="text/markdown",
            artifact_type="quality-report",
        ),
        root_person_ref="@I1@",
        gedcom_version="5.5.5",
        provider=_offline_provider(),
        similarity_threshold=70,
    )
    service = GedcomService(
        artifacts=registry,
        consent_lookup=lambda consent_id: _unexpected_consent_lookup(
            consent_lookups,
            consent_id,
        ),
    )

    first_result = service.execute_merge(request)
    second_result = service.execute_merge(request)

    assert consent_lookups == []
    assert first_result.gedcom.status is ArtifactStatus.READY
    assert first_result.quality_report.status is ArtifactStatus.READY
    assert first_result.gedcom.sha256 is not None
    assert first_result.quality_report.sha256 is not None
    assert first_result.root_person_ref.startswith("person:")
    assert len(first_result.root_person_ref.removeprefix("person:")) == 32
    assert first_result.changes == second_result.changes
    assert first_result.quality == second_result.quality
    assert first_result.provenance == second_result.provenance
    assert first_result.root_person_ref == second_result.root_person_ref
    assert first_result.gedcom.sha256 == second_result.gedcom.sha256
    assert first_result.quality_report.sha256 == second_result.quality_report.sha256
    serialized = first_result.to_json()
    assert str(tmp_path) not in serialized
    assert "first.ged" not in serialized
    assert "second.ged" not in serialized
    assert "@I1@" not in serialized
    assert "Ada" not in serialized


def test_subtree_opaque_root_is_consumable_by_quality_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ged"
    subtree = tmp_path / "subtree.ged"
    report = tmp_path / "quality.md"
    _write_person(
        source,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    registry = _ArtifactRegistry()
    service = GedcomService(artifacts=registry)
    subtree_result = service.execute_subtree(
        GedcomSubtreeRequest(
            source=_input_grant(registry, source, operation="gedcom.subtree"),
            output=_output_grant(
                registry,
                subtree,
                operation="gedcom.subtree",
                media_type=GEDCOM_MEDIA_TYPE,
                artifact_type="gedcom",
            ),
            root_person_ref="@I1@",
            scope="connected",
            generations=None,
            gedcom_version="5.5.5",
        )
    )

    quality_result = service.execute_quality(
        GedcomQualityRequest(
            source=_input_grant(registry, subtree, operation="gedcom.quality"),
            output=_output_grant(
                registry,
                report,
                operation="gedcom.quality",
                media_type="text/markdown",
                artifact_type="quality-report",
            ),
            root_person_ref=subtree_result.root_person_ref,
            provider=_offline_provider(),
        )
    )

    assert quality_result.report.status is ArtifactStatus.READY
    assert quality_result.report.sha256 is not None
    assert str(tmp_path) not in subtree_result.to_json()
    assert str(tmp_path) not in quality_result.to_json()
    assert "@I1@" not in subtree_result.to_json()


def test_merge_contract_cancellation_preserves_existing_bundle(
    tmp_path: Path,
) -> None:
    class CancelBeforePublication:
        def __init__(self) -> None:
            self.calls = 0

        def check_cancelled(self) -> None:
            self.calls += 1
            if self.calls >= 3:
                raise DomainFailure(DomainFailureCode.CANCELLED)

    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "quality.md"
    _write_person(
        first,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        second,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    output.write_text("previous fictional GEDCOM\n", encoding="utf-8")
    report.write_text("previous fictional quality report\n", encoding="utf-8")
    registry = _ArtifactRegistry()
    request = GedcomMergeRequest(
        inputs=(
            _input_grant(registry, first, operation="gedcom.merge"),
            _input_grant(registry, second, operation="gedcom.merge"),
        ),
        output=_output_grant(
            registry,
            output,
            operation="gedcom.merge",
            media_type=GEDCOM_MEDIA_TYPE,
            artifact_type="gedcom",
        ),
        quality_report=_output_grant(
            registry,
            report,
            operation="gedcom.merge",
            media_type="text/markdown",
            artifact_type="quality-report",
        ),
        root_person_ref="@I1@",
        gedcom_version="5.5.5",
        provider=_offline_provider(),
        similarity_threshold=70,
    )
    cancellation = CancelBeforePublication()

    with pytest.raises(DomainFailure) as caught:
        GedcomService(artifacts=registry).execute_merge(
            request,
            cancellation=cancellation,
        )

    assert caught.value.code is DomainFailureCode.CANCELLED
    assert cancellation.calls == 3
    assert output.read_text(encoding="utf-8") == "previous fictional GEDCOM\n"
    assert report.read_text(encoding="utf-8") == "previous fictional quality report\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "first.ged",
        "merged.ged",
        "quality.md",
        "second.ged",
    ]


def test_cloud_contract_requires_explicit_consent_before_publication(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "merged.ged"
    report = tmp_path / "quality.md"
    _write_person(
        first,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        second,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()
    request = GedcomMergeRequest(
        inputs=(
            _input_grant(registry, first, operation="gedcom.merge"),
            _input_grant(registry, second, operation="gedcom.merge"),
        ),
        output=_output_grant(
            registry,
            output,
            operation="gedcom.merge",
            media_type=GEDCOM_MEDIA_TYPE,
            artifact_type="gedcom",
        ),
        quality_report=_output_grant(
            registry,
            report,
            operation="gedcom.merge",
            media_type="text/markdown",
            artifact_type="quality-report",
        ),
        root_person_ref="@I1@",
        gedcom_version="5.5.5",
        provider=ProviderSelection(provider_id="openai", model_id="fictional-model"),
        similarity_threshold=70,
    )

    with pytest.raises(DomainFailure) as caught:
        GedcomService(artifacts=registry).execute_merge(request)

    assert caught.value.code is DomainFailureCode.PROVIDER_CONSENT_REQUIRED
    assert not output.exists()
    assert not report.exists()


def _sync_update_request(
    registry: _ArtifactRegistry,
    *,
    master: Path,
    snapshot: Path,
    release_root: Path,
    provider: ProviderSelection | None = None,
    dry_run: bool = False,
    automatic_identity_resolution: bool = True,
) -> GedcomSyncRequest:
    operation = "gedcom.sync"
    return GedcomSyncRequest(
        sync_command="update",
        master=_input_grant(registry, master, operation=operation),
        release_root=_output_grant(
            registry,
            release_root,
            operation=operation,
            media_type="application/x-directory",
            artifact_type="sync-release-root",
        ),
        provider=provider or _offline_provider(),
        snapshots=(
            GedcomSyncSnapshot(
                source_id="ancestry-main",
                vendor="ancestry",
                artifact=_input_grant(registry, snapshot, operation=operation),
                exported_at="2026-07-29T12:00:00Z",
            ),
        ),
        initialize_manifest=True,
        quality_report_enabled=False,
        dry_run=dry_run,
        automatic_identity_resolution=automatic_identity_resolution,
    )


def test_sync_update_contract_is_opaque_deterministic_and_network_free(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    release_root = tmp_path / "releases"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()
    consent_lookups: list[str] = []
    result = GedcomService(
        artifacts=registry,
        consent_lookup=lambda consent_id: _unexpected_consent_lookup(
            consent_lookups,
            consent_id,
        ),
    ).execute_sync(
        _sync_update_request(
            registry,
            master=master,
            snapshot=snapshot,
            release_root=release_root,
        )
    )

    assert consent_lookups == []
    assert result.committed is True
    assert len(result.artifacts) == 5
    assert {artifact.artifact_type for artifact in result.artifacts} == {
        "gedcom",
        "quality-report",
        "rollback-manifest",
        "sync-manifest",
        "update-report",
    }
    assert all(artifact.status is ArtifactStatus.READY for artifact in result.artifacts)
    assert all(artifact.sha256 is not None for artifact in result.artifacts)
    assert len(result.provenance) == 5
    assert all(provenance.rule_code == "sync-published" for provenance in result.provenance)
    generation = next(release_root.iterdir())
    assert {path.name for path in generation.iterdir()} == {
        "master.ged",
        "manifest.json",
        "quality.md",
        "rollback.json",
        "update.md",
    }
    serialized = result.to_json()
    assert str(tmp_path) not in serialized
    assert "master.ged" not in serialized
    assert "snapshot.ged" not in serialized
    assert "@I1@" not in serialized
    assert "Ada" not in serialized


def test_sync_dry_run_returns_accounting_without_publication(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    release_root = tmp_path / "releases"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()

    result = GedcomService(artifacts=registry).execute_sync(
        _sync_update_request(
            registry,
            master=master,
            snapshot=snapshot,
            release_root=release_root,
            dry_run=True,
        )
    )

    assert result.committed is False
    assert result.artifacts == ()
    assert result.provenance == ()
    assert result.changes.created >= 1
    assert not release_root.exists()


def test_sync_cloud_selection_without_auto_resolution_remains_network_free(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    release_root = tmp_path / "releases"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()
    consent_lookups: list[str] = []
    service = GedcomService(
        artifacts=registry,
        consent_lookup=lambda consent_id: _unexpected_consent_lookup(
            consent_lookups,
            consent_id,
        ),
    )

    result = service.execute_sync(
        _sync_update_request(
            registry,
            master=master,
            snapshot=snapshot,
            release_root=release_root,
            provider=ProviderSelection(
                provider_id="openai",
                model_id="fictional-model",
            ),
            automatic_identity_resolution=False,
        )
    )

    assert consent_lookups == []
    assert result.committed is True


def test_sync_cloud_contract_requires_consent_before_publication(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    release_root = tmp_path / "releases"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()

    with pytest.raises(DomainFailure) as caught:
        GedcomService(artifacts=registry).execute_sync(
            _sync_update_request(
                registry,
                master=master,
                snapshot=snapshot,
                release_root=release_root,
                provider=ProviderSelection(
                    provider_id="openai",
                    model_id="fictional-model",
                ),
            )
        )

    assert caught.value.code is DomainFailureCode.PROVIDER_CONSENT_REQUIRED
    assert not release_root.exists()


def test_sync_cancellation_prevents_publication(
    tmp_path: Path,
) -> None:
    class CancelDuringReconciliation:
        def __init__(self) -> None:
            self.calls = 0

        def check_cancelled(self) -> None:
            self.calls += 1
            if self.calls >= 2:
                raise DomainFailure(DomainFailureCode.CANCELLED)

    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    release_root = tmp_path / "releases"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    registry = _ArtifactRegistry()
    cancellation = CancelDuringReconciliation()

    with pytest.raises(DomainFailure) as caught:
        GedcomService(artifacts=registry).execute_sync(
            _sync_update_request(
                registry,
                master=master,
                snapshot=snapshot,
                release_root=release_root,
            ),
            cancellation=cancellation,
        )

    assert caught.value.code is DomainFailureCode.CANCELLED
    assert cancellation.calls == 2
    assert not release_root.exists()


def test_sync_rebase_uses_the_same_opaque_result_contract(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.ged"
    snapshot = tmp_path / "snapshot.ged"
    update_root = tmp_path / "updates"
    _write_person(
        master,
        pointer="@I1@",
        given_name="Ada",
        birth_date="1 JAN 1900",
    )
    _write_person(
        snapshot,
        pointer="@I2@",
        given_name="Grace",
        birth_date="2 FEB 1910",
    )
    update_registry = _ArtifactRegistry()
    update_result = GedcomService(artifacts=update_registry).execute_sync(
        _sync_update_request(
            update_registry,
            master=master,
            snapshot=snapshot,
            release_root=update_root,
        )
    )
    assert update_result.committed is True
    update_generation = next(update_root.iterdir())
    released_master = update_generation / "master.ged"
    manifest = update_generation / "manifest.json"
    edited_master = tmp_path / "edited-master.ged"
    edited_master.write_text(
        released_master.read_text(encoding="utf-8").replace(
            "0 TRLR",
            "1 NOTE Fictional manual note\n0 TRLR",
        ),
        encoding="utf-8",
    )
    rebase_root = tmp_path / "rebases"
    rebase_registry = _ArtifactRegistry()
    operation = "gedcom.sync"
    rebase_request = GedcomSyncRequest(
        sync_command="rebase",
        master=_input_grant(rebase_registry, edited_master, operation=operation),
        manifest=_input_grant(
            rebase_registry,
            manifest,
            operation=operation,
            media_type="application/json",
            artifact_type="sync-manifest",
        ),
        release_root=_output_grant(
            rebase_registry,
            rebase_root,
            operation=operation,
            media_type="application/x-directory",
            artifact_type="sync-release-root",
        ),
        provider=_offline_provider(),
        reason="Protect a reviewed fictional manual note.",
    )

    result = GedcomService(artifacts=rebase_registry).execute_sync(rebase_request)

    assert result.committed is True
    assert len(result.artifacts) == 5
    assert result.changes.created >= 1
    assert all(provenance.rule_code == "rebase-published" for provenance in result.provenance)
    assert str(tmp_path) not in result.to_json()
    assert "edited-master.ged" not in result.to_json()


@pytest.mark.parametrize(
    "sync_request",
    (
        GedcomSyncRequest(
            sync_command="replace",
            master=_unresolved_grant("gedcom.sync", ArtifactAccess.READ, "a"),
            release_root=_unresolved_grant(
                "gedcom.sync",
                ArtifactAccess.WRITE,
                "b",
            ),
            provider=_offline_provider(),
        ),
        GedcomSyncRequest(
            sync_command="update",
            master=_unresolved_grant("gedcom.sync", ArtifactAccess.READ, "a"),
            release_root=_unresolved_grant(
                "gedcom.sync",
                ArtifactAccess.WRITE,
                "b",
            ),
            provider=_offline_provider(),
            initialize_manifest=True,
            quality_report_enabled=False,
        ),
        GedcomSyncRequest(
            sync_command="rebase",
            master=_unresolved_grant("gedcom.sync", ArtifactAccess.READ, "a"),
            manifest=_unresolved_grant(
                "gedcom.sync",
                ArtifactAccess.READ,
                "b",
            ),
            release_root=_unresolved_grant(
                "gedcom.sync",
                ArtifactAccess.WRITE,
                "c",
            ),
            provider=ProviderSelection(provider_id="openai"),
            reason="Fictional reviewed edit.",
        ),
    ),
)
def test_sync_rejects_invalid_requests_before_resolving_grants(
    sync_request: GedcomSyncRequest,
) -> None:
    with pytest.raises(DomainFailure) as caught:
        GedcomService().execute_sync(sync_request)

    assert caught.value.code is DomainFailureCode.INVALID_REQUEST
    assert caught.value.details == ()
