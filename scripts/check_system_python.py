#!/usr/bin/env python3
"""Fail closed unless uv can use a supported system-supplied Python."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

MINIMUM_PYTHON = (3, 12)
MAXIMUM_PYTHON = (3, 15)


class SystemPythonError(RuntimeError):
    """A stable, user-facing system Python preflight failure."""


def validate_python_version(version: Sequence[int]) -> None:
    """Require a Python version inside the repository's supported range."""

    if len(version) < 2:
        raise SystemPythonError(
            "UVENV_PYTHON_VERSION_UNSUPPORTED: could not determine the system Python version"
        )

    current = (version[0], version[1])
    if not MINIMUM_PYTHON <= current < MAXIMUM_PYTHON:
        current_text = ".".join(str(part) for part in version[:3])
        raise SystemPythonError(
            "UVENV_PYTHON_VERSION_UNSUPPORTED: expected system Python 3.12-3.14; "
            f"found {current_text}"
        )


def main() -> int:
    """Validate the running interpreter and emit only stable failure details."""

    try:
        validate_python_version(sys.version_info)
    except SystemPythonError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
