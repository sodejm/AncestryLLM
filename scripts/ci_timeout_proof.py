"""Create sanitized, deterministic evidence for the hosted CI timeout proof."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

ARMED_RECORD: dict[str, object] = {
    "expected_job_result": "failure",
    "fixture": "fictional-ci-timeout-v1",
    "hang_seconds": 300,
    "schema_version": 1,
    "status": "armed",
    "timeout_minutes": 1,
}


def _fail(code: str, message: str) -> NoReturn:
    print(f"{code}: {message}", file=sys.stderr)
    raise SystemExit(2)


def _write_record(path: Path, record: dict[str, object]) -> None:
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        path.write_text(serialized, encoding="utf-8")
    except OSError:
        _fail("CI_TIMEOUT_PROOF_WRITE_FAILED", "could not write proof record")


def _read_armed_record(path: Path) -> dict[str, object]:
    try:
        record: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _fail(
            "CI_TIMEOUT_PROOF_SCHEMA_MISMATCH",
            "armed proof record does not match schema v1",
        )
    if record != ARMED_RECORD:
        _fail(
            "CI_TIMEOUT_PROOF_SCHEMA_MISMATCH",
            "armed proof record does not match schema v1",
        )
    return ARMED_RECORD


def _arm(output: Path) -> None:
    _write_record(output, ARMED_RECORD)


def _confirm(armed: Path, job_result: str, output: Path) -> None:
    record = _read_armed_record(armed)
    if job_result != record["expected_job_result"]:
        _fail(
            "CI_TIMEOUT_PROOF_UNEXPECTED_RESULT",
            "exercise job did not fail at its timeout",
        )
    _write_record(
        output,
        {
            "exercise_job_result": job_result,
            "fixture": record["fixture"],
            "schema_version": record["schema_version"],
            "status": "confirmed",
            "timeout_minutes": record["timeout_minutes"],
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    arm = commands.add_parser("arm", help="write the deterministic armed record")
    arm.add_argument("--output", type=Path, required=True)

    confirm = commands.add_parser(
        "confirm",
        help="confirm that the hosted exercise failed at its job timeout",
    )
    confirm.add_argument("--armed", type=Path, required=True)
    confirm.add_argument("--job-result", required=True)
    confirm.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "arm":
        _arm(arguments.output)
        return
    _confirm(arguments.armed, arguments.job_result, arguments.output)


if __name__ == "__main__":
    main()
