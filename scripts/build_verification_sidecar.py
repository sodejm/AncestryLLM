#!/usr/bin/env python3
"""Build the target-native wrong-build sidecar used only by package verification."""

from __future__ import annotations

import argparse
import platform
import sysconfig
import tempfile
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from collections.abc import Callable

_build_sidecar_module = "scripts.build_sidecar" if __package__ else "build_sidecar"
runtime_target: Callable[[str, str, str], str] = import_module(_build_sidecar_module).runtime_target

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "ancestryllm-wrong-build-sidecar"


def executable_path(output_root: Path, target: str) -> Path:
    """Return the standalone verification executable for one native target."""

    suffix = ".exe" if target.startswith("win32-") else ""
    return output_root / target / f"{EXECUTABLE_NAME}{suffix}"


def build(output_root: Path, expected_target: str | None = None) -> Path:
    """Build the current host target and return its verification executable path."""

    target = runtime_target(platform.system(), platform.machine(), sysconfig.get_platform())
    if expected_target is not None and target != expected_target:
        raise RuntimeError(f"native build host is {target}, expected {expected_target}")

    try:
        from PyInstaller.__main__ import run as run_pyinstaller  # type: ignore[import-untyped]
    except ImportError as error:  # pragma: no cover - dependency failure is environment-specific
        raise RuntimeError("PyInstaller is required to build the verification sidecar") from error

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ancestryllm-verification-sidecar-") as work:
        temporary = Path(work)
        run_pyinstaller(
            [
                "--noconfirm",
                "--clean",
                "--onefile",
                "--name",
                EXECUTABLE_NAME,
                "--distpath",
                str(output_root / target),
                "--workpath",
                str(temporary / "work"),
                "--specpath",
                str(temporary / "spec"),
                str(ROOT / "scripts" / "verification_wrong_sidecar.py"),
            ]
        )

    result = executable_path(output_root, target)
    if not result.is_file():
        raise RuntimeError(f"verification sidecar build did not create {result}")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "desktop" / "build" / "verification-sidecar",
    )
    parser.add_argument("--expected-target")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    result = build(arguments.output_root, arguments.expected_target)
    print(result.relative_to(ROOT) if result.is_relative_to(ROOT) else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
