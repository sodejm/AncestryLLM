"""Verify native entry routing without starting an API server or inspection process."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from ancestryllm.gedcom import _private_files


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


def test_native_entry_exposes_a_path_free_directory_stage(tmp_path: Path) -> None:
    """Keep native worker startup failures observable without process output."""

    expected_directory = tmp_path / "expected"
    expected_directory.mkdir(mode=0o700)
    actual_directory = tmp_path / "actual"
    actual_directory.mkdir(mode=0o700)
    assert _private_files.harden_private_directory(expected_directory)
    assert _private_files.harden_private_directory(actual_directory)
    repository = Path(__file__).resolve().parents[1]
    entry = repository / "scripts" / "sidecar_entry.py"
    control = {
        "source": "opaque",
        "directory": str(expected_directory),
        "request": {},
        "budgets": {},
        "ingress": {},
    }

    completed = subprocess.run(
        [sys.executable, str(entry), "--inspection-worker"],
        cwd=actual_directory,
        env={**os.environ, "PYTHONPATH": str(repository / "src")},
        input=json.dumps(control).encode("utf-8"),
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 66
    assert completed.stdout == b""
    assert completed.stderr == b""
