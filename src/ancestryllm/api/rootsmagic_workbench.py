"""Native-only capability mediation and private RootsMagic job results."""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
from ancestryllm.application._rootsmagic_workbench import RootsMagicWorkbench, sanitized_source_name
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import CommittedJobResult, JobState

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.application.jobs import JobLifecycleService, PublicJobSnapshot
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest
    from ancestryllm.core.jobs import JobReporter, JobSnapshot

_CAPABILITY = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
_PLATFORM = sys.platform


class _PathManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: int = Field(ge=1, le=1)
    path: str = Field(min_length=1, max_length=32768)


class _OutputManifest(_PathManifest):
    parent_dev: str = Field(pattern=r"^(0|[1-9][0-9]{0,39})$")
    parent_ino: str = Field(pattern=r"^(0|[1-9][0-9]{0,39})$")


class _SourceManifest(_PathManifest):
    size_bytes: int = Field(ge=0, le=8 * 1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    friendly_name: str = Field(min_length=1, max_length=1024)


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


def _export_parent_identity(manifest: _OutputManifest) -> tuple[int, int]:
    """Verify Node's Windows IDs and translate through the same held handle."""
    expected = (int(manifest.parent_dev), int(manifest.parent_ino))
    if _PLATFORM != "win32":
        return expected
    from ancestryllm.rootsmagic.source import RootsMagicReader

    handle: int | None = None
    try:
        handle = RootsMagicReader._windows_open_directory_handle(Path(manifest.path).parent)
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


def _unavailable() -> AncestryError:
    return AncestryError(
        "ROOTSMAGIC_RESULT_UNAVAILABLE", "The result is unavailable in this session."
    )


@dataclass(slots=True)
class _OwnedJob:
    kind: str
    source_ref: str | None
    discarded: bool = False


class NativeRootsMagicWorkbench:
    """Consume Main-issued manifests without exposing paths to the control API."""

    def __init__(self, *, directory: Path, jobs: JobLifecycleService) -> None:
        self._directory = directory.resolve(strict=True)
        if self._directory != directory.absolute() or not self._directory.is_dir():
            raise ValueError("RootsMagic mediation requires a canonical private directory.")
        self._jobs = jobs
        self._service = RootsMagicWorkbench()
        self._owned: dict[str, _OwnedJob] = {}
        self._lock = RLock()
        self._closed = False
        self._unsubscribe = jobs.manager.subscribe(self._on_job)

    def _manifest(self, capability: str, kind: str) -> _PathManifest:
        if not _CAPABILITY.fullmatch(capability):
            raise AncestryError("ROOTSMAGIC_CAPABILITY_INVALID", "Invalid native capability.")
        path = self._directory / f"{capability}.rootsmagic-{kind}.json"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError("Invalid manifest")
            if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError("Unprotected manifest")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("Replaced manifest")
                payload = stream.read(65537)
            # Consume before parsing so malformed or replayed authority cannot survive.
            path.unlink()
            model = _SourceManifest if kind == "source" else _OutputManifest
            manifest = model.model_validate_json(payload)
            target = Path(manifest.path)
            if not target.is_absolute():
                raise ValueError("Relative path")
            if isinstance(
                manifest, _SourceManifest
            ) and manifest.friendly_name != sanitized_source_name(target.name):
                raise ValueError("Invalid friendly name")
            return manifest
        except (OSError, ValueError) as exc:
            raise AncestryError(
                "ROOTSMAGIC_CAPABILITY_INVALID",
                "The native capability is invalid or expired.",
                "Select the source or destination again.",
            ) from exc

    def _on_job(self, snapshot: JobSnapshot) -> None:
        if snapshot.state not in _TERMINAL:
            return
        with self._lock:
            owned = self._owned.get(snapshot.job_id)
            if owned is None:
                return
            if (
                owned.kind == "inspection"
                and snapshot.state != JobState.COMPLETED
                and owned.source_ref
            ):
                self._service.discard(owned.source_ref)
            if self._closed or owned.discarded:
                self._jobs.manager.discard_result(snapshot.job_id)
                self._owned.pop(snapshot.job_id, None)

    def _submit(self, owned: _OwnedJob, work: Callable[[JobReporter], Any]) -> PublicJobSnapshot:
        with self._lock:
            if self._closed:
                raise _unavailable()
            # Bound private result retention separately from Task Center history.
            for job_id in tuple(self._owned):
                if len(self._owned) < 100:
                    break
                if self._jobs.manager.get(job_id).state in _TERMINAL:
                    self._jobs.manager.discard_result(job_id)
                    self._owned.pop(job_id)
            if len(self._owned) >= 100:
                raise AncestryError(
                    "ROOTSMAGIC_JOB_CAPACITY", "Wait for a workbench job to finish."
                )
            submitted = self._jobs.manager.submit_with_progress(f"rootsmagic.{owned.kind}", work)
            self._owned[submitted.job_id] = owned
            self._on_job(self._jobs.manager.get(submitted.job_id))
            return self._jobs.get(submitted.job_id)

    def inspect(self, capability: str) -> PublicJobSnapshot:
        """Consume one picker capability and inspect the original SQLite generation."""
        with self._lock:
            if self._closed:
                raise _unavailable()
            manifest = self._manifest(capability, "source")
            assert isinstance(manifest, _SourceManifest)
            owned = _OwnedJob("inspection", None)

            def work(reporter: JobReporter) -> Any:
                reporter.update("rootsmagic.inspect")
                summary = self._service.inspect(
                    Path(manifest.path), size_bytes=manifest.size_bytes, sha256=manifest.sha256
                )
                owned.source_ref = summary.source_ref
                reporter.set_outcome(
                    "RootsMagic source inspected.",
                    next_action="Choose a preset to browse the immutable source.",
                )
                return {"schema_version": 1, **cast("dict[str, Any]", summary.to_serializable())}

            return self._submit(owned, work)

    @staticmethod
    def presets() -> dict[str, Any]:
        """List fixed definitions without disclosing source metadata."""
        return {
            "schema_version": 1,
            "queries": [item.to_serializable() for item in RootsMagicPresetService.definitions()],
        }

    def query(self, request: RootsMagicPresetQueryRequest) -> PublicJobSnapshot:
        """Submit a bounded preset against a retained source capability."""
        self._service.source(request.source_ref)

        def work(reporter: JobReporter) -> Any:
            reporter.update("rootsmagic.query")
            result = self._service.query(request)
            reporter.set_outcome(
                "RootsMagic query completed.", next_action="Review the result page."
            )
            return {"schema_version": 1, **cast("dict[str, Any]", result.to_serializable())}

        return self._submit(_OwnedJob("query", request.source_ref), work)

    def export(
        self,
        source_ref: str,
        output_capability: str,
        *,
        root_person_id: int,
        scope: str,
        generations: int | None,
        living: str,
    ) -> PublicJobSnapshot:
        """Publish one new verified export folder through durable coordination."""
        from ancestryllm.application._rootsmagic_directory_export import RootsMagicDirectoryExporter

        source = self._service.source(source_ref)
        with self._lock:
            manifest = self._manifest(output_capability, "output")
            assert isinstance(manifest, _OutputManifest)

        def work(reporter: JobReporter) -> CommittedJobResult:
            reporter.update("rootsmagic.export")
            with source.lock:
                source.verify()
                exported = RootsMagicDirectoryExporter(reader=source.reader).export(
                    source.path,
                    Path(manifest.path),
                    root_person_id=str(root_person_id),
                    scope=scope,
                    generations=generations,
                    living=living,
                    source_ref=source_ref,
                    source_fingerprint=source.summary.fingerprint,
                    verify_source=source.verify,
                    publication_guard=source.publication_guard,
                    expected_parent=_export_parent_identity(manifest),
                )
            return CommittedJobResult(
                {
                    "schema_version": 1,
                    "artifact_id": exported.manifest.artifact_id,
                    "display_name": Path(manifest.path).name,
                    "source_ref": source_ref,
                    "source_fingerprint": source.summary.fingerprint,
                    "profile_code": "portable",
                    "gedcom_version": "5.5.5",
                }
            )

        return self._submit(_OwnedJob("export", source_ref), work)

    def result(self, job_id: str) -> dict[str, Any]:
        """Return only this native session's completed, still-authorized result."""
        with self._lock:
            owned = self._owned.get(job_id)
            if self._closed or owned is None or owned.discarded:
                raise _unavailable()
            if owned.source_ref:
                self._service.source(owned.source_ref)
            snapshot = self._jobs.manager.get(job_id)
            if snapshot.state != JobState.COMPLETED or not isinstance(snapshot.result, dict):
                raise _unavailable()
            return {"schema_version": 1, "kind": owned.kind, "result": snapshot.result}

    def discard(self, source_ref: str) -> None:
        """Revoke the source and all retained pages immediately."""
        self._service.discard(source_ref)
        with self._lock:
            for job_id, owned in tuple(self._owned.items()):
                if owned.source_ref == source_ref:
                    owned.discarded = True
                    self._jobs.manager.discard_result(job_id)
                    self._on_job(self._jobs.manager.cancel(job_id))

    def close(self) -> None:
        """Revoke all capabilities on sidecar shutdown; never revive old grants."""
        self._service.close()
        with self._lock:
            self._closed = True
            for job_id, owned in tuple(self._owned.items()):
                owned.discarded = True
                self._jobs.manager.discard_result(job_id)
                self._on_job(self._jobs.manager.cancel(job_id))
            self._unsubscribe()
