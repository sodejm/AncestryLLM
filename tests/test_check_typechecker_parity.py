"""Tests for the deterministic type-checker parity harness."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from scripts.check_typechecker_parity import (
    TYPEPARITY_CHECKER_FAILED,
    TYPEPARITY_DIAGNOSTIC_MISSED,
    Checker,
    Fixture,
    evaluate_parity,
)


def test_evaluate_parity_accepts_diagnostics_on_the_expected_line() -> None:
    fixture = Fixture("tests/fixtures/typecheck/example.py", 7)
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="tests/fixtures/typecheck/example.py:7: error: incompatible type\n",
            stderr="",
        )

    failures = evaluate_parity(
        checkers=(Checker("mypy", ("mypy",)), Checker("ty", ("ty", "check"))),
        fixtures=(fixture,),
        runner=runner,
    )

    assert failures == ()
    assert commands == [
        ("mypy", fixture.relative_path),
        ("ty", "check", fixture.relative_path),
    ]


def test_evaluate_parity_reports_a_missed_or_wrong_line_diagnostic() -> None:
    fixture = Fixture("tests/fixtures/typecheck/example.py", 7)
    results = iter(
        (
            subprocess.CompletedProcess(["mypy"], 0, stdout="Success", stderr=""),
            subprocess.CompletedProcess(
                ["ty"],
                1,
                stdout="tests/fixtures/typecheck/example.py:8: error: unrelated\n",
                stderr="",
            ),
        )
    )

    failures = evaluate_parity(
        checkers=(Checker("mypy", ("mypy",)), Checker("ty", ("ty", "check"))),
        fixtures=(fixture,),
        runner=lambda _command: next(results),
    )

    assert [failure.code for failure in failures] == [
        TYPEPARITY_DIAGNOSTIC_MISSED,
        TYPEPARITY_DIAGNOSTIC_MISSED,
    ]


def test_evaluate_parity_distinguishes_checker_execution_failure() -> None:
    fixture = Fixture("tests/fixtures/typecheck/example.py", 7)

    failures = evaluate_parity(
        checkers=(Checker("ty", ("ty", "check")),),
        fixtures=(fixture,),
        runner=lambda command: subprocess.CompletedProcess(command, 2, stdout="", stderr="bad"),
    )

    assert len(failures) == 1
    assert failures[0].code == TYPEPARITY_CHECKER_FAILED
    assert failures[0].returncode == 2
