"""Packaged private control-sidecar bootstrap.

The Electron main process supplies the launch secret through one bounded stdin
frame.  No launch secret is accepted through command-line arguments or the
environment, and the public readiness frame deliberately contains no secret.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, BinaryIO, NoReturn

from platformdirs import user_config_path, user_data_path
from uvicorn import Server

from ancestryllm.api.app import create_app
from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.server import LOOPBACK_HOST, create_uvicorn_config
from ancestryllm.api.settings import ApiSettings
from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.application.gedcom_jobs import GedcomJobFacade
from ancestryllm.application.jobs import JOB_SCHEMA_VERSION, JobLifecycleService, ShutdownAssessment
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import APP_NAME, AppConfig
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager
from ancestryllm.core.secrets import KeyringSecretStore, MemorySecretStore, SecretSourceMode
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.llm.chat import ChatService
from ancestryllm.llm.chat_streaming import ChatStreamingService
from ancestryllm.llm.endpoint_validation import EndpointValidationService
from ancestryllm.llm.profiles import ProviderProfileService
from ancestryllm.llm.provider_configuration import ProviderConfigurationService
from ancestryllm.llm.registry import ProviderRegistry
from ancestryllm.llm.service import LLMService
from ancestryllm.observability.structured_diagnostics import (
    DesktopDiagnosticComponent,
    DesktopDiagnosticSeverity,
    DesktopDiagnosticWriter,
    validate_desktop_diagnostic_run_id,
)
from ancestryllm.storage.database import Database
from ancestryllm.storage.diagnostics import StartupConfigurationFailure, diagnose_startup
from ancestryllm.storage.job_events import SqlJobEventRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

    from ancestryllm.core.commands import ModuleDescriptor
    from ancestryllm.core.secrets import SecretStore
    from ancestryllm.storage.diagnostics import StartupDiagnosticReport

SIDECAR_BUILD = "0.6.0"
MAX_LAUNCH_FRAME_BYTES = 4096
STARTUP_TIMEOUT_SECONDS = 10.0
WINDOWS_KILL_ON_JOB_CLOSE = 0x00002000
WINDOWS_EXTENDED_LIMIT_INFORMATION = 9
NATIVE_VERIFICATION_EPHEMERAL_WORKSPACE_ENV = "ANCESTRYLLM_NATIVE_VERIFICATION_EPHEMERAL_WORKSPACE"

_process_tree_guard: object | None = None

DiagnosticMetadata = Mapping[str, bool | int | None]
DiagnosticRecorder = Callable[[str, DesktopDiagnosticSeverity, DiagnosticMetadata | None], None]


@dataclass(frozen=True, slots=True)
class LaunchFrame:
    """One private, exact-build launch request from Electron main."""

    contract: str
    app_build: str
    bearer_token: str = field(repr=False)
    diagnostic_run_id: str = field(repr=False)
    diagnostic_directory: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.contract != API_CONTRACT:
            raise ValueError("unsupported sidecar contract")
        if self.app_build != SIDECAR_BUILD:
            raise ValueError("sidecar build does not match the application build")
        validate_desktop_diagnostic_run_id(self.diagnostic_run_id)
        if "\0" in self.diagnostic_directory or not Path(self.diagnostic_directory).is_absolute():
            raise ValueError("diagnostic directory must be an absolute path")
        # Reuse the API boundary's validation rather than creating a second
        # token/build/host policy here.
        self.settings()

    def settings(self) -> ApiSettings:
        """Convert the verified launch frame into validated sidecar settings."""
        return ApiSettings(
            bearer_token=self.bearer_token,
            app_build=self.app_build,
            sidecar_build=SIDECAR_BUILD,
            provider_id="none",
        )


@dataclass(frozen=True, slots=True)
class _SidecarDiagnosticRecorders:
    """Keep Python component streams correlated without affecting runtime flow."""

    core_writer: DesktopDiagnosticWriter
    sidecar_writer: DesktopDiagnosticWriter

    @staticmethod
    def _record(
        writer: DesktopDiagnosticWriter,
        fallback_writer: DesktopDiagnosticWriter,
        code: str,
        severity: DesktopDiagnosticSeverity,
        metadata: DiagnosticMetadata | None = None,
    ) -> None:
        try:
            if writer.write(code, severity, metadata):
                return
            fallback_writer.write(
                "DIAGNOSTIC_WRITER_UNAVAILABLE",
                DesktopDiagnosticSeverity.WARNING,
                {"writer_failed": True},
            )
        except BaseException:  # noqa: BLE001 - diagnostics never control runtime flow
            with suppress(BaseException):
                fallback_writer.write(
                    "DIAGNOSTIC_WRITER_UNAVAILABLE",
                    DesktopDiagnosticSeverity.WARNING,
                    {"writer_failed": True},
                )

    def core(
        self,
        code: str,
        severity: DesktopDiagnosticSeverity,
        metadata: DiagnosticMetadata | None = None,
    ) -> None:
        """Record one core event through the non-blocking boundary."""

        self._record(self.core_writer, self.sidecar_writer, code, severity, metadata)

    def sidecar(
        self,
        code: str,
        severity: DesktopDiagnosticSeverity,
        metadata: DiagnosticMetadata | None = None,
    ) -> None:
        """Record one sidecar event through the non-blocking boundary."""

        self._record(self.sidecar_writer, self.core_writer, code, severity, metadata)


def _create_sidecar_diagnostic_recorders(
    frame: LaunchFrame,
    *,
    directory: Path | None = None,
) -> _SidecarDiagnosticRecorders:
    """Create the two fixed local component streams for this launch only."""

    diagnostic_directory = directory or Path(frame.diagnostic_directory)
    return _SidecarDiagnosticRecorders(
        core_writer=DesktopDiagnosticWriter(
            directory=diagnostic_directory,
            run_id=frame.diagnostic_run_id,
            app_version=frame.app_build,
            component=DesktopDiagnosticComponent.PYTHON_CORE,
        ),
        sidecar_writer=DesktopDiagnosticWriter(
            directory=diagnostic_directory,
            run_id=frame.diagnostic_run_id,
            app_version=frame.app_build,
            component=DesktopDiagnosticComponent.DESKTOP_SIDECAR,
        ),
    )


def _record_diagnostic(
    recorder: DiagnosticRecorder | None,
    code: str,
    severity: DesktopDiagnosticSeverity,
    metadata: DiagnosticMetadata | None = None,
) -> None:
    """Invoke an injected diagnostic boundary without allowing propagation."""

    if recorder is None:
        return
    try:
        recorder(code, severity, metadata)
    except BaseException:  # noqa: BLE001 - diagnostics cannot block safety behavior
        return


class _EmptyRegistry:
    """Expose no domain modules until separately owned routes are implemented."""

    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


@dataclass(slots=True)
class _SidecarLifecycle:
    """Own restart reconciliation and encrypted job persistence."""

    database: Database
    startup_diagnostics: Callable[[], StartupDiagnosticReport]
    chat_service: ChatService
    llm_service: LLMService
    gedcom_service: GedcomService
    chat_streaming_service: ChatStreamingService
    job_lifecycle: JobLifecycleService | None = field(init=False, default=None, repr=False)

    def _close_owned_resources(
        self,
        startup_job_service: JobLifecycleService | None = None,
    ) -> None:
        first_failure: BaseException | None = None
        job_service = startup_job_service or self.job_lifecycle
        self.job_lifecycle = None
        actions = []
        if job_service is not None:
            actions.append(job_service.close)
        actions.extend((self.chat_service.close, self.llm_service.close, self.database.close))
        for action in actions:
            try:
                action()
            except BaseException as exc:  # noqa: BLE001 - every owned resource must close
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure

    async def startup(self) -> None:
        """Open writable storage only after startup diagnostics authorize it."""

        if not self.startup_diagnostics().mutations_allowed:
            return
        service: JobLifecycleService | None = None
        try:
            self.database.initialize()
            repository = SqlJobEventRepository(self.database)
            service = JobLifecycleService(
                JobManager(start_id=repository.next_job_number()),
                repository,
            )
            service.startup()
            await self.chat_streaming_service.startup()
        except BaseException:
            with suppress(BaseException):
                await self.chat_streaming_service.shutdown()
            with suppress(BaseException):
                self._close_owned_resources(service)
            raise
        assert service is not None
        self.job_lifecycle = service

    def jobs(self) -> JobLifecycleService:
        """Return the ready job boundary or a stable degraded-mode failure."""

        if self.job_lifecycle is None:
            raise AncestryError(
                "JOB_SERVICE_UNAVAILABLE",
                "Background jobs are unavailable while startup safety checks are blocked.",
                "Resolve startup diagnostics, then restart the desktop application.",
            )
        return self.job_lifecycle

    def gedcom_jobs(self) -> GedcomJobFacade:
        """Bind GEDCOM operations to the ready background-job service."""

        return GedcomJobFacade(service=self.gedcom_service, jobs=self.jobs())

    def prepare_job_shutdown(self, action: str, timeout_seconds: float) -> ShutdownAssessment:
        """Authorize degraded shutdown or delegate to the live job boundary."""

        if self.job_lifecycle is None:
            return ShutdownAssessment(
                schema_version=JOB_SCHEMA_VERSION,
                safe_to_quit=True,
                active_jobs=(),
            )
        return self.job_lifecycle.prepare_shutdown(
            action=action,
            timeout_seconds=timeout_seconds,
        )

    async def shutdown(self) -> None:
        first_failure: BaseException | None = None
        try:
            await self.chat_streaming_service.shutdown()
        except BaseException as exc:  # noqa: BLE001 - every owned resource must close
            first_failure = exc
        try:
            self._close_owned_resources()
        except BaseException as exc:  # noqa: BLE001 - preserve the first failure
            if first_failure is None:
                first_failure = exc
        if first_failure is not None:
            raise first_failure


def _create_native_windows_process_tree_guard() -> object:
    """Assign this process to a kill-on-close Windows Job Object.

    The non-inheritable handle remains owned by this process. Windows closes it
    on every process exit path, including a crash, and then terminates every
    descendant that inherited membership in the job.
    """

    class _IoCounters(ctypes.Structure):
        _fields_ = (
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        )

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    create_job.restype = wintypes.HANDLE
    set_job_information = kernel32.SetInformationJobObject
    set_job_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_job_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    assign_process.restype = wintypes.BOOL
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_job(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    try:
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = WINDOWS_KILL_ON_JOB_CLOSE
        if not set_job_information(
            handle,
            WINDOWS_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        if not assign_process(handle, get_current_process()):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        return handle
    except BaseException:
        close_handle(handle)
        raise


def acquire_windows_process_tree_guard(
    platform: str = sys.platform,
    create_native: Callable[[], object] = _create_native_windows_process_tree_guard,
) -> object | None:
    """Create the Windows crash-safe tree guard, or no-op elsewhere."""

    if platform != "win32":
        return None
    try:
        return create_native()
    except Exception as error:
        raise RuntimeError("Windows process-tree guard unavailable") from error


def parse_launch_frame(stream: BinaryIO) -> LaunchFrame:
    """Parse exactly one newline-terminated, bounded JSON frame and then EOF."""

    raw = stream.read(MAX_LAUNCH_FRAME_BYTES + 1)
    if not raw or len(raw) > MAX_LAUNCH_FRAME_BYTES or not raw.endswith(b"\n"):
        raise ValueError("invalid sidecar launch frame")
    if stream.read(1) != b"":
        raise ValueError("unexpected data after sidecar launch frame")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid sidecar launch frame") from error
    if not isinstance(document, dict) or set(document) != {
        "contract",
        "app_build",
        "bearer_token",
        "diagnostic_run_id",
        "diagnostic_directory",
    }:
        raise ValueError("invalid sidecar launch frame fields")
    if not all(isinstance(value, str) for value in document.values()):
        raise ValueError("invalid sidecar launch frame values")
    return LaunchFrame(
        contract=document["contract"],
        app_build=document["app_build"],
        bearer_token=document["bearer_token"],
        diagnostic_run_id=document["diagnostic_run_id"],
        diagnostic_directory=document["diagnostic_directory"],
    )


def create_listener() -> socket.socket:
    """Pre-bind one IPv4 loopback listener on an OS-selected port."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.set_inheritable(False)
        listener.bind((LOOPBACK_HOST, 0))
        listener.listen()
    except OSError:
        listener.close()
        raise
    return listener


def readiness_line(frame: LaunchFrame, port: int) -> str:
    """Render the intentionally public portion of the startup handshake."""

    return json.dumps(
        {"contract": frame.contract, "sidecar_build": SIDECAR_BUILD, "port": port},
        separators=(",", ":"),
        sort_keys=True,
    )


def create_sidecar_app(
    frame: LaunchFrame,
    *,
    config: AppConfig | None = None,
    secret_store: SecretStore | None = None,
    artifact_registry: _ArtifactRegistry | None = None,
    request_runtime_shutdown: Callable[[], None] | None = None,
    record_diagnostic: DiagnosticRecorder | None = None,
) -> FastAPI:
    """Compose the packaged sidecar with only bounded control-plane routes."""

    configuration_failure: StartupConfigurationFailure | None = None
    if config is not None:
        resolved_config = config
    else:
        try:
            resolved_config = AppConfig.load()
        except AncestryError as error:
            resolved_config = _packaged_fallback_config()
            configuration_failure = StartupConfigurationFailure(
                code=(
                    "CONFIG_INVALID"
                    if error.code == "CONFIG_INVALID"
                    else "CONFIGURATION_UNAVAILABLE"
                ),
            )
        except OSError:
            resolved_config = _packaged_fallback_config()
            configuration_failure = StartupConfigurationFailure(
                code="CONFIGURATION_UNAVAILABLE",
            )
    if configuration_failure is not None:
        _record_diagnostic(
            record_diagnostic,
            "CONFIGURATION_DEGRADED",
            DesktopDiagnosticSeverity.WARNING,
            {"blocked": True},
        )
    resolved_secret_store = (
        secret_store
        if secret_store is not None
        else KeyringSecretStore(source_mode=SecretSourceMode.KEYRING_ONLY)
    )

    def startup_report() -> StartupDiagnosticReport:
        return diagnose_startup(
            resolved_config.database_path,
            resolved_secret_store,
            configuration_failure=configuration_failure,
        )

    database = Database(resolved_config.database_path, resolved_secret_store)
    endpoint_validator = EndpointValidationService()
    provider_profiles = ProviderProfileService(
        database,
        endpoint_validator=endpoint_validator,
    )
    provider_registry = ProviderRegistry(resolved_secret_store)
    llm_service = LLMService(
        provider_registry,
        database,
        profiles=provider_profiles,
    )
    resolved_artifact_registry = (
        artifact_registry if artifact_registry is not None else _ArtifactRegistry()
    )
    gedcom_service = GedcomService(
        llm_service,
        consent_lookup=provider_profiles.consent_grant,
        artifacts=resolved_artifact_registry,
    )
    chat_service = ChatService(llm_service, provider_profiles)
    chat_streaming_service = ChatStreamingService(chat_service, llm_service)

    lifecycle = _SidecarLifecycle(
        database=database,
        startup_diagnostics=startup_report,
        chat_service=chat_service,
        llm_service=llm_service,
        gedcom_service=gedcom_service,
        chat_streaming_service=chat_streaming_service,
    )
    return create_app(
        settings=frame.settings(),
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
        settings_service=SettingsService(resolved_config),
        secret_service=SecretManagementService(resolved_secret_store),
        provider_configuration_service=ProviderConfigurationService(
            provider_profiles,
            endpoint_validator,
        ),
        endpoint_validation_service=endpoint_validator,
        chat_service=chat_service,
        chat_streaming_service=chat_streaming_service,
        lifecycle=lifecycle,
        startup_diagnostics=startup_report,
        mutations_allowed=lambda: startup_report().mutations_allowed,
        job_service=lifecycle.jobs,
        gedcom_job_service=(lifecycle.gedcom_jobs if artifact_registry is not None else None),
        job_shutdown=lifecycle.prepare_job_shutdown,
        runtime_shutdown=request_runtime_shutdown,
    )


def _packaged_fallback_config() -> AppConfig:
    """Return non-writing platform defaults for the degraded desktop shell."""

    return AppConfig(
        config_path=user_config_path(APP_NAME) / "config.toml",
        data_dir=user_data_path(APP_NAME),
    )


@contextmanager
def _verification_sidecar_dependencies(
    environment: Mapping[str, str],
) -> Iterator[tuple[AppConfig | None, SecretStore | None]]:
    """Provide isolated state only for the unpublished native verifier."""

    if environment.get(NATIVE_VERIFICATION_EPHEMERAL_WORKSPACE_ENV) != "1":
        yield None, None
        return

    with TemporaryDirectory(prefix="ancestryllm-native-verification-") as directory:
        root = Path(directory)
        yield (
            AppConfig(config_path=root / "config.toml", data_dir=root / "data"),
            MemorySecretStore({}),
        )


async def _serve(frame: LaunchFrame) -> int:
    with _verification_sidecar_dependencies(os.environ) as (config, secret_store):
        return await _serve_with_dependencies(
            frame,
            config=config,
            secret_store=secret_store,
        )


async def _serve_with_dependencies(
    frame: LaunchFrame,
    *,
    config: AppConfig | None,
    secret_store: SecretStore | None,
) -> int:
    recorders = _create_sidecar_diagnostic_recorders(frame)
    recorders.core("PYTHON_CORE_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.INFO)
    recorders.sidecar("SIDECAR_BOOTSTRAP_STARTED", DesktopDiagnosticSeverity.INFO)
    try:
        listener = create_listener()
    except BaseException:
        recorders.sidecar("SIDECAR_TERMINATION_FAILED", DesktopDiagnosticSeverity.ERROR)
        raise
    server: Server

    def request_runtime_shutdown() -> None:
        recorders.sidecar("SIDECAR_SHUTDOWN_REQUESTED", DesktopDiagnosticSeverity.INFO)
        server.should_exit = True

    server = Server(
        create_uvicorn_config(
            create_sidecar_app(
                frame,
                config=config,
                secret_store=secret_store,
                request_runtime_shutdown=request_runtime_shutdown,
                record_diagnostic=recorders.core,
            )
        )
    )
    serve_task = asyncio.create_task(server.serve(sockets=[listener]))
    completed = False
    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
            while not server.started:
                if serve_task.done():
                    await serve_task
                    raise RuntimeError("sidecar stopped before readiness")
                await asyncio.sleep(0.01)
        sys.stdout.write(readiness_line(frame, listener.getsockname()[1]) + "\n")
        sys.stdout.flush()
        recorders.core("PYTHON_CORE_READY", DesktopDiagnosticSeverity.INFO)
        recorders.sidecar("SIDECAR_SERVER_READY", DesktopDiagnosticSeverity.INFO)
        await serve_task
        completed = True
        return 0
    finally:
        recorders.sidecar("SIDECAR_TERMINATION_REQUESTED", DesktopDiagnosticSeverity.INFO)
        if not serve_task.done():
            server.should_exit = True
            try:
                await serve_task
            except BaseException:
                recorders.sidecar(
                    "SIDECAR_TERMINATION_FAILED",
                    DesktopDiagnosticSeverity.ERROR,
                )
                raise
        listener.close()
        recorders.sidecar(
            "SIDECAR_TERMINATION_SUCCEEDED" if completed else "SIDECAR_TERMINATION_FAILED",
            DesktopDiagnosticSeverity.INFO if completed else DesktopDiagnosticSeverity.ERROR,
        )


def _fail() -> NoReturn:
    # Keep diagnostics structural: configuration details and secrets never
    # enter stderr, command arguments, or logs.
    sys.stderr.write("SIDECAR_START_FAILED\n")
    raise SystemExit(1)


def main() -> int:
    """Start the packaged sidecar from its private standard-input frame."""

    global _process_tree_guard
    try:
        _process_tree_guard = acquire_windows_process_tree_guard()
        frame = parse_launch_frame(sys.stdin.buffer)
        return asyncio.run(_serve(frame))
    except (AncestryError, ValueError, OSError, RuntimeError, TimeoutError):
        _fail()


if __name__ == "__main__":  # pragma: no cover - exercised by packaged smoke tests
    raise SystemExit(main())


__all__ = [
    "MAX_LAUNCH_FRAME_BYTES",
    "SIDECAR_BUILD",
    "LaunchFrame",
    "acquire_windows_process_tree_guard",
    "create_listener",
    "create_sidecar_app",
    "main",
    "parse_launch_frame",
    "readiness_line",
]
