"""Fictional-input regressions for the isolated inspection resource boundary."""

import json
import math
import platform
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.operations import GedcomInspectRequest, GedcomInspectResult
from ancestryllm.core.ingress import FileIngressLimits, FileIngressPolicy
from ancestryllm.domain.errors import DomainFailure
from ancestryllm.gedcom import _private_files
from ancestryllm.gedcom.inspection_worker import (
    InspectionBudgets,
    WorkerInspection,
    _cleanup_orphans,
    _control_payload,
    _monitor_process,
    _process_identity,
    _ProcessIdentity,
    _read_failure,
    _read_rss,
    _sanitized_environment,
    _scratch_root,
    _stop_process,
    _validate_private_outputs,
    preflight_source,
    run_inspection,
)


class _Process:
    pid = 123

    def __init__(self, polls: list[int | None]) -> None:
        self._polls = iter(polls)

    def poll(self) -> int | None:
        return next(self._polls)

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return


class _StubbornProcess:
    pid = 456

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._waits = 0

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"wait:{timeout}")
        self._waits += 1
        if self._waits == 1:
            raise subprocess.TimeoutExpired("worker", timeout if timeout is not None else 0.0)
        return -9

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")


class _Cancellation:
    def check_cancelled(self) -> None:
        return


class _CompletedProcess:
    pid = 789

    def __init__(self) -> None:
        import io

        self.stdin = io.BytesIO()

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return

    def kill(self) -> None:
        return


def test_preflight_accepts_exact_line_and_xref_budgets(tmp_path: Path) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 @I1@ INDI\n1 FAMC @F1@\n0 TRLR\n")
    preflight_source(source, InspectionBudgets(max_physical_lines=3, max_xrefs=2))


def test_preflight_enforces_the_shared_wall_deadline(tmp_path: Path) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n0 TRLR\n")

    with pytest.raises(DomainFailure) as caught:
        preflight_source(
            source,
            InspectionBudgets(),
            deadline=1.0,
            clock=iter((0.5, 1.0)).__next__,
        )

    assert caught.value.code.value == "WORKER_TIMEOUT"


@pytest.mark.parametrize(
    "budgets,content,budget",
    [
        (InspectionBudgets(max_physical_lines=2), "0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", "lines"),
        (InspectionBudgets(max_xrefs=1), "0 @I1@ INDI\n1 FAMC @F1@\n", "xrefs"),
    ],
)
def test_preflight_rejects_one_over_without_changing_source(
    tmp_path: Path, budgets: InspectionBudgets, content: str, budget: str
) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text(content)
    before = source.read_bytes()
    with pytest.raises(DomainFailure) as caught:
        preflight_source(source, budgets)
    assert caught.value.code.value == "WORKER_LIMIT"
    assert caught.value.details[0].value == budget
    assert source.read_bytes() == before
    assert str(source) not in str(caught.value)


def test_worker_budgets_reject_unbounded_or_invalid_values() -> None:
    with pytest.raises(ValueError):
        InspectionBudgets(max_xrefs=0)
    with pytest.raises(ValueError):
        InspectionBudgets(wall_seconds=float("inf"))


def test_worker_inspection_preserves_the_absolute_deadline(tmp_path: Path) -> None:
    inspection = WorkerInspection(
        result=cast("GedcomInspectResult", object()),
        index_path=tmp_path / "roots.sqlite3",
        directory=tmp_path,
        deadline=2.0,
    )

    with pytest.raises(DomainFailure) as caught:
        inspection.check_deadline(_Cancellation(), clock=lambda: 2.0)

    assert caught.value.code.value == "WORKER_TIMEOUT"

    fixture = WorkerInspection(
        result=cast("GedcomInspectResult", object()),
        index_path=tmp_path / "roots.sqlite3",
        directory=tmp_path,
    )
    assert fixture.deadline == math.inf


def test_child_environment_excludes_provider_credentials_and_dotenv_inputs() -> None:
    environment = _sanitized_environment(
        {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "secret",
            "HOME": "/contains/dotenv",
            "PYTHONPATH": "/untrusted",
        },
        source_root=Path("/private/source"),
    )
    assert environment == {
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/private/source",
        "PYTHONUTF8": "1",
    }


@pytest.mark.parametrize(
    "polls,rss,clock,code,budget",
    [
        ([7], 1, [0.0], "WORKER_FAILED", None),
        ([None], 101, [0.0], "WORKER_LIMIT", "memory"),
        ([None], 1, [2.0], "WORKER_TIMEOUT", None),
    ],
)
def test_monitor_classifies_crash_memory_and_timeout_without_process_text(
    polls: list[int | None],
    rss: int,
    clock: list[float],
    code: str,
    budget: str | None,
) -> None:
    process = _Process(polls)
    with pytest.raises(DomainFailure) as caught:
        _monitor_process(
            process,
            InspectionBudgets(memory_bytes=100),
            _Cancellation(),
            deadline=1.0,
            rss_reader=lambda _process: rss,
            clock=lambda: clock.pop(0),
            sleeper=lambda _seconds: None,
        )
    assert caught.value.code.value == code
    assert (caught.value.details[0].value if caught.value.details else None) == budget
    assert str(caught.value) == code


def test_monitor_accepts_successful_exit_after_rss_lookup_race() -> None:
    process = _Process([None, 0])

    _monitor_process(
        process,
        InspectionBudgets(),
        _Cancellation(),
        deadline=1.0,
        rss_reader=lambda _process: None,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )


def test_private_output_validation_rejects_oversize_and_nonprivate_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "lease"
    directory.mkdir(mode=0o700)
    result = directory / "result.json"
    result.write_bytes(b"{}")
    if platform.system() == "Windows":
        monkeypatch.setattr(
            _private_files,
            "_windows_path_is_private",
            lambda path, *, directory: path != result,
        )
    else:
        result.chmod(0o644)
    with pytest.raises(DomainFailure) as caught:
        _validate_private_outputs(directory, InspectionBudgets())
    assert caught.value.code.value == "WORKER_FAILED"

    if platform.system() == "Windows":
        monkeypatch.setattr(
            _private_files,
            "_windows_path_is_private",
            lambda _path, *, directory: True,
        )
    else:
        result.chmod(0o600)
    result.write_bytes(b"x" * 9)
    with pytest.raises(DomainFailure) as caught:
        _validate_private_outputs(directory, InspectionBudgets(summary_bytes=8))
    assert caught.value.code.value == "WORKER_LIMIT"
    assert caught.value.details[0].value == "summary"


def test_failure_channel_rejects_structured_or_path_bearing_details(
    tmp_path: Path,
) -> None:
    failure_path = tmp_path / "failure.json"
    for value in ({"nested": "payload"}, "/private/source.ged"):
        failure_path.write_text(
            json.dumps(
                {
                    "code": "WORKER_LIMIT",
                    "details": [{"name": "budget", "value": value}],
                }
            )
        )
        with pytest.raises(DomainFailure) as caught:
            _read_failure(failure_path)
        assert caught.value.code.value == "WORKER_FAILED"
        assert caught.value.details == ()


def test_stop_process_escalates_and_reaps_a_stubborn_child() -> None:
    process = _StubbornProcess()
    _stop_process(process)
    assert process.calls == ["terminate", "wait:1", "kill", "wait:1"]


def test_restart_cleanup_removes_only_dead_owned_private_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ancestryllm-inspection"
    root.mkdir(mode=0o700)
    dead = root / "ancestryllm-inspect-dead"
    live = root / "ancestryllm-inspect-live"
    unrelated = root / "other-directory"
    reused = root / "ancestryllm-inspect-reused"
    for directory, pid, start in (
        (dead, 1001, "dead-start"),
        (live, 1002, "live-start"),
        (reused, 1003, "old-start"),
        (unrelated, 1004, "other-start"),
    ):
        directory.mkdir(mode=0o700)
        owner = directory / "owner.json"
        owner.write_text(json.dumps({"pid": pid, "start": start}), encoding="ascii")
        owner.chmod(0o600)

    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._process_identity",
        lambda pid: {
            1001: _ProcessIdentity(running=False, start_token=None),
            1002: _ProcessIdentity(running=True, start_token="live-start"),
            1003: _ProcessIdentity(running=True, start_token="new-start"),
        }.get(pid, _ProcessIdentity(running=None, start_token=None)),
    )
    _cleanup_orphans(root)

    assert not dead.exists()
    assert live.exists()
    assert not reused.exists()
    assert unrelated.exists()


def test_scratch_root_cannot_be_preclaimed_at_a_predictable_user_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._current_user_scope", lambda: "uid-1001"
    )
    preclaimed = tmp_path / "ancestryllm-inspection-uid-1001"
    preclaimed.mkdir()

    root = _scratch_root()

    assert root.parent == tmp_path.resolve()
    assert root != preclaimed
    assert root.name.startswith("ancestryllm-inspection-uid-1001-")
    assert _private_files.is_private_directory(root)


def test_scratch_root_recovers_dead_leases_from_a_prior_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._current_user_scope", lambda: "uid-1001"
    )
    prior = tmp_path / "ancestryllm-inspection-uid-1001-prior"
    prior.mkdir()
    assert _private_files.harden_private_directory(prior)
    dead = prior / "ancestryllm-inspect-dead"
    dead.mkdir()
    assert _private_files.harden_private_directory(dead)
    owner = dead / "owner.json"
    owner.write_text(json.dumps({"pid": 1001, "start": "dead-start"}), encoding="ascii")
    assert _private_files.harden_private_file(owner)
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._process_identity",
        lambda _pid: _ProcessIdentity(running=False, start_token=None),
    )

    root = _scratch_root()

    assert not prior.exists()
    assert root.parent == tmp_path.resolve()
    assert root.name.startswith("ancestryllm-inspection-uid-1001-")
    assert _private_files.is_private_directory(root)


def test_process_identity_uses_windows_process_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _ProcessIdentity(running=True, start_token="windows-start")
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._windows_process_identity",
        lambda pid: expected if pid == 123 else _ProcessIdentity(False, None),
    )
    monkeypatch.setattr(
        "os.kill", lambda *_args: pytest.fail("Windows process checks must not send signals")
    )

    assert _process_identity(123) == expected


def test_control_payload_round_trips_custom_ingress_limits(tmp_path: Path) -> None:
    defaults = FileIngressLimits()
    limits = replace(
        defaults,
        gedcom=replace(defaults.gedcom, max_collection_items=1),
    )
    registry = _ArtifactRegistry()
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    request = GedcomInspectRequest(
        source=registry.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="application/x-gedcom",
            artifact_type="gedcom",
        )
    )

    payload = _control_payload(
        source,
        tmp_path / "private-worker",
        request,
        InspectionBudgets(),
        limits,
    )
    document = json.loads(payload)

    assert document["ingress"]["gedcom"]["max_collection_items"] == 1
    assert FileIngressLimits.from_mapping(document["ingress"]) == limits


def test_real_child_enforces_custom_collection_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "root_candidate_count" not in {item.name for item in fields(GedcomInspectResult)}:
        pytest.skip("requires the integrated worker result contract")
    source = tmp_path / "fictional.ged"
    source.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME Ada /Example/\n"
        "0 @I2@ INDI\n1 NAME Grace /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    before = source.read_bytes()
    real_temp = tmp_path / "real-temp"
    real_temp.mkdir()
    temp_alias = tmp_path / "temp-alias"
    try:
        temp_alias.symlink_to(real_temp, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker.tempfile.gettempdir",
        lambda: str(temp_alias),
    )
    defaults = FileIngressLimits()
    ingress = FileIngressPolicy(
        replace(
            defaults,
            gedcom=replace(defaults.gedcom, max_collection_items=1),
        )
    )
    registry = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=registry.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="application/x-gedcom",
            artifact_type="gedcom",
        )
    )

    with pytest.raises(DomainFailure) as caught:
        run_inspection(source, request, _Cancellation(), ingress=ingress)

    assert caught.value.code.value == "ARTIFACT_TOO_LARGE"
    assert source.read_bytes() == before


def test_run_inspection_enforces_deadline_after_source_reverification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")
    before = source.read_bytes()
    registry = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=registry.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="application/x-gedcom",
            artifact_type="gedcom",
        )
    )
    root = tmp_path / "worker-root"
    root.mkdir(mode=0o700)
    now = [0.0]
    original_verify = FileIngressPolicy.verify

    def delayed_verify(
        policy: FileIngressPolicy,
        path: Path,
        kind: object,
        fingerprint: object,
    ) -> None:
        original_verify(policy, path, kind, fingerprint)  # type: ignore[arg-type]
        now[0] = 2.0

    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker.time.monotonic", lambda: now[0])
    monkeypatch.setattr(FileIngressPolicy, "verify", delayed_verify)
    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker._scratch_root", lambda: root)
    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker._cleanup_orphans", lambda _root: None)
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker.preflight_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._process_identity",
        lambda _pid: _ProcessIdentity(True, "test:start"),
    )
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker.subprocess.Popen",
        lambda *_args, **_kwargs: _CompletedProcess(),
    )
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._monitor_process",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(DomainFailure) as caught:
        run_inspection(
            source,
            request,
            _Cancellation(),
            budgets=InspectionBudgets(wall_seconds=1.0),
        )

    assert caught.value.code.value == "WORKER_TIMEOUT"
    assert source.read_bytes() == before
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("frozen", [False, True])
def test_worker_launch_uses_the_runtime_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen: bool
) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", encoding="utf-8")
    registry = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=registry.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="application/x-gedcom",
            artifact_type="gedcom",
        )
    )
    executable = str(tmp_path / ("native-sidecar" if frozen else "python"))
    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker.sys.frozen", frozen, raising=False)
    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker.sys.executable", executable)
    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._process_identity",
        lambda _pid: _ProcessIdentity(True, "test:start"),
    )
    commands: list[list[str]] = []

    def refuse_launch(command: list[str], **kwargs: object) -> None:
        commands.append(command)
        raise OSError("simulated launch failure")

    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker.subprocess.Popen", refuse_launch)
    with pytest.raises(DomainFailure) as caught:
        run_inspection(source, request, _Cancellation())

    assert caught.value.code.value == "WORKER_FAILED"
    expected = (
        [executable, "--inspection-worker"]
        if frozen
        else [executable, "-m", "ancestryllm.gedcom.inspection_worker", "--child"]
    )
    assert commands == [expected]


def test_windows_rss_api_declares_pointer_safe_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NativeFunction:
        def __init__(self, result: object) -> None:
            self.result = result
            self.argtypes: list[object] | None = None
            self.restype: object | None = None
            self.calls: list[tuple[object, ...]] = []

        def __call__(self, *args: object) -> object:
            self.calls.append(args)
            return self.result

    open_process = _NativeFunction(0x1_0000_0001)
    memory_info = _NativeFunction(False)
    close_handle = _NativeFunction(True)
    windll = SimpleNamespace(
        kernel32=SimpleNamespace(OpenProcess=open_process, CloseHandle=close_handle),
        psapi=SimpleNamespace(GetProcessMemoryInfo=memory_info),
    )
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)

    assert _read_rss(_Process([None])) is None
    assert open_process.restype is wintypes.HANDLE
    assert memory_info.argtypes is not None
    assert memory_info.argtypes[0] is wintypes.HANDLE
    assert close_handle.argtypes == [wintypes.HANDLE]
    assert memory_info.calls[0][0] == 0x1_0000_0001
    assert close_handle.calls == [(0x1_0000_0001,)]
