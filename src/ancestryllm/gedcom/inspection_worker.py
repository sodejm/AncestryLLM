"""Bounded process isolation for offline GEDCOM inspection.

The parent sends one small JSON control document over standard input and accepts
only private, validated files from the child.  Paths and imported genealogy
content never cross the public error or logging boundary.
"""

from __future__ import annotations

import errno
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, fields, replace
from importlib import import_module
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from ancestryllm.core.ingress import FileIngressLimits, FileIngressPolicy, FileKind
from ancestryllm.domain.errors import (
    DomainFailure,
    DomainFailureCode,
    DomainFailureDetail,
    DomainScalar,
)
from ancestryllm.gedcom._private_files import (
    current_user_scope as _current_user_scope,
)
from ancestryllm.gedcom._private_files import (
    harden_private_directory,
    harden_private_file,
    is_private_directory,
    is_private_file,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ancestryllm.application.operations import (
        GedcomInspectRequest,
        GedcomInspectResult,
        RootCandidate,
    )
    from ancestryllm.application.ports import CancellationPort

_XREF = re.compile(r"@[^@\r\n]+@")
_CONTROL_BYTES = 8_192
_FAILURE_BYTES = 4_096
_POLL_SECONDS = 0.05
_ROOT_PREFIX = "ancestryllm-inspection-"
_LEASE_PREFIX = "ancestryllm-inspect-"
_OWNER_FILE = "owner.json"
_RESULT_FILE = "result.json"
_FAILURE_FILE = "failure.json"
_INDEX_FILE = "roots.sqlite3"
_ALLOWED_OUTPUTS = frozenset({_OWNER_FILE, _RESULT_FILE, _FAILURE_FILE, _INDEX_FILE})
_WORKER_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True, slots=True)
class InspectionBudgets:
    """Hard ceilings for one inspection; callers may choose smaller test budgets."""

    max_physical_lines: int = 5_000_000
    max_xrefs: int = 2_000_000
    wall_seconds: float = 120.0
    memory_bytes: int = 1_073_741_824
    index_bytes: int = 268_435_456
    summary_bytes: int = 1_048_576
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        for value in (
            self.max_physical_lines,
            self.max_xrefs,
            self.memory_bytes,
            self.index_bytes,
            self.summary_bytes,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Inspection budgets require positive integers.")
        if not math.isfinite(self.wall_seconds) or self.wall_seconds <= 0:
            raise ValueError("Inspection wall time must be finite and positive.")
        if self.max_concurrency != 2:
            raise ValueError("Inspection concurrency is fixed at two workers.")


@dataclass(frozen=True, slots=True)
class WorkerInspection:
    """Private successful worker result and its parent-owned lease."""

    result: GedcomInspectResult
    index_path: Path
    directory: Path
    deadline: float = math.inf

    def check_deadline(
        self,
        cancellation: CancellationPort,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Enforce the original worker deadline during parent-side admission."""

        _check_deadline(cancellation, self.deadline, clock=clock)


@dataclass(frozen=True, slots=True)
class _ProcessIdentity:
    """Liveness and non-reusable start identity for one OS process."""

    running: bool | None
    start_token: str | None


class _PollableProcess(Protocol):
    """Process surface used by the watchdog and its deterministic tests."""

    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def _limit(budget: str) -> DomainFailure:
    return DomainFailure(DomainFailureCode.WORKER_LIMIT, (DomainFailureDetail("budget", budget),))


def _failed() -> DomainFailure:
    return DomainFailure(DomainFailureCode.WORKER_FAILED)


def _check_deadline(
    cancellation: CancellationPort,
    deadline: float,
    *,
    clock: Callable[[], float] | None = None,
) -> None:
    """Check cancellation first, then the shared absolute wall deadline."""

    cancellation.check_cancelled()
    now = time.monotonic if clock is None else clock
    if now() >= deadline:
        raise DomainFailure(DomainFailureCode.WORKER_TIMEOUT)


def preflight_source(
    source: Path,
    budgets: InspectionBudgets,
    *,
    ingress: FileIngressPolicy | None = None,
    cancellation: CancellationPort | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Count physical lines and xrefs through the immutable bounded reader."""

    policy = ingress or FileIngressPolicy(FileIngressLimits())
    now = time.monotonic if clock is None else clock
    xrefs = 0
    if deadline is not None and now() >= deadline:
        raise DomainFailure(DomainFailureCode.WORKER_TIMEOUT)
    for line_number, line in enumerate(policy.iter_text_lines(source, FileKind.GEDCOM), 1):
        if deadline is not None and now() >= deadline:
            raise DomainFailure(DomainFailureCode.WORKER_TIMEOUT)
        if line_number % 4_096 == 0 and cancellation is not None:
            cancellation.check_cancelled()
        if line_number > budgets.max_physical_lines:
            raise _limit("lines")
        xrefs += sum(1 for _ in _XREF.finditer(line))
        if xrefs > budgets.max_xrefs:
            raise _limit("xrefs")


def _sanitized_environment(environ: Mapping[str, str], *, source_root: Path) -> dict[str, str]:
    """Build a minimal child environment without provider keys or dotenv roots."""

    child: dict[str, str] = {}
    if "LANG" in environ:
        child["LANG"] = environ["LANG"]
    if os.name == "nt":
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if name in environ:
                child[name] = environ[name]
    child["PYTHONPATH"] = str(source_root)
    child["PYTHONUTF8"] = "1"
    return child


def _read_rss(process: _PollableProcess) -> int | None:
    """Return resident bytes without exposing command or process output."""

    operating_system = platform.system()
    if operating_system == "Linux":
        try:
            status = Path(f"/proc/{process.pid}/status").read_text(
                encoding="ascii", errors="strict"
            )
            match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
            return int(match.group(1)) * 1024 if match else None
        except (OSError, ValueError):
            return None
    if operating_system == "Darwin":
        try:
            completed = subprocess.run(  # noqa: S603 - fixed absolute executable
                ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
                env={"PATH": "/usr/bin:/bin"},
            )
            value = completed.stdout.strip()
            return int(value) * 1024 if completed.returncode == 0 and value else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    if operating_system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            windll = cast("Any", ctypes).windll

            class _MemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = windll.kernel32
            psapi = windll.psapi
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_MemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            query = 0x1000
            handle = kernel32.OpenProcess(query, False, process.pid)
            if not handle:
                return None
            try:
                counters = _MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return None
                return int(counters.WorkingSetSize)
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None
    return None


def _monitor_process(
    process: _PollableProcess,
    budgets: InspectionBudgets,
    cancellation: CancellationPort,
    *,
    deadline: float,
    rss_reader: Callable[[_PollableProcess], int | None] = _read_rss,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Watch cancellation, wall time, RSS, and abrupt child termination."""

    while True:
        cancellation.check_cancelled()
        if clock() >= deadline:
            raise DomainFailure(DomainFailureCode.WORKER_TIMEOUT)
        return_code = process.poll()
        if return_code is not None:
            if return_code != 0:
                raise _failed()
            return
        resident = rss_reader(process)
        if resident is None:
            # The process can finish after the preceding poll and before RSS is
            # sampled.  Accept that successful exit instead of misclassifying it.
            return_code = process.poll()
            if return_code == 0:
                return
            raise _failed()
        if resident > budgets.memory_bytes:
            raise _limit("memory")
        sleeper(_POLL_SECONDS)


def _validate_private_outputs(directory: Path, budgets: InspectionBudgets) -> None:
    """Reject unexpected, nonregular, nonprivate, or oversized child outputs."""

    if not is_private_directory(directory):
        raise _failed()
    try:
        children = tuple(directory.iterdir())
    except OSError as exc:
        raise _failed() from exc
    for child in children:
        if child.name not in _ALLOWED_OUTPUTS:
            raise _failed()
        try:
            metadata = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise _failed() from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise _failed()
        if not is_private_file(child):
            raise _failed()
        if child.name == _RESULT_FILE and metadata.st_size > budgets.summary_bytes:
            raise _limit("summary")
        if child.name == _INDEX_FILE and metadata.st_size > budgets.index_bytes:
            raise _limit("index")
        if child.name == _FAILURE_FILE and metadata.st_size > _FAILURE_BYTES:
            raise _failed()


def _write_private(path: Path, payload: bytes, *, maximum: int) -> None:
    if len(payload) > maximum:
        raise _limit("summary")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not harden_private_file(path):
            raise _failed()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _failure_payload(failure: DomainFailure) -> bytes:
    document = {
        "code": failure.code.value,
        "details": [asdict(detail) for detail in failure.details],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_failure(path: Path) -> DomainFailure:
    try:
        payload = path.read_bytes()
        if len(payload) > _FAILURE_BYTES:
            raise ValueError
        document = json.loads(payload)
        if not isinstance(document, dict) or set(document) != {"code", "details"}:
            raise ValueError
        code = DomainFailureCode(document["code"])
        if code not in {
            DomainFailureCode.CANCELLED,
            DomainFailureCode.INVALID_REQUEST,
            DomainFailureCode.ARTIFACT_INVALID,
            DomainFailureCode.ARTIFACT_TOO_LARGE,
            DomainFailureCode.WORKER_LIMIT,
            DomainFailureCode.WORKER_TIMEOUT,
            DomainFailureCode.WORKER_FAILED,
        }:
            raise ValueError
        raw_details = document["details"]
        if not isinstance(raw_details, list) or len(raw_details) > 8:
            raise ValueError
        details: list[DomainFailureDetail] = []
        for item in raw_details:
            if not isinstance(item, dict) or set(item) != {"name", "value"}:
                raise ValueError
            name = item["name"]
            value = item["value"]
            if not isinstance(name, str) or type(value) not in {
                str,
                int,
                float,
                bool,
                type(None),
            }:
                raise ValueError
            details.append(DomainFailureDetail(name, cast("DomainScalar", value)))
        if len(details) != len(raw_details):
            raise ValueError
        return DomainFailure(code, tuple(details))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise _failed() from exc


def _process_identity(pid: int) -> _ProcessIdentity:
    """Read liveness plus an OS start token without sending process signals."""

    if pid <= 0:
        return _ProcessIdentity(False, None)
    operating_system = platform.system()
    if operating_system == "Windows":
        return _windows_process_identity(pid)
    if operating_system == "Darwin":
        return _darwin_process_identity(pid)
    if operating_system == "Linux":
        try:
            stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            fields = stat_line[stat_line.rindex(")") + 2 :].split()
            if len(fields) < 20:
                return _ProcessIdentity(None, None)
            return _ProcessIdentity(fields[0] != "Z", f"linux:{fields[19]}")
        except FileNotFoundError:
            return _ProcessIdentity(False, None)
        except (OSError, UnicodeError, ValueError):
            return _ProcessIdentity(None, None)
    return _ProcessIdentity(None, None)


def _darwin_process_identity(pid: int) -> _ProcessIdentity:
    """Read macOS liveness and start time without a signal or subprocess."""

    try:
        import ctypes

        class _ProcessBsdInfo(ctypes.Structure):
            _fields_ = [
                ("flags", ctypes.c_uint32),
                ("status", ctypes.c_uint32),
                ("xstatus", ctypes.c_uint32),
                ("pid", ctypes.c_uint32),
                ("ppid", ctypes.c_uint32),
                ("uid", ctypes.c_uint32),
                ("gid", ctypes.c_uint32),
                ("ruid", ctypes.c_uint32),
                ("rgid", ctypes.c_uint32),
                ("svuid", ctypes.c_uint32),
                ("svgid", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32),
                ("command", ctypes.c_char * 16),
                ("name", ctypes.c_char * 32),
                ("nfiles", ctypes.c_uint32),
                ("pgid", ctypes.c_uint32),
                ("pjobc", ctypes.c_uint32),
                ("tty_device", ctypes.c_uint32),
                ("tty_pgid", ctypes.c_uint32),
                ("nice", ctypes.c_int32),
                ("start_seconds", ctypes.c_uint64),
                ("start_microseconds", ctypes.c_uint64),
            ]

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        information = _ProcessBsdInfo()
        received = proc_pidinfo(
            pid,
            3,  # PROC_PIDTBSDINFO
            0,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if received != ctypes.sizeof(information):
            return (
                _ProcessIdentity(False, None)
                if ctypes.get_errno() == errno.ESRCH
                else _ProcessIdentity(None, None)
            )
        if information.status == 5:  # SZOMB
            return _ProcessIdentity(False, None)
        token = f"darwin:{information.start_seconds}:{information.start_microseconds}"
        return _ProcessIdentity(True, token)
    except (AttributeError, OSError, TypeError, ValueError):
        return _ProcessIdentity(None, None)


def _windows_process_identity(pid: int) -> _ProcessIdentity:
    """Read Windows process liveness and creation time through a safe handle."""

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = cast("Any", ctypes).windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            return (
                _ProcessIdentity(False, None)
                if kernel32.GetLastError() == 87
                else _ProcessIdentity(None, None)
            )
        try:
            wait = kernel32.WaitForSingleObject(handle, 0)
            if wait == 0:
                return _ProcessIdentity(False, None)
            if wait != 258:
                return _ProcessIdentity(None, None)
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return _ProcessIdentity(None, None)
            token = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return _ProcessIdentity(True, f"windows:{token}")
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return _ProcessIdentity(None, None)


def _cleanup_owned_directory(directory: Path) -> bool:
    """Remove a private worker directory only after its owner is proven stale."""

    owner = directory / _OWNER_FILE
    try:
        document = json.loads(owner.read_text(encoding="ascii"))
        pid = document["pid"]
        start = document["start"]
        if type(pid) is not int or not isinstance(start, str) or not start:
            return False
        identity = _process_identity(pid)
        if identity.running is None:
            return False
        if identity.running and identity.start_token == start:
            return False
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def _cleanup_orphans(root: Path, *, maximum: int = 32) -> bool:
    """Remove bounded stale leases from a legacy worker root."""

    try:
        candidates = tuple(islice(root.iterdir(), maximum))
    except OSError:
        return False
    found_lease = False
    for candidate in candidates:
        if not candidate.name.startswith(_LEASE_PREFIX) or not candidate.is_dir():
            continue
        found_lease = True
        _cleanup_owned_directory(candidate)
    return found_lease


def _cleanup_scratch_roots(base: Path, prefix: str, *, maximum: int = 32) -> None:
    """Recover stale leases only from bounded, current-user-private roots."""

    try:
        candidates = tuple(islice(base.iterdir(), maximum))
    except OSError:
        return
    for candidate in candidates:
        if not candidate.name.startswith(prefix) or not is_private_directory(candidate):
            continue
        if (candidate / _OWNER_FILE).is_file():
            _cleanup_owned_directory(candidate)
            continue
        if _cleanup_orphans(candidate):
            with suppress(OSError):
                candidate.rmdir()


def _scratch_root() -> Path:
    """Create an unguessable private root after bounded restart recovery."""

    # macOS commonly exposes the same temporary directory through both /var and
    # /private/var. Canonicalize before passing the lease path to a child whose
    # cwd is reported canonically, otherwise the child rejects its own directory.
    base = Path(tempfile.gettempdir()).resolve()
    prefix = f"{_ROOT_PREFIX}{_current_user_scope()}-"
    _cleanup_scratch_roots(base, prefix)
    try:
        root = Path(tempfile.mkdtemp(prefix=prefix, dir=base)).resolve()
    except OSError as exc:
        raise _failed() from exc
    try:
        private = harden_private_directory(root)
    except OSError as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise _failed() from exc
    if not private:
        shutil.rmtree(root, ignore_errors=True)
        raise _failed()
    return root


def _acquire_slot(
    cancellation: CancellationPort, deadline: float, *, clock: Callable[[], float]
) -> None:
    while True:
        cancellation.check_cancelled()
        remaining = deadline - clock()
        if remaining <= 0:
            raise DomainFailure(DomainFailureCode.WORKER_TIMEOUT)
        if _WORKER_SLOTS.acquire(timeout=min(_POLL_SECONDS, remaining)):
            return


def _stop_process(process: _PollableProcess) -> None:
    """Terminate, escalate, and reap a child without retaining its output."""

    if process.poll() is not None:
        with suppress(OSError, subprocess.SubprocessError):
            process.wait(timeout=0)
        return
    try:
        process.terminate()
        process.wait(timeout=1)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
        process.wait(timeout=1)
    except (OSError, subprocess.SubprocessError):
        pass


def _control_payload(
    source: Path,
    directory: Path,
    request: GedcomInspectRequest,
    budgets: InspectionBudgets,
    ingress_limits: FileIngressLimits,
) -> bytes:
    document = {
        "source": str(source),
        "directory": str(directory),
        "request": json.loads(request.to_json()),
        "budgets": asdict(budgets),
        "ingress": ingress_limits.to_mapping(),
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > _CONTROL_BYTES:
        raise _failed()
    return payload


def run_inspection(
    source: Path,
    request: GedcomInspectRequest,
    cancellation: CancellationPort,
    *,
    budgets: InspectionBudgets | None = None,
    ingress: FileIngressPolicy | None = None,
) -> WorkerInspection:
    """Execute inspection in a supervised child and return a private index lease."""

    from ancestryllm.application.operations import GedcomInspectResult

    selected = budgets or InspectionBudgets()
    policy = ingress or FileIngressPolicy(FileIngressLimits())
    started = time.monotonic()
    deadline = started + selected.wall_seconds
    _check_deadline(cancellation, deadline)
    source = source.absolute()
    try:
        fingerprint = policy.fingerprint(source, FileKind.GEDCOM)
        _check_deadline(cancellation, deadline)
        preflight_source(
            source,
            selected,
            ingress=policy,
            cancellation=cancellation,
            deadline=deadline,
        )
        _check_deadline(cancellation, deadline)
    except DomainFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - collapse ingress details at the boundary
        from ancestryllm.application.errors import domain_failure_from_exception

        raise domain_failure_from_exception(exc) from None
    _acquire_slot(cancellation, deadline, clock=time.monotonic)
    directory: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    success = False
    try:
        directory = _scratch_root()
        _check_deadline(cancellation, deadline)
        owner_identity = _process_identity(os.getpid())
        if not owner_identity.running or owner_identity.start_token is None:
            raise _failed()
        _write_private(
            directory / _OWNER_FILE,
            json.dumps(
                {"pid": os.getpid(), "start": owner_identity.start_token},
                separators=(",", ":"),
            ).encode("ascii"),
            maximum=_FAILURE_BYTES,
        )
        _check_deadline(cancellation, deadline)
        payload = _control_payload(source, directory, request, selected, policy.limits)
        source_root = Path(__file__).resolve().parents[2]
        command = (
            [sys.executable, "--inspection-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "ancestryllm.gedcom.inspection_worker", "--child"]
        )
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed protocol and current runtime executable
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=_sanitized_environment(os.environ, source_root=source_root),
            )
        except OSError:
            raise _failed() from None
        assert process.stdin is not None
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            raise _failed() from None
        _monitor_process(process, selected, cancellation, deadline=deadline)
        _check_deadline(cancellation, deadline)
        try:
            policy.verify(source, FileKind.GEDCOM, fingerprint)
        except Exception as exc:  # noqa: BLE001 - collapse ingress details at the boundary
            from ancestryllm.application.errors import domain_failure_from_exception

            raise domain_failure_from_exception(exc) from None
        _check_deadline(cancellation, deadline)
        _validate_private_outputs(directory, selected)
        _check_deadline(cancellation, deadline)
        failure_path = directory / _FAILURE_FILE
        if failure_path.exists():
            raise _read_failure(failure_path)
        result_path = directory / _RESULT_FILE
        index_path = directory / _INDEX_FILE
        if not result_path.is_file() or not index_path.is_file():
            raise _failed()
        try:
            result = GedcomInspectResult.from_json(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise _failed() from exc
        _check_deadline(cancellation, deadline)
        success = True
        return WorkerInspection(
            result=result,
            index_path=index_path,
            directory=directory,
            deadline=deadline,
        )
    finally:
        if process is not None:
            _stop_process(process)
        if directory is not None and not success:
            shutil.rmtree(directory, ignore_errors=True)
        _WORKER_SLOTS.release()


def _apply_child_memory_limit(maximum: int) -> None:
    if platform.system() != "Linux":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))


def _child_result(result: GedcomInspectResult, count: int) -> GedcomInspectResult:
    """Remove names from IPC while tolerating the pre-integration DTO shape."""

    available = {item.name for item in fields(result)}
    changes: dict[str, object] = {"root_candidates": ()}
    if "root_candidate_count" in available:
        changes["root_candidate_count"] = count
    if "root_index_ref" in available:
        changes["root_index_ref"] = None
    return replace(result, **cast("Any", changes))


def _build_child_index(
    path: Path,
    candidates: Iterable[RootCandidate],
    *,
    max_bytes: int,
) -> int:
    """Build the child index while exposing only its stable budget classification."""
    module = import_module("ancestryllm.gedcom.root_index")
    build = cast("Callable[..., int]", module.build_root_index)
    limit_error = cast("type[Exception]", module.RootIndexLimitError)
    try:
        return build(path, candidates, max_bytes=max_bytes)
    except Exception as exc:
        if isinstance(exc, limit_error):
            raise _limit("index") from None
        raise


def _child_main() -> int:
    directory: Path | None = None
    try:
        raw = sys.stdin.buffer.read(_CONTROL_BYTES + 1)
        if len(raw) > _CONTROL_BYTES:
            return 64
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {
            "source",
            "directory",
            "request",
            "budgets",
            "ingress",
        }:
            return 64
        source = Path(document["source"])
        directory = Path(document["directory"])
        if Path.cwd() != directory or not is_private_directory(directory):
            return 64
        budgets = InspectionBudgets(**document["budgets"])
        ingress = FileIngressPolicy(FileIngressLimits.from_mapping(document["ingress"]))
        _apply_child_memory_limit(budgets.memory_bytes)

        from ancestryllm.application._artifacts import _ArtifactRegistry
        from ancestryllm.application.errors import domain_failure_from_exception
        from ancestryllm.application.operations import GedcomInspectRequest
        from ancestryllm.gedcom.service import GedcomService

        request = GedcomInspectRequest.from_json(
            json.dumps(document["request"], sort_keys=True, separators=(",", ":"))
        )
        registry = _ArtifactRegistry()
        source_grant = registry.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="application/x-gedcom",
            artifact_type="gedcom",
        )
        local_request = replace(request, source=source_grant)
        service = GedcomService(ingress=ingress, artifacts=registry)
        execute = getattr(service, "_execute_inspect_locally", None)
        if execute is None:
            raise RuntimeError("local inspection seam unavailable")
        result = execute(local_request, cancellation=None)
        root_candidates = result.root_candidates
        count = _build_child_index(
            directory / _INDEX_FILE,
            root_candidates,
            max_bytes=budgets.index_bytes,
        )
        bounded = _child_result(result, count)
        payload = bounded.to_json().encode("utf-8")
        _write_private(directory / _RESULT_FILE, payload, maximum=budgets.summary_bytes)
        return 0
    except MemoryError:
        failure = _limit("memory")
    except DomainFailure as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - strict child boundary must collapse failures
        try:
            from ancestryllm.application.errors import domain_failure_from_exception

            failure = domain_failure_from_exception(exc)
        except Exception:  # noqa: BLE001 - mapping failure cannot escape child boundary
            failure = _failed()
        if failure.code is DomainFailureCode.INTERNAL:
            failure = _failed()
    if directory is None:
        return 70
    try:
        _write_private(directory / _FAILURE_FILE, _failure_payload(failure), maximum=_FAILURE_BYTES)
        return 0
    except Exception:  # noqa: BLE001 - a broken failure channel requires abrupt failure
        return 70


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the private stdin worker protocol, rejecting other arguments."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    return _child_main() if arguments == ["--child"] else 64


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["InspectionBudgets", "WorkerInspection", "main", "preflight_source", "run_inspection"]
