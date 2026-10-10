"""Fictional-input regressions for the isolated inspection resource boundary."""

import json
from pathlib import Path

import pytest

from ancestryllm.domain.errors import DomainFailure
from ancestryllm.gedcom.inspection_worker import (
    InspectionBudgets,
    _monitor_process,
    _read_failure,
    _sanitized_environment,
    _validate_private_outputs,
    preflight_source,
)


class _Process:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = iter(polls)

    def poll(self) -> int | None:
        return next(self._polls)


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
