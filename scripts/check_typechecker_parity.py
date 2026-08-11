"""Check that mypy and ty detect representative typing defects.

The fixtures are intentionally invalid and live outside the production source tree.
This harness is evaluation evidence for the ty advisory period; it does not replace
the authoritative strict-mypy gate.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPEPARITY_DIAGNOSTIC_MISSED = "TYPEPARITY_DIAGNOSTIC_MISSED"
TYPEPARITY_CHECKER_FAILED = "TYPEPARITY_CHECKER_FAILED"


@dataclass(frozen=True)
class Checker:
    """A type-checker command evaluated by this harness."""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Fixture:
    """An intentionally invalid file and the line its checker must diagnose."""

    relative_path: str
    diagnostic_line: int


@dataclass(frozen=True)
class ParityFailure:
    """A stable, sanitized parity failure."""

    code: str
    checker: str
    fixture: str
    returncode: int


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

CHECKERS = (
    Checker("mypy", ("mypy",)),
    Checker("ty", ("ty", "check")),
)
FIXTURES = (
    Fixture("tests/fixtures/typecheck/language_error.py", 8),
    Fixture("tests/fixtures/typecheck/pydantic_error.py", 15),
)


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - commands come only from fixed CHECKERS
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _contains_expected_diagnostic(output: str, fixture: Fixture) -> bool:
    location = re.compile(rf"{re.escape(fixture.relative_path)}:{fixture.diagnostic_line}(?::|\b)")
    return location.search(output.replace("\\", "/")) is not None


def evaluate_parity(
    *,
    checkers: Sequence[Checker] = CHECKERS,
    fixtures: Sequence[Fixture] = FIXTURES,
    runner: CommandRunner = _run,
) -> tuple[ParityFailure, ...]:
    """Return stable failures for missed diagnostics or checker execution errors."""

    failures: list[ParityFailure] = []
    for checker in checkers:
        for fixture in fixtures:
            completed = runner((*checker.command, fixture.relative_path))
            output = f"{completed.stdout}\n{completed.stderr}"
            if completed.returncode == 1 and _contains_expected_diagnostic(output, fixture):
                continue
            code = (
                TYPEPARITY_DIAGNOSTIC_MISSED
                if completed.returncode in {0, 1}
                else TYPEPARITY_CHECKER_FAILED
            )
            failures.append(
                ParityFailure(
                    code=code,
                    checker=checker.name,
                    fixture=fixture.relative_path,
                    returncode=completed.returncode,
                )
            )
    return tuple(failures)


def main() -> int:
    failures = evaluate_parity()
    if failures:
        for failure in failures:
            print(
                f"{failure.code}: checker={failure.checker} fixture={failure.fixture} "
                f"returncode={failure.returncode}"
            )
        return 1

    print("Type-checker parity fixtures were detected by mypy and ty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
