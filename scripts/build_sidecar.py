#!/usr/bin/env python3
"""Build the native, self-contained desktop control sidecar with PyInstaller."""

from __future__ import annotations

import argparse
import platform
import sysconfig
import tempfile
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "ancestryllm-sidecar"


def native_target(system: str, machine: str) -> str:
    """Return the Electron platform/architecture label for a supported host."""

    normalized = (system.casefold(), machine.casefold())
    targets = {
        ("darwin", "arm64"): "darwin-arm64",
        ("darwin", "x86_64"): "darwin-x64",
        ("windows", "amd64"): "win32-x64",
        ("linux", "x86_64"): "linux-x64",
    }
    try:
        return targets[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported native sidecar target: {system}/{machine}") from error


def runtime_target(system: str, machine: str, python_platform: str) -> str:
    """Return the native target, allowing x64 Python on a Windows ARM64 host."""

    if (
        system.casefold() == "windows"
        and machine.casefold() == "arm64"
        and python_platform.casefold() == "win-amd64"
    ):
        return "win32-x64"
    return native_target(system, machine)


def executable_path(output_root: Path, target: str) -> Path:
    """Return the path Electron expects after electron-builder copies resources."""

    suffix = ".exe" if target == "win32-x64" else ""
    return output_root / target / EXECUTABLE_NAME / f"{EXECUTABLE_NAME}{suffix}"


def build(output_root: Path, expected_target: str | None = None) -> Path:
    """Build the current host target and return its native executable path."""

    target = runtime_target(platform.system(), platform.machine(), sysconfig.get_platform())
    if expected_target is not None and target != expected_target:
        raise RuntimeError(f"native build host is {target}, expected {expected_target}")

    try:
        from PyInstaller.__main__ import run as run_pyinstaller  # type: ignore[import-untyped]
    except ImportError as error:  # pragma: no cover - dependency failure is environment-specific
        raise RuntimeError("PyInstaller is required to build the desktop sidecar") from error

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ancestryllm-pyinstaller-") as work:
        temporary = Path(work)
        run_pyinstaller(
            [
                "--noconfirm",
                "--clean",
                "--onedir",
                "--name",
                EXECUTABLE_NAME,
                "--distpath",
                str(output_root / target),
                "--workpath",
                str(temporary / "work"),
                "--specpath",
                str(temporary / "spec"),
                "--paths",
                str(ROOT / "src"),
                str(ROOT / "scripts" / "sidecar_entry.py"),
            ]
        )

    result = executable_path(output_root, target)
    if not result.is_file():
        raise RuntimeError(f"native sidecar build did not create {result}")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "desktop" / "build" / "sidecar",
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
