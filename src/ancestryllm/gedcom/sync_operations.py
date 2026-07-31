"""Transport-neutral update and rebase orchestration for GEDCOM sync."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

from ancestryllm.core.cancellation import (
    CancellationError,
    cancellation_checkpoint,
    non_interruptible_section,
)
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import (
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
)
from ancestryllm.gedcom import sync_publication
from ancestryllm.gedcom.contracts import IdentityResolver
from ancestryllm.gedcom.sync_algorithms import (
    _block_key,
    _block_logical_identity,
    _json_bytes,
    _map_nonpeople,
    _match_people,
    _quality_report,
    _reconcile_person_blocks,
    _render_update_report,
    _rewrite_lines,
    _seed_snapshot_history,
    _sha256_file,
)
from ancestryllm.gedcom.sync_contracts import (
    CancellationCheck,
    SyncAccounting,
    SyncCommand,
    SyncError,
    SyncExecutionResult,
    SyncRebaseCommand,
    SyncStats,
    SyncUpdateCommand,
    _checkpoint,
)
from ancestryllm.gedcom.sync_manifest import (
    _load_manifest,
    _new_manifest,
    _snapshot_specs,
    _validate_manifest,
    _validate_snapshot_continuity,
    _verify_manifest_artifacts,
)


def _sync_accounting(stats: SyncStats, quality: Any | None = None) -> SyncAccounting:
    """Map one update's detailed stats to stable service-owned counts."""

    severities = Counter(
        str(getattr(finding, "severity", "")).casefold()
        for finding in getattr(quality, "findings", ())
    )
    return SyncAccounting(
        created=(len(stats.added_people) + len(stats.added_facts) + len(stats.citations_attached)),
        updated=(
            len(stats.mapped_people)
            + len(stats.consolidated_facts)
            + len(stats.citations_deduplicated)
            + len(stats.source_records_consolidated)
        ),
        unchanged=len(stats.unchanged_people),
        conflicts=len(stats.conflicts),
        warnings=(
            len(stats.unresolved_people) + len(stats.removed) + len(stats.disappeared_retained)
        ),
        information=severities["low"],
        quality_warnings=severities["medium"],
        errors=severities["critical"] + severities["high"],
        resolved=len(stats.record_aliases),
    )


def _rebase_accounting(
    additions: Mapping[str, set[str]],
    deletions: Mapping[str, set[str]],
) -> SyncAccounting:
    """Map explicit manual rebase changes to the same deterministic contract."""

    return SyncAccounting(
        created=sum(len(hashes) for hashes in additions.values()),
        updated=sum(len(hashes) for hashes in deletions.values()),
    )


def _normalize_command_paths(
    command: SyncCommand,
    ingress: FileIngressPolicy,
) -> SyncCommand:
    """Normalize every standalone user-supplied sync path at one boundary."""

    master = ingress.normalize_path(command.master, FileKind.GEDCOM, absolute=True)
    release_root = ingress.normalize_path(
        command.release_root,
        FileKind.MANIFEST,
        absolute=True,
    )
    if isinstance(command, SyncUpdateCommand):
        manifest = (
            ingress.normalize_path(command.manifest, FileKind.MANIFEST, absolute=True)
            if command.manifest is not None
            else None
        )
        return dataclasses.replace(
            command,
            master=master,
            release_root=release_root,
            manifest=manifest,
        )
    manifest = ingress.normalize_path(
        command.manifest,
        FileKind.MANIFEST,
        absolute=True,
    )
    return dataclasses.replace(
        command,
        master=master,
        release_root=release_root,
        manifest=manifest,
    )


def _perform_update(
    args: SyncUpdateCommand,
    core: ModuleType,
    ingress: FileIngressPolicy,
    identity_resolver: IdentityResolver | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> SyncExecutionResult:
    """Execute one offline-first update and publish an atomic generation bundle."""
    _checkpoint(cancellation_check)
    master: Path = args.master
    release_root: Path = args.release_root
    master_fingerprint = ingress.fingerprint(master, FileKind.GEDCOM)
    if args.initialize_manifest and args.manifest:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "--initialize-manifest and --manifest were supplied together.",
            "Initialization and continuation have different provenance guarantees.",
            ["Use --initialize-manifest only for a master with no manifest."],
        )
    if not args.initialize_manifest and not args.manifest:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "No manifest was supplied for an existing synchronization tree.",
            "Without it, snapshot ownership and safe removals cannot be proven.",
            [
                "Pass --manifest from the same release as --master.",
                "Or use --initialize-manifest once for a legacy master.",
            ],
        )
    if not args.no_quality_report and not args.quality_root_person:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "Quality reporting is enabled but no quality root person was supplied.",
            "Direct-ancestor priorities require an unambiguous root.",
            [
                "Add --quality-root-person with a master pointer or unique name.",
                "Or add --no-quality-report.",
            ],
        )
    specs = _snapshot_specs(args.snapshots, core, ingress)
    manifest_path: Path | None = args.manifest
    manifest_fingerprint = (
        ingress.fingerprint(manifest_path, FileKind.MANIFEST) if manifest_path is not None else None
    )
    if args.initialize_manifest:
        manifest = _new_manifest(master, release_root, master_fingerprint)
    else:
        assert manifest_path is not None
        assert manifest_fingerprint is not None
        manifest = _load_manifest(
            manifest_path,
            ingress,
            manifest_fingerprint,
            master_fingerprint,
        )
    _validate_snapshot_continuity(manifest, specs)

    def verify_inputs() -> None:
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        for spec in specs:
            ingress.verify(spec.path, FileKind.GEDCOM, spec.fingerprint)
        if manifest_path is not None and manifest_fingerprint is not None:
            ingress.verify(
                manifest_path,
                FileKind.MANIFEST,
                manifest_fingerprint,
            )

    guarded_identity_resolver = identity_resolver
    if identity_resolver is not None:
        selected_identity_resolver = identity_resolver

        def verify_and_resolve(left: Any, right: Any) -> Mapping[str, object]:
            verify_inputs()
            try:
                return selected_identity_resolver(left, right)
            finally:
                verify_inputs()

        guarded_identity_resolver = verify_and_resolve

    if all(
        manifest.get("active_snapshots", {}).get(spec.source_id) == spec.snapshot_id
        for spec in specs
    ):
        verify_inputs()
        _checkpoint(cancellation_check)
        return SyncExecutionResult(
            exit_code=0,
            output=(
                "No update was needed: every supplied snapshot is already active "
                "with the same SHA-256 checksum. No release files were changed.\n"
            ),
        )
    try:
        sources = core.load_sources(
            [master, *(spec.path for spec in specs)],
            ingress,
            {
                master: master_fingerprint.snapshot,
                **{spec.path: spec.fingerprint.snapshot for spec in specs},
            },
        )
    except (OSError, ValueError) as exc:
        raise SyncError(
            "SYNC_PARSE",
            "A master or snapshot GEDCOM could not be parsed safely.",
            "Publishing a partial generation could break relationships or citations.",
            ["Open the named file and repair the reported GEDCOM line, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    _checkpoint(cancellation_check)
    stats = SyncStats()
    pointer_map, bindings, _ = _match_people(
        sources,
        specs,
        manifest,
        core,
        stats,
        provider_id=args.provider,
        identity_resolver=guarded_identity_resolver,
    )
    _checkpoint(cancellation_check)
    pointer_map, nonpeople = _map_nonpeople(sources, pointer_map, manifest, core, stats)
    _checkpoint(cancellation_check)
    people, block_registry = _reconcile_person_blocks(
        sources,
        specs,
        pointer_map,
        manifest,
        core,
        stats,
        initialize=args.initialize_manifest,
    )
    _checkpoint(cancellation_check)
    nonpeople = [
        core.GedcomRecord(
            _rewrite_lines(record.lines, pointer_map, core),
            record.source_file,
            record.sequence,
        )
        for record in nonpeople
    ]
    family_records = [record for record in nonpeople if record.tag == "FAM"]
    people = core.enrich_relationship_context(people, family_records)
    head = next(
        (record for record in sources[0].records if record.tag == "HEAD"),
        core.GedcomRecord(["0 HEAD"], str(master), 0),
    )
    output_records = [head, *nonpeople]
    output_source = core.ParsedSource(master, output_records, {})
    next_generation = int(manifest.get("generation", 0)) + 1
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_name = f"g{next_generation:04d}-{timestamp}"
    manifest["generation"] = next_generation
    manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["blocks"] = block_registry
    manifest["person_bindings"].update(bindings)
    manifest["record_aliases"].update(stats.record_aliases)
    _seed_snapshot_history(manifest, specs)
    if args.dry_run:
        report = _render_update_report(
            manifest,
            specs,
            stats,
            "not-written-dry-run",
            provider_id=args.provider,
            dry_run=True,
        )
        verify_inputs()
        _checkpoint(cancellation_check)
        return SyncExecutionResult(
            exit_code=0,
            output=report,
            accounting=_sync_accounting(stats),
        )
    release_root_capability = sync_publication._ensure_release_root(release_root)
    final_dir = release_root / release_name
    published_artifacts = tuple(
        final_dir / name
        for name in (
            "master.ged",
            "manifest.json",
            "update.md",
            "quality.md",
            "rollback.json",
        )
    )
    quality: Any | None = None
    staging: Path | None = None
    staging_name: str | None = None
    staging_descriptor: int | None = None
    staging_identity: sync_publication._DirectoryIdentity | None = None
    staging_marker_name: str | None = None
    staging_marker_descriptor: int | None = None
    staging_marker_identity: sync_publication._DirectoryIdentity | None = None
    transaction: sync_publication._PublicationTransactionState | None = None
    try:
        (
            staging,
            staging_name,
            staging_descriptor,
            staging_identity,
            staging_marker_name,
            staging_marker_descriptor,
            staging_marker_identity,
        ) = sync_publication._create_staging_directory(release_root_capability, ".gedcom-sync-")
        _checkpoint(cancellation_check)
        staged_master = staging / "master.ged"
        core.write_gedcom(
            people,
            staged_master,
            source_documents=[output_source],
            pointer_map=dict(pointer_map),
            gedcom_version=args.gedcom_version,
        )
        master_sha = _sha256_file(staged_master)
        parent_master = copy.deepcopy(manifest.get("master"))
        parent_manifest_path = str(manifest_path) if manifest_path is not None else None
        parent_manifest_sha = (
            manifest_fingerprint.sha256 if manifest_fingerprint is not None else None
        )
        manifest["parent_release"] = {
            "generation": next_generation - 1,
            "master": parent_master,
            "manifest_path": parent_manifest_path,
            "manifest_sha256": parent_manifest_sha,
        }
        manifest["master"] = {
            "path": str(final_dir / "master.ged"),
            "sha256": master_sha,
        }
        manifest["releases"].append(
            {
                "generation": next_generation,
                "path": str(final_dir),
                "master_sha256": master_sha,
                "created_at": manifest["updated_at"],
            }
        )
        report_text = _render_update_report(
            manifest,
            specs,
            stats,
            master_sha,
            provider_id=args.provider,
            dry_run=False,
        )
        sync_publication._write_bytes(staging / "update.md", report_text.encode("utf-8"))
        if not args.no_quality_report:
            assert args.quality_root_person is not None
            quality = _quality_report(
                people,
                output_records,
                output_source,
                args.quality_root_person,
                final_dir / "master.ged",
                core,
            )
            sync_publication._write_bytes(
                staging / "quality.md",
                core.render_quality_report(quality).encode("utf-8"),
            )
        else:
            sync_publication._write_bytes(
                staging / "quality.md",
                b"# Quality Report Disabled\n\nNo quality analysis was requested.\n",
            )
        rollback = {
            "schema_version": 1,
            "current_generation": next_generation,
            "current_master": str(final_dir / "master.ged"),
            "current_master_sha256": master_sha,
            "previous": manifest["parent_release"],
            "instructions": (
                "To roll back, select the previous release's matching master.ged "
                "and manifest.json for the next update. Do not overwrite releases."
            ),
        }
        sync_publication._write_bytes(staging / "rollback.json", _json_bytes(rollback))
        manifest["artifact_checksums"] = {
            name: _sha256_file(staging / name)
            for name in ("master.ged", "quality.md", "rollback.json", "update.md")
        }
        manifest_payload = _json_bytes(manifest)
        sync_publication._write_bytes(staging / "manifest.json", manifest_payload)
        verify_inputs()
        _checkpoint(cancellation_check)
        assert staging_marker_descriptor is not None
        assert staging_marker_name is not None
        assert staging_marker_identity is not None
        assert staging_identity is not None
        transaction = sync_publication._PublicationTransactionState(staging_marker_descriptor)
        with non_interruptible_section("publishing incremental release"):
            finalization_error = sync_publication._publish_and_finalize_directory(
                staging_name,
                release_name,
                release_root_capability,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                staging_marker_identity,
                transaction,
            )
            if finalization_error is not None:
                raise finalization_error
        if not transaction.committed:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release transaction did not reach a committed state.",
                "Publication stopped before the ownership marker was removed.",
                ["Inspect the release root and retry with a new patch version."],
            )
    except BaseException as exc:
        if transaction is not None and transaction.committed:
            sync_publication._close_descriptor_quietly(staging_descriptor)
            sync_publication._close_capability_quietly(release_root_capability)
            if isinstance(exc, CancellationError):
                raise
            return SyncExecutionResult(
                exit_code=0,
                committed=True,
                artifacts=published_artifacts,
                accounting=_sync_accounting(stats, quality),
            )
        cleanup_marker_descriptor = (
            transaction.marker_descriptor if transaction is not None else staging_marker_descriptor
        )
        if (
            staging_name is not None
            and staging_identity is not None
            and staging_marker_name is not None
            and staging_marker_identity is not None
        ):
            sync_publication._cleanup_staging_directory(
                release_root_capability,
                staging_name,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                cleanup_marker_descriptor,
                staging_marker_identity,
            )
            staging_descriptor = None
            staging_marker_descriptor = None
        sync_publication._cleanup_empty_release_root(release_root_capability)
        raise
    sync_publication._close_descriptor_quietly(staging_descriptor)
    sync_publication._close_capability_quietly(release_root_capability)
    return SyncExecutionResult(
        exit_code=0,
        output="\n".join(
            (
                f"Incremental update complete: {final_dir}",
                f"Master GEDCOM: {final_dir / 'master.ged'}",
                f"Manifest: {final_dir / 'manifest.json'}",
                f"Update report: {final_dir / 'update.md'}",
            )
        )
        + "\n",
        committed=True,
        artifacts=published_artifacts,
        accounting=_sync_accounting(stats, quality),
    )


def _master_block_index(
    path: Path,
    core: ModuleType,
    ingress: FileIngressPolicy,
    expected: FileSnapshot,
) -> dict[str, dict[str, dict[str, str]]]:
    """Index person blocks by pointer for explicit manual rebase comparison."""
    result: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for record in core.iter_gedcom_records(path, ingress, expected):
        cancellation_checkpoint()
        if record.tag != "INDI":
            continue
        for block in core._top_level_blocks(record.lines):
            cancellation_checkpoint()
            result[record.pointer][_block_key(block, core)] = {
                "tag": core.parse_gedcom_line(block[0]).tag,
                "logical_identity": _block_logical_identity(block, core),
            }
    return result


def _perform_rebase(
    args: SyncRebaseCommand,
    core: ModuleType,
    ingress: FileIngressPolicy,
    cancellation_check: CancellationCheck | None = None,
) -> SyncExecutionResult:
    """Adopt external edits explicitly as protected manual provenance."""
    _checkpoint(cancellation_check)
    master: Path = args.master
    manifest_path: Path = args.manifest
    release_root: Path = args.release_root
    master_fingerprint = ingress.fingerprint(master, FileKind.GEDCOM)
    manifest_fingerprint = ingress.fingerprint(manifest_path, FileKind.MANIFEST)
    manifest = ingress.read_json(
        manifest_path,
        FileKind.MANIFEST,
        require_object=True,
        expected=manifest_fingerprint.snapshot,
    )
    assert isinstance(manifest, dict)
    _validate_manifest(manifest)
    _verify_manifest_artifacts(manifest_path, manifest)
    previous_path = ingress.normalize_path(
        str(manifest.get("master", {}).get("path", "")),
        FileKind.GEDCOM,
        absolute=True,
    )
    previous_fingerprint = ingress.fingerprint(previous_path, FileKind.GEDCOM)
    expected_previous_sha = str(manifest["master"]["sha256"])
    if previous_fingerprint.sha256 != expected_previous_sha:
        raise SyncError(
            "MANIFEST_MASTER_MISMATCH",
            "The previous master GEDCOM does not match the supplied manifest.",
            "Manual rebase comparisons require the exact immutable parent generation.",
            [
                "Restore master.ged and manifest.json from the same release bundle.",
                "Preserve the externally edited master separately, then retry the rebase.",
            ],
            details=(
                f"Expected SHA-256: {expected_previous_sha}",
                f"Actual SHA-256: {previous_fingerprint.sha256}",
            ),
        )
    previous = _master_block_index(
        previous_path,
        core,
        ingress,
        previous_fingerprint.snapshot,
    )
    current = _master_block_index(
        master,
        core,
        ingress,
        master_fingerprint.snapshot,
    )
    _checkpoint(cancellation_check)
    additions = {
        pointer: set(hashes) - set(previous.get(pointer, {}))
        for pointer, hashes in current.items()
        if set(hashes) - set(previous.get(pointer, {}))
    }
    deletions = {
        pointer: set(hashes) - set(current.get(pointer, {}))
        for pointer, hashes in previous.items()
        if set(hashes) - set(current.get(pointer, {}))
    }
    if deletions and not args.accept_manual_deletions:
        details = tuple(
            f"{pointer}: {len(hashes)} removed block(s)"
            for pointer, hashes in sorted(deletions.items())
        )
        raise SyncError(
            "SYNC_UNSAFE_REMOVAL",
            "The externally edited master removes existing person details.",
            "Without explicit confirmation, the updater cannot know whether the "
            "deletions were intentional.",
            [
                "Review the listed people in the old and edited masters.",
                "If every deletion is intentional, rerun with --accept-manual-deletions.",
            ],
            details=details,
        )
    next_generation = int(manifest.get("generation", 0)) + 1
    revived = {
        (pointer, block_hash) for pointer, hashes in additions.items() for block_hash in hashes
    }
    manifest["manual_tombstones"] = [
        tombstone
        for tombstone in manifest.get("manual_tombstones", [])
        if (tombstone.get("person"), tombstone.get("block_hash")) not in revived
    ]
    for pointer, hashes in additions.items():
        _checkpoint(cancellation_check)
        registry = manifest.setdefault("blocks", {}).setdefault(pointer, {})
        for block_hash in hashes:
            _checkpoint(cancellation_check)
            entry = registry.setdefault(
                block_hash,
                {
                    "tag": current[pointer][block_hash]["tag"],
                    "kind": "person-block",
                    "observations": [],
                    "protected": [],
                    "logical_identity": current[pointer][block_hash]["logical_identity"],
                },
            )
            entry.setdefault(
                "logical_identity",
                current[pointer][block_hash]["logical_identity"],
            )
            if "manual" not in entry["protected"]:
                entry["protected"].append("manual")
    if deletions:
        manifest.setdefault("manual_tombstones", []).extend(
            {
                "generation": next_generation,
                "person": pointer,
                "block_hash": block_hash,
                "logical_identity": previous[pointer][block_hash]["logical_identity"],
                "reason": args.reason,
            }
            for pointer, hashes in deletions.items()
            for block_hash in hashes
        )
    summary = (
        "# GEDCOM Manual Rebase Report\n\n"
        f"- Reason: {args.reason}\n"
        f"- Added or changed blocks protected as manual: "
        f"{sum(map(len, additions.values()))}\n"
        f"- Confirmed manual deletions: {sum(map(len, deletions.values()))}\n"
        "- No website snapshots were processed.\n"
    )
    if args.dry_run:
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        ingress.verify(previous_path, FileKind.GEDCOM, previous_fingerprint)
        ingress.verify(
            manifest_path,
            FileKind.MANIFEST,
            manifest_fingerprint,
        )
        _checkpoint(cancellation_check)
        return SyncExecutionResult(
            exit_code=0,
            output=summary,
            accounting=_rebase_accounting(additions, deletions),
        )
    release_root_capability = sync_publication._ensure_release_root(release_root)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = release_root / f"g{next_generation:04d}-{timestamp}"
    published_artifacts = tuple(
        final_dir / name
        for name in (
            "master.ged",
            "manifest.json",
            "update.md",
            "quality.md",
            "rollback.json",
        )
    )
    staging: Path | None = None
    staging_name: str | None = None
    staging_descriptor: int | None = None
    staging_identity: sync_publication._DirectoryIdentity | None = None
    staging_marker_name: str | None = None
    staging_marker_descriptor: int | None = None
    staging_marker_identity: sync_publication._DirectoryIdentity | None = None
    transaction: sync_publication._PublicationTransactionState | None = None
    try:
        (
            staging,
            staging_name,
            staging_descriptor,
            staging_identity,
            staging_marker_name,
            staging_marker_descriptor,
            staging_marker_identity,
        ) = sync_publication._create_staging_directory(release_root_capability, ".gedcom-rebase-")
        _checkpoint(cancellation_check)
        ingress.copy_to(
            master,
            staging / "master.ged",
            FileKind.GEDCOM,
            expected=master_fingerprint,
        )
        master_sha = _sha256_file(staging / "master.ged")
        prior = copy.deepcopy(manifest.get("master"))
        manifest["generation"] = next_generation
        manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest["parent_release"] = {
            "generation": next_generation - 1,
            "master": prior,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_fingerprint.sha256,
        }
        manifest["master"] = {
            "path": str(final_dir / "master.ged"),
            "sha256": master_sha,
        }
        manifest["releases"].append(
            {
                "generation": next_generation,
                "path": str(final_dir),
                "master_sha256": master_sha,
                "created_at": manifest["updated_at"],
                "kind": "manual-rebase",
            }
        )
        sync_publication._write_bytes(staging / "update.md", summary.encode("utf-8"))
        sync_publication._write_bytes(
            staging / "quality.md",
            b"# Quality Report\n\nRun the next update or basic quality command.\n",
        )
        rollback = {
            "schema_version": 1,
            "current_generation": next_generation,
            "previous": manifest["parent_release"],
            "instructions": "Select the previous matching master and manifest to roll back.",
        }
        sync_publication._write_bytes(staging / "rollback.json", _json_bytes(rollback))
        manifest["artifact_checksums"] = {
            name: _sha256_file(staging / name)
            for name in ("master.ged", "quality.md", "rollback.json", "update.md")
        }
        sync_publication._write_bytes(staging / "manifest.json", _json_bytes(manifest))
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        ingress.verify(previous_path, FileKind.GEDCOM, previous_fingerprint)
        ingress.verify(
            manifest_path,
            FileKind.MANIFEST,
            manifest_fingerprint,
        )
        _checkpoint(cancellation_check)
        assert staging_marker_descriptor is not None
        assert staging_marker_name is not None
        assert staging_marker_identity is not None
        assert staging_identity is not None
        transaction = sync_publication._PublicationTransactionState(staging_marker_descriptor)
        with non_interruptible_section("publishing incremental release"):
            finalization_error = sync_publication._publish_and_finalize_directory(
                staging_name,
                final_dir.name,
                release_root_capability,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                staging_marker_identity,
                transaction,
            )
            if finalization_error is not None:
                raise finalization_error
        if not transaction.committed:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release transaction did not reach a committed state.",
                "Publication stopped before the ownership marker was removed.",
                ["Inspect the release root and retry with a new patch version."],
            )
    except BaseException as exc:
        if transaction is not None and transaction.committed:
            sync_publication._close_descriptor_quietly(staging_descriptor)
            sync_publication._close_capability_quietly(release_root_capability)
            if isinstance(exc, CancellationError):
                raise
            return SyncExecutionResult(
                exit_code=0,
                committed=True,
                artifacts=published_artifacts,
                accounting=_rebase_accounting(additions, deletions),
            )
        cleanup_marker_descriptor = (
            transaction.marker_descriptor if transaction is not None else staging_marker_descriptor
        )
        if (
            staging_name is not None
            and staging_identity is not None
            and staging_marker_name is not None
            and staging_marker_identity is not None
        ):
            sync_publication._cleanup_staging_directory(
                release_root_capability,
                staging_name,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                cleanup_marker_descriptor,
                staging_marker_identity,
            )
            staging_descriptor = None
            staging_marker_descriptor = None
        sync_publication._cleanup_empty_release_root(release_root_capability)
        raise
    sync_publication._close_descriptor_quietly(staging_descriptor)
    sync_publication._close_capability_quietly(release_root_capability)
    return SyncExecutionResult(
        exit_code=0,
        output=f"Manual rebase complete: {final_dir}\n",
        committed=True,
        artifacts=published_artifacts,
        accounting=_rebase_accounting(additions, deletions),
    )


def _execute_typed_command(
    command: SyncCommand,
    core: ModuleType,
    ingress: FileIngressPolicy,
    *,
    identity_resolver: IdentityResolver | None,
    cancellation_check: CancellationCheck | None,
) -> SyncExecutionResult:
    """Execute one normalized typed command without adapter concerns."""

    normalized = _normalize_command_paths(command, ingress)
    if isinstance(normalized, SyncUpdateCommand):
        if normalized.provider != "none" and normalized.auto and identity_resolver is None:
            raise AncestryError(
                "LLM_SERVICE_UNAVAILABLE",
                "Incremental LLM assistance requires the modular application service.",
            )
        return _perform_update(
            normalized,
            core,
            ingress,
            identity_resolver if normalized.auto else None,
            cancellation_check,
        )
    return _perform_rebase(normalized, core, ingress, cancellation_check)


def _with_error_contract(
    operation: Callable[[], SyncExecutionResult],
    *,
    raise_errors: bool,
) -> SyncExecutionResult:
    """Apply the stable coded sync error contract around one operation."""

    try:
        return operation()
    except FileIngressError as exc:
        if raise_errors:
            raise
        return SyncExecutionResult(
            exit_code=exc.exit_code,
            error=exc.render() + "\n",
        )
    except SyncError as exc:
        if raise_errors:
            raise exc.as_ancestry_error() from exc
        return SyncExecutionResult(exit_code=exc.exit_code, error=exc.render())
    except AncestryError:
        raise
    except Exception as exc:
        error = SyncError(
            "SYNC_OUTPUT",
            "The incremental operation stopped before publishing a release.",
            "An unexpected failure was caught to preserve atomic output.",
            [
                "Retry with --verbose and review the coded error above.",
                "Preserve the master, manifest, and snapshots for troubleshooting.",
            ],
            details=(f"Error class: {type(exc).__name__}",),
        )
        if raise_errors:
            raise error.as_ancestry_error() from exc
        return SyncExecutionResult(exit_code=error.exit_code, error=error.render())


def execute_command(
    command: SyncCommand,
    core: ModuleType,
    ingress: FileIngressPolicy | None = None,
    *,
    identity_resolver: IdentityResolver | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Execute a typed sync command for a non-terminal application adapter."""

    policy = ingress or FileIngressPolicy()
    return _with_error_contract(
        lambda: _execute_typed_command(
            command,
            core,
            policy,
            identity_resolver=identity_resolver,
            cancellation_check=cancellation_check,
        ),
        raise_errors=raise_errors,
    )
