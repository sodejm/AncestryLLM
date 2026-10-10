"""Fictional-input regressions for the isolated inspection resource boundary."""

import json
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.operations import GedcomInspectRequest, GedcomInspectResult
from ancestryllm.core.ingress import FileIngressLimits, FileIngressPolicy
from ancestryllm.domain.errors import DomainFailure
from ancestryllm.gedcom.inspection_worker import (
    InspectionBudgets,
    _cleanup_orphans,
    _control_payload,
    _monitor_process,
    _read_failure,
    _read_rss,
    _sanitized_environment,
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
            raise subprocess.TimeoutExpired("worker", timeout)
        return -9

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")


class _Cancellation:
    def check_cancelled(self) -> None:
        return


def test_preflight_accepts_exact_line_and_xref_budgets(tmp_path: Path) -> None:
    source = tmp_path / "fictional.ged"
    source.write_text("0 @I1@ INDI\n1 FAMC @F1@\n0 TRLR\n")
    preflight_source(source, InspectionBudgets(max_physical_lines=3, max_xrefs=2))


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


def test_private_output_validation_rejects_oversize_and_nonprivate_files(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "lease"
    directory.mkdir(mode=0o700)
    result = directory / "result.json"
    result.write_bytes(b"{}")
    result.chmod(0o644)
    with pytest.raises(DomainFailure) as caught:
        _validate_private_outputs(directory, InspectionBudgets())
    assert caught.value.code.value == "WORKER_FAILED"

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
    for directory, pid in ((dead, 1001), (live, 1002), (unrelated, 1003)):
        directory.mkdir(mode=0o700)
        owner = directory / "owner.json"
        owner.write_text(json.dumps({"pid": pid}), encoding="ascii")
        owner.chmod(0o600)

    monkeypatch.setattr(
        "ancestryllm.gedcom.inspection_worker._pid_is_live", lambda pid: pid == 1002
    )
    _cleanup_orphans(root)

    assert not dead.exists()
    assert live.exists()
    assert unrelated.exists()


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
