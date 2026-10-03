#!/usr/bin/env python3
"""Exercise one documentation scenario through a true Linux pseudo-terminal."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path
from typing import Any, NoReturn

_ROOT_KEYS = {
    "columns",
    "interactions",
    "launch",
    "ready_signal",
    "rows",
    "schema_version",
    "timeout_seconds",
}
_INTERACTION_KEYS = {"expect", "input", "wait_after"}
_ANSI = re.compile(rb"(?:\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_])")


class PtyError(RuntimeError):
    """Stable true-PTY validation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise PtyError(code)


def _load_scenario(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
    if type(payload) is not dict or set(payload) != _ROOT_KEYS:
        _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
    if (
        payload["schema_version"] != 1
        or type(payload["columns"]) is not int
        or type(payload["rows"]) is not int
        or type(payload["timeout_seconds"]) is not int
        or type(payload["launch"]) is not list
        or not payload["launch"]
        or any(type(token) is not str or not token for token in payload["launch"])
        or type(payload["ready_signal"]) is not str
        or not payload["ready_signal"]
        or type(payload["interactions"]) is not list
    ):
        _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
    for interaction in payload["interactions"]:
        if type(interaction) is not dict or not set(interaction).issubset(_INTERACTION_KEYS):
            _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
        if set(interaction) not in ({"input", "wait_after"}, _INTERACTION_KEYS):
            _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
        if type(interaction["input"]) is not str or type(interaction["wait_after"]) is not str:
            _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
        if "expect" in interaction and type(interaction["expect"]) is not str:
            _fail("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID")
    return payload


def _read_until(
    descriptor: int,
    output: bytearray,
    expected: bytes | None,
    *,
    search_start: int = 0,
    deadline: float,
    child_pid: int,
) -> tuple[bool, int | None]:
    while time.monotonic() < deadline:
        readable, _, _ = select.select((descriptor,), (), (), 0.1)
        if readable:
            try:
                chunk = os.read(descriptor, 65536)
            except OSError:
                chunk = b""
            if chunk:
                output.extend(chunk)
                if expected is not None and expected in output[search_start:]:
                    return True, None
        finished, status = os.waitpid(child_pid, os.WNOHANG)
        if finished:
            return expected is None or expected in output[search_start:], status
    return False, None


def _terminate(child_pid: int) -> None:
    try:
        os.kill(child_pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        finished, _ = os.waitpid(child_pid, os.WNOHANG)
        if finished:
            return
        time.sleep(0.05)
    try:
        os.kill(child_pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    os.waitpid(child_pid, 0)


def _exit_code(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 2


def _sanitized(output: bytes) -> str:
    cleaned = _ANSI.sub(b"", output).replace(b"\r", b"\n")
    return cleaned.decode("utf-8", errors="replace").replace("\x00", "")


def exercise_scenario(scenario: dict[str, Any]) -> tuple[int, str]:
    """Execute the scenario without a shell and return its actual child status."""
    child_pid, descriptor = pty.fork()
    if child_pid == 0:
        os.chdir("/workspace")
        # The closed scenario schema permits only the reviewed repository executable.
        os.execv(scenario["launch"][0], scenario["launch"])  # noqa: S606
        raise AssertionError("unreachable")

    dimensions = struct.pack("HHHH", scenario["rows"], scenario["columns"], 0, 0)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, dimensions)
    output = bytearray()
    deadline = time.monotonic() + scenario["timeout_seconds"]
    child_status: int | None = None
    try:
        ready, child_status = _read_until(
            descriptor,
            output,
            scenario["ready_signal"].encode(),
            deadline=deadline,
            child_pid=child_pid,
        )
        if not ready:
            _fail("DOCSHOT_TERMINAL_READY_MISSING")
        for interaction in scenario["interactions"]:
            if child_status is not None:
                _fail("DOCSHOT_TERMINAL_COMMAND_FAILED")
            start = len(output)
            os.write(descriptor, interaction["input"].encode() + b"\n")
            expected = interaction.get("expect")
            if expected is not None:
                matched, child_status = _read_until(
                    descriptor,
                    output,
                    expected.encode(),
                    search_start=start,
                    deadline=deadline,
                    child_pid=child_pid,
                )
                if not matched or expected.encode() not in output[start:]:
                    _fail("DOCSHOT_TERMINAL_INTERACTION_MISMATCH")
        if child_status is None:
            _, child_status = _read_until(
                descriptor,
                output,
                None,
                deadline=deadline,
                child_pid=child_pid,
            )
        if child_status is None:
            _fail("DOCSHOT_TERMINAL_TIMEOUT")
        return _exit_code(child_status), _sanitized(bytes(output))
    except BaseException:
        if child_status is None:
            _terminate(child_pid)
        raise
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Exercise one scenario and preserve its real application exit status."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("DOCSHOT_TERMINAL_PTY_SCENARIO_INVALID", file=sys.stderr)
        return 2
    try:
        scenario = _load_scenario(Path(arguments[0]))
        exit_code, transcript = exercise_scenario(scenario)
    except PtyError as error:
        print(error.code, file=sys.stderr)
        return 2
    sys.stdout.write(transcript)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
