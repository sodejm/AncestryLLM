"""Publish one validated RootsMagic export as a new atomic directory."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ancestryllm.application.dto import ArtifactRef, ArtifactStatus, ServiceResult
from ancestryllm.application.mutations import MutationState
from ancestryllm.core.cancellation import cancellation_checkpoint, non_interruptible_section
from ancestryllm.core.directory_mutation import DirectoryMutation
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import FileKind
from ancestryllm.core.mutation import LocalMutationCoordinator
from ancestryllm.core.publication import paths_alias
from ancestryllm.gedcom.model import GedcomParseError
from ancestryllm.gedcom.serializer import serialize_gedcom_document
from ancestryllm.gedcom.sync_publication import (
    _exclusive_rename_directory,
    _write_bytes,
)
from ancestryllm.gedcom.validator import validate_gedcom_document
from ancestryllm.rootsmagic.core import RootsMagicReader
from ancestryllm.rootsmagic.mapping import RootsMagicMapper

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

__all__ = [
    "RootsMagicDirectoryExportResult",
    "RootsMagicDirectoryExporter",
    "export_parent_identity",
]

_PLATFORM = sys.platform
_ARTIFACT_NAMES = frozenset({"tree.ged", "report.md", "manifest.json"})


@dataclass(frozen=True, slots=True)
class RootsMagicDirectoryExportResult(ServiceResult):
    """Serializable descriptors of a committed, digest-verified export bundle."""

    manifest: ArtifactRef
    gedcom: ArtifactRef
    report: ArtifactRef
    state: MutationState


def _windows_stat_identity(handle: int) -> tuple[int, int]:
    """Read Python's full Windows stat identity from an already-held directory."""
    import ctypes
    from ctypes import wintypes

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_uint64), ("file_id", ctypes.c_ubyte * 16)]

    information = _FileIdInfo()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    get_information.restype = wintypes.BOOL
    if not get_information(handle, 18, ctypes.byref(information), ctypes.sizeof(information)):
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise ctypes.WinError(error)  # type: ignore[attr-defined]
    inode = int.from_bytes(information.file_id, "little")
    if inode == 0:
        raise OSError("The directory handle has no reliable identity.")
    return int(information.volume), inode


def export_parent_identity(target: Path, expected: tuple[int, int]) -> tuple[int, int]:
    """Verify Node's Windows IDs and translate through the same held handle."""
    if _PLATFORM != "win32":
        return expected

    handle: int | None = None
    try:
        handle = RootsMagicReader._windows_open_directory_handle(target.parent)
        if RootsMagicReader._windows_handle_identity(handle) != expected:
            raise ValueError("Replaced export parent")
        # libuv uses a 32-bit volume serial and 64-bit file index. Python 3.12+
        # can use 64/128-bit IDs; truncating them is not valid for every filesystem.
        return _windows_stat_identity(handle)
    except (OSError, ValueError) as exc:
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_PARENT_INVALID",
            "The selected export parent directory is unavailable or changed.",
            "Select an existing local parent directory again.",
            exit_code=2,
        ) from exc
    finally:
        if handle is not None:
            RootsMagicReader._windows_close_handle(handle)


def _verify_parent(target: Path, expected: tuple[int, int] | None) -> tuple[int, int]:
    """Bind publication to the original canonical directory selected by the caller."""

    try:
        parent = target.parent
        info = parent.lstat()
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISDIR(info.st_mode)
            or parent.resolve(strict=True) != parent
            or (expected is not None and identity != expected)
        ):
            raise ValueError("Replaced or noncanonical export parent")
        return identity
    except (OSError, ValueError) as exc:
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_PARENT_INVALID",
            "The selected export parent directory is unavailable or changed.",
            "Select an existing local parent directory again.",
            exit_code=2,
        ) from exc


def _artifact(payload: bytes, media_type: str, artifact_type: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"art_{uuid4().hex}",
        media_type=media_type,
        artifact_type=artifact_type,
        size_bytes=len(payload),
        status=ArtifactStatus.READY,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _digest(payload: bytes) -> dict[str, int | str]:
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _friendly_source_value(value: str, *, field: str) -> str:
    """Accept bounded opaque labels while keeping host paths out of public metadata."""

    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 500
        or any(ord(character) < 32 for character in normalized)
        or "/" in normalized
        or "\\" in normalized
    ):
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_SOURCE_METADATA_INVALID",
            f"The export source {field} is not a safe public label.",
            "Use a bounded opaque label that does not contain a filesystem path.",
            exit_code=2,
        )
    return normalized


def _validate_staged_export(stage: Path, expected_manifest: dict[str, Any]) -> None:
    """Verify the complete flat export using bytes read back from staging."""

    children = tuple(stage.iterdir())
    if {child.name for child in children} != _ARTIFACT_NAMES:
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_STAGE_INVALID",
            "The staged export does not contain the required artifacts.",
        )
    for child in children:
        info = child.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_STAGE_INVALID",
                "The staged export contains an unsafe artifact.",
            )
    try:
        actual_manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_STAGE_INVALID",
            "The staged export manifest could not be validated.",
        ) from exc
    if actual_manifest != expected_manifest:
        raise AncestryError(
            "ROOTSMAGIC_EXPORT_STAGE_INVALID",
            "The staged export manifest does not match the intended export.",
        )
    for name in ("tree.ged", "report.md"):
        try:
            payload = (stage / name).read_bytes()
        except OSError as exc:
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_STAGE_INVALID",
                "A staged export artifact could not be validated.",
            ) from exc
        if _digest(payload) != actual_manifest["files"][name]:
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_STAGE_INVALID",
                "A staged export artifact does not match its manifest digest.",
            )


class RootsMagicDirectoryExporter(RootsMagicMapper):
    """Map immutable RootsMagic data and exclusively publish a new directory."""

    def export(
        self,
        tree: Path,
        target: Path,
        *,
        root_person_id: str,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
        coordinator: LocalMutationCoordinator | None = None,
        source_ref: str | None = None,
        source_fingerprint: str | None = None,
        verify_source: Callable[[], None] | None = None,
        publication_guard: Callable[[], AbstractContextManager[None]] | None = None,
        expected_parent: tuple[int, int] | None = None,
    ) -> RootsMagicDirectoryExportResult:
        """Create a validated ``tree.ged``/report/manifest directory atomically."""

        normalized_root = str(root_person_id).strip()
        if not normalized_root:
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_ROOT_REQUIRED",
                "A root person is required for a RootsMagic directory export.",
                "Select a person and retry the export.",
                exit_code=2,
            )
        self._validate_mapping_options(
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            living=living,
        )
        source_tree = self.reader.ingress.normalize_path(
            tree,
            FileKind.ROOTSMAGIC,
            absolute=True,
        )
        resolved_target = self.reader.ingress.normalize_path(
            target,
            FileKind.ROOTSMAGIC,
            absolute=True,
        )
        if paths_alias(source_tree, resolved_target):
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_SOURCE_ALIAS",
                "The export directory must not alias the RootsMagic source.",
                exit_code=2,
            )
        if resolved_target.exists() or resolved_target.is_symlink():
            raise AncestryError(
                "ROOTSMAGIC_EXPORT_TARGET_EXISTS",
                "The export directory already exists.",
                "Choose a new destination directory.",
                exit_code=2,
            )
        parent_identity = _verify_parent(resolved_target, expected_parent)

        mapped = self._map_snapshot(
            source_tree,
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            root_person_id=normalized_root,
            scope=scope,
            generations=generations,
            living=living,
        )
        try:
            validate_gedcom_document(
                mapped.content.document,
                checkpoint=cancellation_checkpoint,
            )
            gedcom_payload = serialize_gedcom_document(mapped.content.document).encode("utf-8")
        except GedcomParseError as exc:
            raise AncestryError(
                "GEDCOM_VALIDATION_FAILED",
                "Generated GEDCOM content failed validation.",
                "Review the mapping options and retry the export.",
                exit_code=2,
            ) from exc

        report_payload = mapped.legacy_report.markdown(
            Path(source_tree.name),
            Path("tree.ged"),
            omitted_records=dict(mapped.omitted_records),
            sqlite_snapshot=mapped.sqlite_snapshot,
        ).encode("utf-8")
        default_ref = mapped.content.source_ref
        default_fingerprint = default_ref.rsplit(":", 1)[-1]
        public_ref = _friendly_source_value(
            source_ref if source_ref is not None else default_ref,
            field="reference",
        )
        public_fingerprint = _friendly_source_value(
            source_fingerprint if source_fingerprint is not None else default_fingerprint,
            field="fingerprint",
        )
        public_source_name = _friendly_source_value(source_tree.name, field="name")
        manifest: dict[str, Any] = {
            "schema": "ancestryllm.rootsmagic-directory-export.v1",
            "source": {
                "fingerprint": public_fingerprint,
                "name": public_source_name,
                "reference": public_ref,
                "sqlite_snapshot": mapped.sqlite_snapshot,
            },
            "selection": {
                "generations": generations,
                "living": living,
                "root_person_id": normalized_root,
                "scope": scope,
            },
            "format": {
                "destination": destination,
                "gedcom_version": gedcom_version,
                "profile": profile,
            },
            "files": {
                "report.md": _digest(report_payload),
                "tree.ged": _digest(gedcom_payload),
            },
        }
        manifest_payload = (
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

        if coordinator is None:
            with LocalMutationCoordinator() as owned_coordinator:
                return self._publish(
                    resolved_target,
                    mapped.source_path,
                    mapped.source_fingerprint,
                    gedcom_payload,
                    report_payload,
                    manifest,
                    manifest_payload,
                    owned_coordinator,
                    verify_source,
                    publication_guard,
                    parent_identity,
                )
        return self._publish(
            resolved_target,
            mapped.source_path,
            mapped.source_fingerprint,
            gedcom_payload,
            report_payload,
            manifest,
            manifest_payload,
            coordinator,
            verify_source,
            publication_guard,
            parent_identity,
        )

    def _publish(
        self,
        target: Path,
        source_path: Path,
        source_fingerprint: Any,
        gedcom_payload: bytes,
        report_payload: bytes,
        manifest: dict[str, Any],
        manifest_payload: bytes,
        coordinator: LocalMutationCoordinator,
        verify_source: Callable[[], None] | None,
        publication_guard: Callable[[], AbstractContextManager[None]] | None,
        parent_identity: tuple[int, int],
    ) -> RootsMagicDirectoryExportResult:
        mutation = DirectoryMutation(target, coordinator)
        stage = target.parent / f".ancestry-export-{uuid4().hex}"
        manifest_ref = _artifact(manifest_payload, "application/json", "rootsmagic-export-manifest")
        gedcom_ref = _artifact(gedcom_payload, "text/vnd.familysearch.gedcom", "gedcom")
        report_ref = _artifact(report_payload, "text/markdown", "rootsmagic-export-report")

        def result(state: MutationState) -> RootsMagicDirectoryExportResult:
            return RootsMagicDirectoryExportResult(
                manifest=manifest_ref,
                gedcom=gedcom_ref,
                report=report_ref,
                state=state,
            )

        try:
            _verify_parent(target, parent_identity)
            mutation.acquire()
            mutation.plan_staging(stage)
            _verify_parent(target, parent_identity)
            stage.mkdir(mode=0o700)
            mutation.track_staging(stage)
            _write_bytes(stage / "tree.ged", gedcom_payload, mutation=mutation)
            _write_bytes(stage / "report.md", report_payload, mutation=mutation)
            _write_bytes(stage / "manifest.json", manifest_payload, mutation=mutation)
            _validate_staged_export(stage, manifest)
            mutation.prepare(stage)
            guard = publication_guard if publication_guard is not None else nullcontext
            with guard():
                try:
                    self.reader.verify_source(source_path, source_fingerprint)
                except FileIngressError as exc:
                    raise self._source_changed(exc) from exc
                if verify_source is not None:
                    verify_source()
                mutation.committing()
                try:
                    with non_interruptible_section("publish RootsMagic export directory"):
                        _verify_parent(target, parent_identity)
                        _exclusive_rename_directory(stage, target)
                    state = mutation.finish()
                except BaseException:
                    if mutation.lease is not None:
                        state = mutation.finish()
                        if state is MutationState.COMMITTED:
                            return result(state)
                    raise
        except BaseException:
            if mutation.lease is not None:
                state = mutation.finish()
                if state is MutationState.COMMITTED:
                    return result(state)
            raise
        return result(state)
