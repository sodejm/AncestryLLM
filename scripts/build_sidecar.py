#!/usr/bin/env python3
"""Build the native, self-contained desktop control sidecar with PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sysconfig
import tempfile
import tomllib
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "ancestryllm-sidecar"
MANIFEST_NAME = "sidecar-manifest.json"
MANIFEST_SCHEMA = "ancestryllm.sidecar-payload/1"
PYINSTALLER_DATA_PACKAGES = ("rfc3987_syntax",)


def native_target(system: str, machine: str) -> str:
    """Return the Electron platform/architecture label for a supported host."""

    normalized = (system.casefold(), machine.casefold())
    targets = {
        ("darwin", "arm64"): "darwin-arm64",
        ("darwin", "x86_64"): "darwin-x64",
        ("windows", "amd64"): "win32-x64",
        ("windows", "arm64"): "win32-arm64",
        ("linux", "x86_64"): "linux-x64",
    }
    try:
        return targets[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported native sidecar target: {system}/{machine}") from error


def runtime_target(system: str, machine: str, python_platform: str) -> str:
    """Return the package target produced by the current Python runtime."""

    if system.casefold() == "windows":
        windows_targets = {
            "win-amd64": "win32-x64",
            "win-arm64": "win32-arm64",
        }
        try:
            return windows_targets[python_platform.casefold()]
        except KeyError as error:
            raise ValueError(
                f"unsupported Windows Python runtime platform: {python_platform}"
            ) from error
    return native_target(system, machine)


def executable_path(output_root: Path, target: str) -> Path:
    """Return the path Electron expects after electron-builder copies resources."""

    suffix = ".exe" if target.startswith("win32-") else ""
    return output_root / target / EXECUTABLE_NAME / f"{EXECUTABLE_NAME}{suffix}"


def pyinstaller_arguments(
    output_root: Path,
    target: str,
    temporary: Path,
) -> list[str]:
    """Return the reviewed PyInstaller contract for the desktop sidecar."""

    arguments = [
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
    ]
    for package in PYINSTALLER_DATA_PACKAGES:
        arguments.extend(("--collect-data", package))
    arguments.append(str(ROOT / "scripts" / "sidecar_entry.py"))
    return arguments


def project_version() -> str:
    """Return the immutable application build identity bundled with the sidecar."""

    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("pyproject.toml does not define a project version")
    return version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as payload:
        for chunk in iter(lambda: payload.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_payload_manifest(output_root: Path, target: str, app_build: str) -> Path:
    """Write a deterministic manifest covering every packaged sidecar payload entry."""

    target_root = (output_root / target).resolve()
    payload_root = target_root / EXECUTABLE_NAME
    if not payload_root.is_dir():
        raise RuntimeError("native sidecar payload directory is missing")

    entries: list[dict[str, object]] = []
    for path in sorted(payload_root.rglob("*"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(target_root).as_posix()
        metadata = path.lstat()
        if S_ISDIR(metadata.st_mode):
            continue
        if S_ISREG(metadata.st_mode):
            entries.append(
                {
                    "bytes": metadata.st_size,
                    "path": relative_path,
                    "sha256": _sha256(path),
                    "type": "file",
                }
            )
            continue
        if S_ISLNK(metadata.st_mode):
            link_target = path.readlink()
            if link_target.is_absolute() or not path.resolve(strict=False).is_relative_to(
                target_root
            ):
                raise RuntimeError("sidecar payload symlink escapes the sidecar target")
            entries.append(
                {
                    "path": relative_path,
                    "target": link_target.as_posix(),
                    "type": "symlink",
                }
            )
            continue
        raise RuntimeError("sidecar payload contains an unsupported filesystem entry")

    if not entries:
        raise RuntimeError("native sidecar payload is empty")
    manifest = {
        "app_build": app_build,
        "files": entries,
        "schema": MANIFEST_SCHEMA,
        "target": target,
    }
    manifest_path = target_root / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


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
        run_pyinstaller(pyinstaller_arguments(output_root, target, temporary))

    result = executable_path(output_root, target)
    if not result.is_file():
        raise RuntimeError(f"native sidecar build did not create {result}")
    write_payload_manifest(output_root, target, project_version())
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
