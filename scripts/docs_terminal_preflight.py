#!/usr/bin/env python3
"""Fail-closed preflight for the pinned terminal documentation container."""

from __future__ import annotations

import hashlib
import hmac
import json
import locale
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

_ROOT_KEYS = {
    "app_executable",
    "environment",
    "font",
    "locale",
    "schema_version",
    "toolchain",
}
_TOOL_KEYS = {"comparison", "path", "version", "version_arguments"}
_FONT_KEYS = {"family", "path", "sha256"}
_LOCALE_KEYS = {"name", "path", "target"}
_SECRET_NAMES = {
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}


class PreflightError(RuntimeError):
    """Internal stable preflight failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise PreflightError(code)


def _object(value: Any, keys: set[str], *, code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail(code)
    return value


def _load_configuration(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    configuration = _object(
        payload,
        _ROOT_KEYS,
        code="DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID",
    )
    if configuration["schema_version"] != 1:
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    return configuration


def _check_executable(path_value: Any) -> Path:
    if type(path_value) is not str:
        _fail("DOCSHOT_TERMINAL_TOOL_MISSING")
    path = Path(path_value)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or not os.access(path, os.X_OK)
    ):
        _fail("DOCSHOT_TERMINAL_TOOL_MISSING")
    return path


def _check_toolchain(raw_toolchain: Any) -> None:
    if type(raw_toolchain) is not dict or set(raw_toolchain) != {
        "chromium",
        "ffmpeg",
        "ttyd",
        "vhs",
    }:
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    for raw_tool in raw_toolchain.values():
        tool = _object(
            raw_tool,
            _TOOL_KEYS,
            code="DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID",
        )
        path = _check_executable(tool["path"])
        arguments = tool["version_arguments"]
        if (
            type(arguments) is not list
            or not arguments
            or any(type(argument) is not str for argument in arguments)
            or type(tool["version"]) is not str
            or tool["comparison"] not in {"exact", "prefix"}
        ):
            _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
        try:
            # Tool paths and version argv are closed by the validated policy schema.
            completed = subprocess.run(  # noqa: S603
                (str(path), *arguments),
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            _fail("DOCSHOT_TERMINAL_TOOL_VERSION_MISMATCH")
        if completed.returncode != 0:
            _fail("DOCSHOT_TERMINAL_TOOL_VERSION_MISMATCH")
        output = (completed.stdout or completed.stderr).strip()
        expected = tool["version"]
        matches = (
            output == expected if tool["comparison"] == "exact" else output.startswith(expected)
        )
        if not matches:
            _fail("DOCSHOT_TERMINAL_TOOL_VERSION_MISMATCH")


def _check_font(raw_font: Any) -> None:
    font = _object(
        raw_font,
        _FONT_KEYS,
        code="DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID",
    )
    if any(type(font[key]) is not str for key in _FONT_KEYS):
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    path = Path(font["path"])
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            _fail("DOCSHOT_TERMINAL_FONT_MISMATCH")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("DOCSHOT_TERMINAL_FONT_MISMATCH")
    if not hmac.compare_digest(actual, font["sha256"]):
        _fail("DOCSHOT_TERMINAL_FONT_MISMATCH")


def _check_locale(raw_locale: Any) -> None:
    locale_policy = _object(
        raw_locale,
        _LOCALE_KEYS,
        code="DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID",
    )
    if any(type(locale_policy[key]) is not str for key in _LOCALE_KEYS):
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    path = Path(locale_policy["path"])
    target = Path(locale_policy["target"])
    try:
        if (
            not path.is_absolute()
            or not target.is_absolute()
            or not path.is_symlink()
            or path.readlink() != target
            or not target.is_dir()
            or path.resolve(strict=True) != target.resolve(strict=True)
        ):
            _fail("DOCSHOT_TERMINAL_LOCALE_MISMATCH")
        selected = locale.setlocale(locale.LC_ALL, locale_policy["name"])
    except (OSError, locale.Error):
        _fail("DOCSHOT_TERMINAL_LOCALE_MISMATCH")
    if selected != locale_policy["name"]:
        _fail("DOCSHOT_TERMINAL_LOCALE_MISMATCH")


def _check_environment(raw_environment: Any) -> None:
    if (
        type(raw_environment) is not dict
        or not raw_environment
        or any(
            type(name) is not str or type(value) is not str
            for name, value in raw_environment.items()
        )
    ):
        _fail("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID")
    if any(os.getenv(name) != value for name, value in raw_environment.items()):
        _fail("DOCSHOT_TERMINAL_ENVIRONMENT_MISMATCH")
    if any(os.getenv(name) for name in _SECRET_NAMES):
        _fail("DOCSHOT_TERMINAL_SECRET_ENVIRONMENT_PRESENT")


def _check_network() -> None:
    try:
        interfaces = {entry.name for entry in Path("/sys/class/net").iterdir()}
    except OSError:
        _fail("DOCSHOT_TERMINAL_NETWORK_NOT_DENIED")
    if interfaces != {"lo"}:
        _fail("DOCSHOT_TERMINAL_NETWORK_NOT_DENIED")


def run_preflight(configuration: dict[str, Any]) -> None:
    """Validate every executable identity and isolation invariant."""
    _check_executable(configuration["app_executable"])
    _check_toolchain(configuration["toolchain"])
    _check_font(configuration["font"])
    _check_locale(configuration["locale"])
    _check_environment(configuration["environment"])
    _check_network()
    if os.geteuid() == 0:
        _fail("DOCSHOT_TERMINAL_ROOT_USER_FORBIDDEN")


def main(argv: list[str] | None = None) -> int:
    """Run the preflight and emit only a sanitized deterministic receipt."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("DOCSHOT_TERMINAL_PREFLIGHT_CONFIG_INVALID", file=sys.stderr)
        return 2
    try:
        configuration = _load_configuration(Path(arguments[0]))
        run_preflight(configuration)
    except PreflightError as error:
        print(error.code, file=sys.stderr)
        return 2
    print(json.dumps({"network_isolated": True, "status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
