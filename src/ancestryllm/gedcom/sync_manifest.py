"""Snapshot identity and manifest validation for deterministic GEDCOM sync."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional, Sequence

from ancestryllm.core.cancellation import (
    cancellation_checkpoint,
)
from ancestryllm.core.ingress import (
    FileFingerprint,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
)
from ancestryllm.gedcom.sync_algorithms import (
    _sha256_file,
)
from ancestryllm.gedcom.sync_contracts import (
    MANIFEST_SCHEMA_VERSION,
    RECORD_PREFIXES,
    SOURCE_ID_RE,
    SUPPORTED_VENDORS,
    SnapshotSpec,
    SyncError,
    SyncSnapshotInput,
)


def _validate_snapshot_identity(source_id: str, vendor: str) -> None:
    """Validate stable snapshot identity fields for every calling adapter."""

    if not SOURCE_ID_RE.fullmatch(source_id):
        raise SyncError(
            "SYNC_CONFIGURATION",
            "A snapshot source ID is not valid.",
            "Stable source IDs are persisted across snapshot generations.",
            [
                "Start the ID with a lowercase letter.",
                "Use only lowercase letters, digits, underscores, or hyphens.",
            ],
        )
    if vendor not in SUPPORTED_VENDORS:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "A snapshot vendor is not recognized.",
            "Vendor metadata controls reporting and future compatibility profiles.",
            [f"Choose one of: {', '.join(SUPPORTED_VENDORS)}."],
        )


def _parse_snapshot_argument(value: str) -> tuple[str, str, Path]:
    """Parse ``SOURCE_ID:VENDOR=PATH`` without restricting path characters."""

    descriptor, separator, raw_path = value.partition("=")
    source_id, vendor_separator, vendor = descriptor.partition(":")
    if not separator or not vendor_separator or not raw_path:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "A snapshot descriptor is incomplete.",
            "The updater cannot distinguish the source, vendor, and file path.",
            [
                "Use --snapshot SOURCE_ID:VENDOR=/absolute/or/relative/file.ged.",
                "Example: --snapshot ancestry-main:ancestry=tree.ged.",
            ],
        )
    _validate_snapshot_identity(source_id, vendor)
    return source_id, vendor, Path(raw_path)


def _validate_exported_at(timestamp: str) -> None:
    """Require one explicit export time to be valid ISO-8601 text."""

    try:
        dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "An export date is not valid ISO-8601 text.",
            "Incorrect dates make snapshot history misleading.",
            ["Use a value such as 2026-07-17 or 2026-07-17T14:30:00-04:00."],
        ) from exc


def _snapshot_inputs_from_arguments(
    snapshots: Sequence[str],
    exported_at_values: Sequence[str] | None,
) -> tuple[SyncSnapshotInput, ...]:
    """Convert terminal-only repeated arguments into typed snapshot inputs."""

    explicit_dates: dict[str, str] = {}
    for value in exported_at_values or ():
        source_id, separator, timestamp = value.partition("=")
        if not separator or not timestamp:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "An export date descriptor is incomplete.",
                "An export date must be tied to one stable source ID.",
                ["Use --exported-at SOURCE_ID=YYYY-MM-DD or an ISO-8601 timestamp."],
            )
        _validate_exported_at(timestamp)
        explicit_dates[source_id] = timestamp

    inputs = tuple(
        SyncSnapshotInput(
            source_id=source_id,
            vendor=vendor,
            path=path,
            exported_at=explicit_dates.get(source_id),
        )
        for source_id, vendor, path in map(_parse_snapshot_argument, snapshots)
    )
    unknown_dates = sorted(set(explicit_dates) - {snapshot.source_id for snapshot in inputs})
    if unknown_dates:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "An --exported-at value refers to a source ID with no snapshot.",
            "The date would have no snapshot observation to describe.",
            ["Add the matching --snapshot or remove the unused --exported-at value."],
            details=(f"Unused export date entries: {len(unknown_dates)}",),
        )
    return inputs


def _header_export_date(
    path: Path,
    core: ModuleType,
    ingress: FileIngressPolicy,
    expected: FileSnapshot,
) -> Optional[str]:
    """Read a usable HEAD.DATE without treating it as genealogical evidence."""
    try:
        first = next(core.iter_gedcom_records(path, ingress, expected))
    except (StopIteration, OSError, ValueError):
        return None
    if first.tag != "HEAD":
        return None
    for block in core._top_level_blocks(first.lines):
        line = core.parse_gedcom_line(block[0])
        if line.tag == "DATE" and line.value.strip():
            return str(core.normalise_gedcom_date(line.value.strip()))
    return None


def _snapshot_specs(
    inputs: Sequence[SyncSnapshotInput],
    core: ModuleType,
    ingress: FileIngressPolicy,
) -> list[SnapshotSpec]:
    """Validate typed snapshot inputs and derive export timestamps."""

    specs: list[SnapshotSpec] = []
    seen: set[str] = set()
    for item in inputs:
        source_id = item.source_id
        vendor = item.vendor
        path = item.path
        _validate_snapshot_identity(source_id, vendor)
        path = ingress.normalize_path(path, FileKind.GEDCOM, absolute=True)
        if source_id in seen:
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Source ID {source_id!r} was supplied more than once.",
                "Only one snapshot can replace a source in a single generation.",
                ["Keep one --snapshot entry for each source ID."],
            )
        seen.add(source_id)
        snapshot = ingress.inspect(path, FileKind.GEDCOM)
        if item.exported_at is not None:
            _validate_exported_at(item.exported_at)
            exported_at = item.exported_at
            basis = "operator"
        else:
            header_date = _header_export_date(path, core, ingress, snapshot)
            if header_date:
                exported_at = header_date
                basis = "HEAD.DATE"
            else:
                exported_at = dt.datetime.fromtimestamp(
                    snapshot.modified_ns / 1_000_000_000, tz=dt.timezone.utc
                ).isoformat()
                basis = "file-mtime"
        fingerprint = ingress.fingerprint(
            path,
            FileKind.GEDCOM,
            expected=snapshot,
        )
        specs.append(
            SnapshotSpec(
                source_id=source_id,
                vendor=vendor,
                path=path,
                exported_at=exported_at,
                date_basis=basis,
                sha256=fingerprint.sha256,
                fingerprint=fingerprint,
            )
        )
    return sorted(specs, key=lambda item: (item.source_id, item.vendor, item.sha256))


def _new_manifest(
    master: Path,
    release_root: Path,
    master_fingerprint: FileFingerprint,
) -> dict[str, Any]:
    """Create an empty schema-v1 manifest for protected baseline seeding."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tree_id": str(uuid.uuid4()),
        "generation": 0,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "master": {"path": str(master), "sha256": master_fingerprint.sha256},
        "parent_release": None,
        "release_root": str(release_root),
        "active_snapshots": {},
        "snapshots": {},
        "person_bindings": {},
        "record_aliases": {},
        "blocks": {},
        "removed": [],
        "manual_tombstones": [],
        "next_ids": {prefix: 1 for prefix in set(RECORD_PREFIXES.values()) | {"X"}},
        "releases": [],
    }


def _manifest_invalid(field_name: str) -> SyncError:
    return SyncError(
        "MANIFEST_INVALID",
        f"The manifest field {field_name!r} has an unsupported structure.",
        "Malformed provenance cannot support a safe synchronization decision.",
        ["Restore an unmodified manifest from the matching release bundle."],
    )


def _manifest_timestamp(value: Any, field_name: str) -> dt.datetime:
    """Parse one required timezone-aware manifest timestamp."""
    if not isinstance(value, str) or not value:
        raise _manifest_invalid(field_name)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _manifest_invalid(field_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _manifest_invalid(field_name)
    return parsed


def _validate_manifest(value: dict[str, Any]) -> None:
    """Validate every container shape used before synchronization side effects."""

    required = {
        "schema_version",
        "tree_id",
        "generation",
        "master",
        "active_snapshots",
        "snapshots",
        "person_bindings",
        "record_aliases",
        "blocks",
        "removed",
        "manual_tombstones",
        "next_ids",
        "releases",
    }
    if required - value.keys():
        raise _manifest_invalid("required fields")
    if (
        isinstance(value["schema_version"], bool)
        or value["schema_version"] != MANIFEST_SCHEMA_VERSION
    ):
        raise _manifest_invalid("schema_version")
    if not isinstance(value["tree_id"], str) or not value["tree_id"]:
        raise _manifest_invalid("tree_id")
    if (
        isinstance(value["generation"], bool)
        or not isinstance(value["generation"], int)
        or value["generation"] < 0
    ):
        raise _manifest_invalid("generation")
    master = value["master"]
    if (
        not isinstance(master, dict)
        or not isinstance(master.get("path"), str)
        or not master["path"]
        or not isinstance(master.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", master["sha256"]) is None
    ):
        raise _manifest_invalid("master")
    mapping_fields = (
        "active_snapshots",
        "snapshots",
        "person_bindings",
        "record_aliases",
        "blocks",
        "next_ids",
    )
    if any(not isinstance(value[field_name], dict) for field_name in mapping_fields):
        raise _manifest_invalid("mapping")
    list_fields = ("removed", "manual_tombstones", "releases")
    if any(not isinstance(value[field_name], list) for field_name in list_fields):
        raise _manifest_invalid("list")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value["active_snapshots"].items()
    ):
        raise _manifest_invalid("active_snapshots")
    if any(
        not isinstance(key, str) or not isinstance(item, dict)
        for key, item in value["snapshots"].items()
    ):
        raise _manifest_invalid("snapshots")
    snapshot_vendors: dict[str, str] = {}
    for snapshot_id, snapshot in value["snapshots"].items():
        required_snapshot_strings = (
            "snapshot_id",
            "source_id",
            "vendor",
            "path",
            "sha256",
            "exported_at",
            "date_basis",
            "observed_at",
        )
        if (
            any(
                not isinstance(snapshot.get(field_name), str) or not snapshot[field_name]
                for field_name in required_snapshot_strings
            )
            or snapshot["snapshot_id"] != snapshot_id
            or re.fullmatch(r"[0-9a-f]{64}", snapshot["sha256"]) is None
        ):
            raise _manifest_invalid("snapshots")
        source_id = snapshot["source_id"]
        vendor = snapshot["vendor"]
        if (
            SOURCE_ID_RE.fullmatch(source_id) is None
            or vendor not in SUPPORTED_VENDORS
            or snapshot_id != f"{source_id}:{snapshot['sha256'][:20]}"
            or snapshot["date_basis"] not in {"operator", "HEAD.DATE", "file-mtime"}
        ):
            raise _manifest_invalid("snapshots")
        try:
            observed_at = dt.datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise _manifest_invalid("snapshots") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise _manifest_invalid("snapshots")
        established_vendor = snapshot_vendors.setdefault(source_id, vendor)
        if established_vendor != vendor:
            raise _manifest_invalid("snapshots")
    for source_id, snapshot_id in value["active_snapshots"].items():
        snapshot = value["snapshots"].get(snapshot_id)
        if (
            SOURCE_ID_RE.fullmatch(source_id) is None
            or snapshot is None
            or snapshot["source_id"] != source_id
        ):
            raise _manifest_invalid("active_snapshots")
    if set(value["active_snapshots"]) != set(snapshot_vendors):
        raise _manifest_invalid("active_snapshots")
    if any(
        not isinstance(source_id, str)
        or SOURCE_ID_RE.fullmatch(source_id) is None
        or source_id not in snapshot_vendors
        or not isinstance(bindings, dict)
        or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in bindings.items()
        )
        for source_id, bindings in value["person_bindings"].items()
    ):
        raise _manifest_invalid("person_bindings")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value["record_aliases"].items()
    ):
        raise _manifest_invalid("record_aliases")
    if any(
        not isinstance(pointer, str) or not isinstance(entries, dict)
        for pointer, entries in value["blocks"].items()
    ):
        raise _manifest_invalid("blocks")
    for pointer, entries in value["blocks"].items():
        if not pointer:
            raise _manifest_invalid("blocks")
        for block_hash, entry in entries.items():
            if (
                not isinstance(block_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", block_hash) is None
                or not isinstance(entry, dict)
                or not isinstance(entry.get("tag"), str)
                or not entry["tag"]
                or not isinstance(entry.get("observations"), list)
                or any(not isinstance(item, str) for item in entry["observations"])
                or any(item not in value["snapshots"] for item in entry["observations"])
                or not isinstance(entry.get("protected"), list)
                or any(not isinstance(item, str) for item in entry["protected"])
            ):
                raise _manifest_invalid("blocks")
            logical_identity = entry.get("logical_identity")
            if logical_identity is not None and (
                not isinstance(logical_identity, str)
                or re.fullmatch(r"[0-9a-f]{64}", logical_identity) is None
            ):
                raise _manifest_invalid("blocks")
            for generation_field in ("first_seen_generation", "last_seen_generation"):
                generation = entry.get(generation_field)
                if generation is not None and (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation < 0
                ):
                    raise _manifest_invalid("blocks")
    if any(not isinstance(item, dict) for field_name in list_fields for item in value[field_name]):
        raise _manifest_invalid("list entry")
    for tombstone in value["manual_tombstones"]:
        if (
            not isinstance(tombstone.get("person"), str)
            or not isinstance(tombstone.get("block_hash"), str)
            or not isinstance(tombstone.get("reason"), str)
            or isinstance(tombstone.get("generation"), bool)
            or not isinstance(tombstone.get("generation"), int)
            or tombstone["generation"] < 0
            or (
                tombstone.get("logical_identity") is not None
                and (
                    not isinstance(tombstone["logical_identity"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", tombstone["logical_identity"]) is None
                )
            )
        ):
            raise _manifest_invalid("manual_tombstones")
    release_timestamps: list[dt.datetime] = []
    for release in value["releases"]:
        if (
            isinstance(release.get("generation"), bool)
            or not isinstance(release.get("generation"), int)
            or release["generation"] < 1
            or not isinstance(release.get("path"), str)
            or not release["path"]
            or not isinstance(release.get("master_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", release["master_sha256"]) is None
        ):
            raise _manifest_invalid("releases")
        release_timestamps.append(_manifest_timestamp(release.get("created_at"), "releases"))
    generation = value["generation"]
    releases = value["releases"]
    if len(releases) != generation or any(
        release["generation"] != expected for expected, release in enumerate(releases, start=1)
    ):
        raise _manifest_invalid("releases")
    if any(later < earlier for earlier, later in pairwise(release_timestamps)):
        raise _manifest_invalid("releases")
    if releases and releases[-1]["master_sha256"] != master["sha256"]:
        raise _manifest_invalid("releases")
    parent = value.get("parent_release")
    if generation == 0:
        if parent is not None:
            raise _manifest_invalid("parent_release")
    else:
        if (
            not isinstance(parent, dict)
            or isinstance(parent.get("generation"), bool)
            or not isinstance(parent.get("generation"), int)
            or parent["generation"] != generation - 1
        ):
            raise _manifest_invalid("parent_release")
        parent_master = parent.get("master")
        if (
            not isinstance(parent_master, dict)
            or not isinstance(parent_master.get("path"), str)
            or not parent_master["path"]
            or not isinstance(parent_master.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", parent_master["sha256"]) is None
        ):
            raise _manifest_invalid("parent_release")
        if generation > 1 and parent_master["sha256"] != releases[-2]["master_sha256"]:
            raise _manifest_invalid("parent_release")
        parent_manifest_path = parent.get("manifest_path")
        parent_manifest_sha = parent.get("manifest_sha256")
        if generation == 1:
            if parent_manifest_path is not None or parent_manifest_sha is not None:
                raise _manifest_invalid("parent_release")
        elif (
            not isinstance(parent_manifest_path, str)
            or not parent_manifest_path
            or not isinstance(parent_manifest_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", parent_manifest_sha) is None
        ):
            raise _manifest_invalid("parent_release")
    for timestamp_field in ("created_at", "updated_at"):
        if timestamp_field in value:
            _manifest_timestamp(value[timestamp_field], timestamp_field)
    artifact_checksums = value.get("artifact_checksums")
    if artifact_checksums is not None and (
        not isinstance(artifact_checksums, dict)
        or set(artifact_checksums) != {"master.ged", "quality.md", "rollback.json", "update.md"}
        or any(
            not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            for checksum in artifact_checksums.values()
        )
    ):
        raise _manifest_invalid("artifact_checksums")
    if any(
        not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int) or item < 1
        for key, item in value["next_ids"].items()
    ):
        raise _manifest_invalid("next_ids")


def _verify_manifest_artifacts(path: Path, value: Mapping[str, Any]) -> None:
    """Verify the immutable release files named by a schema-v1 manifest."""
    checksums = value.get("artifact_checksums")
    if checksums is None:
        return
    assert isinstance(checksums, dict)
    for name, expected in checksums.items():
        cancellation_checkpoint()
        artifact = path.parent / name
        if not artifact.is_file() or _sha256_file(artifact) != expected:
            raise SyncError(
                "MANIFEST_MASTER_MISMATCH",
                "A release artifact does not match the supplied manifest.",
                "The release bundle may be incomplete, altered, or mixed across generations.",
                ["Select an unmodified master and manifest from one complete release bundle."],
            )


def _load_manifest(
    path: Path,
    ingress: FileIngressPolicy,
    manifest_fingerprint: FileFingerprint,
    master_fingerprint: FileFingerprint,
) -> dict[str, Any]:
    """Load and validate a manifest before using any provenance decisions."""
    value = ingress.read_json(
        path,
        FileKind.MANIFEST,
        require_object=True,
        expected=manifest_fingerprint.snapshot,
    )
    assert isinstance(value, dict)
    _validate_manifest(value)
    _verify_manifest_artifacts(path, value)
    expected = value["master"]["sha256"]
    actual = master_fingerprint.sha256
    if expected != actual:
        raise SyncError(
            "MANIFEST_MASTER_MISMATCH",
            "The master GEDCOM does not match the supplied manifest.",
            "Block ownership and person bindings may point to a different generation.",
            [
                "Select master.ged and manifest.json from the same release bundle.",
                "If the master was intentionally edited, run the rebase command first.",
            ],
            details=(f"Expected SHA-256: {expected}", f"Actual SHA-256: {actual}"),
        )
    return value


def _validate_snapshot_continuity(
    manifest: Mapping[str, Any],
    specs: Sequence[SnapshotSpec],
) -> None:
    """Keep each stable source bound to one vendor and content identity."""
    snapshots = manifest["snapshots"]
    vendors = {snapshot["source_id"]: snapshot["vendor"] for snapshot in snapshots.values()}
    for spec in specs:
        established_vendor = vendors.get(spec.source_id)
        existing_snapshot = snapshots.get(spec.snapshot_id)
        if established_vendor is not None and established_vendor != spec.vendor:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "A snapshot vendor does not match its existing source history.",
                "Changing vendor identity under one source ID corrupts provenance.",
                [
                    "Use the vendor already recorded for this stable source ID.",
                    "Use a new source ID when importing a different website source.",
                ],
            )
        if existing_snapshot is not None and existing_snapshot["sha256"] != spec.sha256:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "A snapshot conflicts with an existing content address.",
                "The updater cannot safely treat two different files as one observation.",
                ["Use a new stable source ID and preserve both snapshots for review."],
            )
