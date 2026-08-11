"""Exercise the deterministic, sanitized hosted-runner timeout proof contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF_RUNNER = ROOT / "scripts/ci_timeout_proof.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROOF_RUNNER), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_timeout_proof_records_are_deterministic_and_sanitized(tmp_path: Path) -> None:
    armed_one = tmp_path / "armed-one.json"
    armed_two = tmp_path / "armed-two.json"
    confirmed = tmp_path / "confirmed.json"

    first = _run("arm", "--output", str(armed_one))
    second = _run("arm", "--output", str(armed_two))
    confirmation = _run(
        "confirm",
        "--armed",
        str(armed_one),
        "--job-result",
        "failure",
        "--output",
        str(confirmed),
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert confirmation.returncode == 0, confirmation.stderr
    assert armed_one.read_bytes() == armed_two.read_bytes()
    assert json.loads(armed_one.read_text(encoding="utf-8")) == {
        "expected_job_result": "failure",
        "fixture": "fictional-ci-timeout-v1",
        "hang_seconds": 300,
        "schema_version": 1,
        "status": "armed",
        "timeout_minutes": 1,
    }
    assert json.loads(confirmed.read_text(encoding="utf-8")) == {
        "exercise_job_result": "failure",
        "fixture": "fictional-ci-timeout-v1",
        "schema_version": 1,
        "status": "confirmed",
        "timeout_minutes": 1,
    }
    combined = armed_one.read_text(encoding="utf-8") + confirmed.read_text(encoding="utf-8")
    assert str(tmp_path) not in combined
    assert "/Users/" not in combined
    assert "secret" not in combined.lower()


def test_timeout_proof_rejects_unknown_fields_and_nonfailure_results(tmp_path: Path) -> None:
    armed = tmp_path / "armed.json"
    output = tmp_path / "confirmed.json"
    assert _run("arm", "--output", str(armed)).returncode == 0

    payload = json.loads(armed.read_text(encoding="utf-8"))
    payload["unexpected"] = "field"
    armed.write_text(json.dumps(payload), encoding="utf-8")
    invalid_schema = _run(
        "confirm",
        "--armed",
        str(armed),
        "--job-result",
        "failure",
        "--output",
        str(output),
    )
    assert invalid_schema.returncode == 2
    assert invalid_schema.stderr == (
        "CI_TIMEOUT_PROOF_SCHEMA_MISMATCH: armed proof record does not match schema v1\n"
    )
    assert str(tmp_path) not in invalid_schema.stderr

    assert _run("arm", "--output", str(armed)).returncode == 0
    unexpected_result = _run(
        "confirm",
        "--armed",
        str(armed),
        "--job-result",
        "success",
        "--output",
        str(output),
    )
    assert unexpected_result.returncode == 2
    assert unexpected_result.stderr == (
        "CI_TIMEOUT_PROOF_UNEXPECTED_RESULT: exercise job did not fail at its timeout\n"
    )
    assert not output.exists()
