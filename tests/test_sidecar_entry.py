"""Verify native entry routing without starting an API server or inspection process."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("arguments", "expected_exit", "expected_calls"),
    [
        (["--inspection-worker"], 23, ["worker"]),
        (["--inspection-worker", "unexpected"], 64, []),
        ([], 17, ["sidecar"]),
    ],
)
def test_native_entry_routes_only_the_selected_private_protocol(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected_exit: int,
    expected_calls: list[str],
) -> None:
    calls: list[str] = []

    def worker() -> int:
        calls.append("worker")
        return 23

    def sidecar() -> int:
        calls.append("sidecar")
        return 17

    monkeypatch.setattr("ancestryllm.gedcom.inspection_worker._child_main", worker)
    monkeypatch.setattr("ancestryllm.api.sidecar.main", sidecar)
    entry = Path(__file__).resolve().parents[1] / "scripts" / "sidecar_entry.py"
    monkeypatch.setattr(sys, "argv", [str(entry), *arguments])

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(entry), run_name="__main__")

    assert caught.value.code == expected_exit
    assert calls == expected_calls
