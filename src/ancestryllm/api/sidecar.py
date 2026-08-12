"""Packaged private control-sidecar bootstrap.

The Electron main process supplies the launch secret through one bounded stdin
frame.  No launch secret is accepted through command-line arguments or the
environment, and the public readiness frame deliberately contains no secret.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import socket
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO, NoReturn

from platformdirs import user_config_path, user_data_path
from uvicorn import Server

from ancestryllm.api.app import create_app
from ancestryllm.api.contracts import API_CONTRACT
from ancestryllm.api.server import LOOPBACK_HOST, create_uvicorn_config
from ancestryllm.api.settings import ApiSettings
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.application.secret_management import SecretManagementService
from ancestryllm.application.settings import SettingsService
from ancestryllm.core.config import APP_NAME, AppConfig
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.secrets import KeyringSecretStore, SecretSourceMode
from ancestryllm.storage.diagnostics import StartupConfigurationFailure, diagnose_startup

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import FastAPI

    from ancestryllm.core.commands import ModuleDescriptor
    from ancestryllm.core.secrets import SecretStore
    from ancestryllm.storage.diagnostics import StartupDiagnosticReport

SIDECAR_BUILD = "0.5.0"
MAX_LAUNCH_FRAME_BYTES = 4096
STARTUP_TIMEOUT_SECONDS = 10.0
WINDOWS_KILL_ON_JOB_CLOSE = 0x00002000
WINDOWS_EXTENDED_LIMIT_INFORMATION = 9

_process_tree_guard: object | None = None


@dataclass(frozen=True, slots=True)
class LaunchFrame:
    """One private, exact-build launch request from Electron main."""

    contract: str
    app_build: str
    bearer_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.contract != API_CONTRACT:
            raise ValueError("unsupported sidecar contract")
        if self.app_build != SIDECAR_BUILD:
            raise ValueError("sidecar build does not match the application build")
        # Reuse the API boundary's validation rather than creating a second
        # token/build/host policy here.
        self.settings()

    def settings(self) -> ApiSettings:
        return ApiSettings(
            bearer_token=self.bearer_token,
            app_build=self.app_build,
            sidecar_build=SIDECAR_BUILD,
            provider_id="none",
        )


class _EmptyRegistry:
    """Expose no domain modules until separately owned routes are implemented."""

    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


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
    }:
        raise ValueError("invalid sidecar launch frame fields")
    if not all(isinstance(value, str) for value in document.values()):
        raise ValueError("invalid sidecar launch frame values")
    return LaunchFrame(
        contract=document["contract"],
        app_build=document["app_build"],
        bearer_token=document["bearer_token"],
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
) -> FastAPI:
    """Compose the packaged control sidecar without any domain modules or routes."""

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

    return create_app(
        settings=frame.settings(),
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
        settings_service=SettingsService(resolved_config),
        secret_service=SecretManagementService(resolved_secret_store),
        startup_diagnostics=startup_report,
        mutations_allowed=lambda: startup_report().mutations_allowed,
    )


def _packaged_fallback_config() -> AppConfig:
    """Return non-writing platform defaults for the degraded desktop shell."""

    return AppConfig(
        config_path=user_config_path(APP_NAME) / "config.toml",
        data_dir=user_data_path(APP_NAME),
    )


async def _serve(frame: LaunchFrame) -> int:
    listener = create_listener()
    server = Server(create_uvicorn_config(create_sidecar_app(frame)))
    serve_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
            while not server.started:
                if serve_task.done():
                    await serve_task
                    raise RuntimeError("sidecar stopped before readiness")
                await asyncio.sleep(0.01)
        sys.stdout.write(readiness_line(frame, listener.getsockname()[1]) + "\n")
        sys.stdout.flush()
        await serve_task
        return 0
    finally:
        if not serve_task.done():
            server.should_exit = True
            await serve_task
        listener.close()


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
    except (ValueError, OSError, RuntimeError, TimeoutError):
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
