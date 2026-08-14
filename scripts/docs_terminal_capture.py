#!/usr/bin/env python3
"""Capture deterministic documentation PNGs from the real terminal surfaces."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Never, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

if __package__:
    from scripts.docs_screenshot_manifest import (
        ScreenshotManifestError,
        ValidatedManifest,
        load_manifest,
        validate_capture_text,
    )
else:
    from docs_screenshot_manifest import (
        ScreenshotManifestError,
        ValidatedManifest,
        load_manifest,
        validate_capture_text,
    )

SCHEMA_VERSION = 1
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_POLICY_SCHEMA = "config/docs-terminal-capture-policy-v1.schema.json"
_URL_MARKERS = ("://",)
_SHELL_MARKERS = ("$(", "`", "\n", "\r", "\x00")
_DOCKER_TIMEOUT_SECONDS = 300


class TerminalCaptureError(RuntimeError):
    """A terminal-capture failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ValidatedCapturePolicy:
    """Closed, schema-validated terminal capture policy."""

    payload: dict[str, Any]

    @property
    def schema_version(self) -> int:
        """Return the policy schema version."""
        return int(self.payload["schema_version"])

    @property
    def container_image(self) -> str:
        """Return the digest-pinned upstream VHS image reference."""
        return str(self.payload["container_image"])

    @property
    def supported_platforms(self) -> dict[str, str]:
        """Return native Linux platform-to-manifest-digest constraints."""
        return deepcopy(self.payload["supported_platforms"])

    @property
    def tool_versions(self) -> dict[str, str]:
        """Return the exact reviewed tool version strings."""
        return {name: str(tool["version"]) for name, tool in self.payload["toolchain"].items()}

    @property
    def font(self) -> dict[str, str]:
        """Return the pinned terminal font identity."""
        return deepcopy(self.payload["font"])

    @property
    def locale(self) -> dict[str, str]:
        """Return the pinned locale compatibility identity."""
        return deepcopy(self.payload["locale"])

    @property
    def environment(self) -> dict[str, str]:
        """Return the complete isolated capture environment."""
        return deepcopy(self.payload["environment"])

    @property
    def scenarios(self) -> dict[str, dict[str, Any]]:
        """Return policy scenarios indexed by their unique identifier."""
        return {item["id"]: deepcopy(item) for item in self.payload["scenarios"]}


@dataclass(frozen=True)
class ScenarioCaptureResult:
    """Sanitized result returned by an isolated capture backend."""

    transcript: str
    exit_code: int
    network_isolated: bool


class CaptureBackend(Protocol):
    """Backend boundary used by the orchestrator and offline unit tests."""

    def prepare(
        self,
        *,
        repository_root: Path,
        policy: ValidatedCapturePolicy,
    ) -> None:
        """Validate and prepare the pinned capture toolchain."""

    def capture(
        self,
        *,
        scenario: dict[str, Any],
        policy_scenario: dict[str, Any],
        run_number: int,
        working_directory: Path,
        image_path: Path,
        tape: str,
    ) -> ScenarioCaptureResult:
        """Capture one scenario inside an isolated true-PTY environment."""


class CommandRunner(Protocol):
    """Injectable subprocess boundary for Docker contract tests."""

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        """Run one closed argv command without invoking a shell."""


def _default_runner(
    command: tuple[str, ...],
    *,
    cwd: Path | None,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    # Every caller supplies a closed argv assembled from schema-validated policy.
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


class DockerCaptureBackend:
    """Run the reviewed VHS toolchain in a native, networkless Docker container."""

    def __init__(
        self,
        *,
        runner: CommandRunner = _default_runner,
        uid: int | None = None,
        gid: int | None = None,
    ) -> None:
        self._runner = runner
        self._uid = os.getuid() if uid is None else uid
        self._gid = os.getgid() if gid is None else gid
        self._policy: ValidatedCapturePolicy | None = None
        self._platform: str | None = None
        self._image_tag: str | None = None

    def _run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout_seconds: int = _DOCKER_TIMEOUT_SECONDS,
        failure_code: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = self._runner(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            _fail(failure_code, "Docker command could not be completed")
        if completed.returncode != 0:
            _fail(failure_code, "Docker command exited nonzero")
        return completed

    @staticmethod
    def _safe_bind_source(
        path: Path,
        *,
        host_platform: str | None = None,
        private_var_root: Path = Path("/private/var"),
        var_alias_root: Path = Path("/var"),
    ) -> str:
        try:
            resolved_path = path.resolve(strict=True)
        except OSError:
            _fail(
                "DOCSHOT_TERMINAL_TEMP_INVALID",
                "capture path must be an existing directory",
            )
        bind_path = resolved_path
        platform_name = sys.platform if host_platform is None else host_platform
        if platform_name == "darwin":
            try:
                private_root = private_var_root.resolve(strict=True)
                relative = resolved_path.relative_to(private_root)
            except ValueError:
                pass
            except OSError:
                _fail(
                    "DOCSHOT_TERMINAL_TEMP_INVALID",
                    "macOS Docker bind roots could not be validated",
                )
            else:
                alias = var_alias_root / relative
                try:
                    alias_target = alias.resolve(strict=True)
                except OSError:
                    _fail(
                        "DOCSHOT_TERMINAL_TEMP_INVALID",
                        "macOS Docker bind alias could not be validated",
                    )
                if alias_target != resolved_path:
                    _fail(
                        "DOCSHOT_TERMINAL_TEMP_INVALID",
                        "macOS Docker bind alias does not match the capture path",
                    )
                # Colima exposes /var to its VM, while pathlib canonicalizes the
                # same macOS path through /private/var. Preserve the verified
                # alias instead of handing Docker the unreachable canonical path.
                bind_path = alias
        resolved = str(bind_path)
        if "," in resolved or "\n" in resolved or "\r" in resolved:
            _fail(
                "DOCSHOT_TERMINAL_TEMP_INVALID",
                "capture paths may not contain Docker mount separators",
            )
        return resolved

    def _docker_run_prefix(self, capture_root: Path) -> tuple[str, ...]:
        if self._policy is None or self._platform is None or self._image_tag is None:
            _fail(
                "DOCSHOT_TERMINAL_BACKEND_NOT_READY",
                "terminal capture backend was not prepared",
            )
        source = self._safe_bind_source(capture_root)
        identity = f"{self._uid}:{self._gid}"
        command = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--platform",
            self._platform,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--user",
            identity,
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777,uid={self._uid},gid={self._gid}",  # noqa: S108
            "--tmpfs",
            f"/dev/shm:rw,nosuid,nodev,size=512m,mode=1777,uid={self._uid},gid={self._gid}",  # noqa: S108
            "--mount",
            f"type=bind,src={source},dst=/capture",
            "--workdir",
            "/workspace",
        ]
        for name, value in sorted(self._policy.environment.items()):
            command.extend(("--env", f"{name}={value}"))
        command.append(self._image_tag)
        return tuple(command)

    @staticmethod
    def _initialize_capture_state(capture_root: Path) -> None:
        for relative in (
            "home",
            "xdg/cache",
            "xdg/config",
            "xdg/data",
            "ancestryllm/config",
            "ancestryllm/data",
        ):
            (capture_root / relative).mkdir(parents=True, exist_ok=True)
        (capture_root / "ancestryllm/config/config.toml").write_text(
            'schema_version = 1\nrevision = 0\n\n[providers]\ndefault = "none"\n',
            encoding="utf-8",
        )

    def prepare(
        self,
        *,
        repository_root: Path,
        policy: ValidatedCapturePolicy,
    ) -> None:
        """Verify native manifests, build the closed image, and run its preflight."""
        docker = shutil.which("docker")
        if docker is None and self._runner is _default_runner:
            _fail(
                "DOCSHOT_TERMINAL_TOOL_MISSING",
                "Docker is required for terminal screenshot capture",
            )
        repository = repository_root.resolve(strict=True)
        server = self._run(
            ("docker", "version", "--format", "{{json .Server}}"),
            failure_code="DOCSHOT_TERMINAL_DOCKER_UNAVAILABLE",
        )
        try:
            server_payload = json.loads(server.stdout)
            selected_platform = normalize_container_platform(
                str(server_payload["Os"]),
                str(server_payload["Arch"]),
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            _fail(
                "DOCSHOT_TERMINAL_DOCKER_UNAVAILABLE",
                "Docker server platform response is invalid",
            )
        expected_digest = policy.supported_platforms.get(selected_platform)
        if expected_digest is None:
            _fail(
                "DOCSHOT_TERMINAL_PLATFORM_UNSUPPORTED",
                "Docker server platform is not permitted by capture policy",
            )

        manifest = self._run(
            ("docker", "manifest", "inspect", policy.container_image),
            failure_code="DOCSHOT_TERMINAL_IMAGE_UNVERIFIED",
        )
        try:
            manifest_payload = json.loads(manifest.stdout)
            descriptors = manifest_payload["manifests"]
            os_name, architecture = selected_platform.split("/", maxsplit=1)
            matching = [
                item
                for item in descriptors
                if item.get("platform", {}).get("os") == os_name
                and item.get("platform", {}).get("architecture") == architecture
            ]
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            _fail(
                "DOCSHOT_TERMINAL_IMAGE_UNVERIFIED",
                "VHS image manifest response is invalid",
            )
        if len(matching) != 1 or matching[0].get("digest") != expected_digest:
            _fail(
                "DOCSHOT_TERMINAL_IMAGE_UNVERIFIED",
                "VHS image native platform digest does not match policy",
            )

        dockerfile = repository / str(policy.payload["dockerfile"])
        try:
            dockerfile.resolve(strict=True).relative_to(repository)
        except (OSError, ValueError):
            _fail(
                "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
                "capture Dockerfile is missing or outside the repository",
            )
        if dockerfile.is_symlink() or not dockerfile.is_file():
            _fail(
                "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
                "capture Dockerfile must be a regular repository file",
            )
        policy_digest = hashlib.sha256(
            json.dumps(policy.payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        image_tag = f"ancestryllm-docs-terminal:{policy_digest}"
        self._run(
            (
                "docker",
                "build",
                "--pull",
                "--platform",
                selected_platform,
                "--file",
                str(dockerfile),
                "--tag",
                image_tag,
                "--build-arg",
                f"VHS_IMAGE={policy.container_image}",
                "--build-arg",
                f"UV_IMAGE={policy.payload['uv_image']}",
                ".",
            ),
            cwd=repository,
            failure_code="DOCSHOT_TERMINAL_IMAGE_BUILD_FAILED",
        )

        self._policy = policy
        self._platform = selected_platform
        self._image_tag = image_tag
        with tempfile.TemporaryDirectory(
            prefix=".ancestryllm-terminal-preflight-",
            dir=repository,
        ) as name:
            preflight_root = Path(name)
            self._initialize_capture_state(preflight_root)
            preflight_payload = {
                "schema_version": 1,
                "app_executable": "/workspace/.venv/bin/ancestry",
                "environment": policy.environment,
                "font": policy.font,
                "locale": policy.locale,
                "toolchain": policy.payload["toolchain"],
            }
            (preflight_root / "preflight.json").write_text(
                json.dumps(preflight_payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            completed = self._run(
                (
                    *self._docker_run_prefix(preflight_root),
                    "/workspace/.venv/bin/python",
                    "/workspace/scripts/docs_terminal_preflight.py",
                    "/capture/preflight.json",
                ),
                timeout_seconds=int(policy.payload["terminal"]["timeout_seconds"]),
                failure_code="DOCSHOT_TERMINAL_PREFLIGHT_FAILED",
            )
            try:
                receipt = json.loads(completed.stdout)
            except json.JSONDecodeError:
                _fail(
                    "DOCSHOT_TERMINAL_PREFLIGHT_FAILED",
                    "capture preflight receipt is invalid",
                )
            if receipt != {"network_isolated": True, "status": "ok"}:
                _fail(
                    "DOCSHOT_TERMINAL_PREFLIGHT_FAILED",
                    "capture preflight receipt did not prove the closed environment",
                )

    @staticmethod
    def _scenario_payload(
        scenario: dict[str, Any],
        policy_scenario: dict[str, Any],
        policy: ValidatedCapturePolicy,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "columns": int(policy.payload["terminal"]["columns"]),
            "rows": int(policy.payload["terminal"]["rows"]),
            "timeout_seconds": int(policy.payload["terminal"]["timeout_seconds"]),
            "launch": [f"/workspace/{token}" for token in scenario["launch"][:1]]
            + [str(token) for token in scenario["launch"][1:]],
            "ready_signal": str(scenario["ready_signal"]["value"]),
            "interactions": deepcopy(policy_scenario["interactions"]),
        }

    def capture(
        self,
        *,
        scenario: dict[str, Any],
        policy_scenario: dict[str, Any],
        run_number: int,
        working_directory: Path,
        image_path: Path,
        tape: str,
    ) -> ScenarioCaptureResult:
        """Validate via a true PTY, then render the same clean scenario with VHS."""
        del run_number
        if self._policy is None:
            _fail(
                "DOCSHOT_TERMINAL_BACKEND_NOT_READY",
                "terminal capture backend was not prepared",
            )
        if image_path != working_directory / "output.png":
            _fail(
                "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
                "Docker backend accepts only its declared output path",
            )

        pty_root = working_directory / "pty-validation"
        pty_root.mkdir()
        self._initialize_capture_state(pty_root)
        scenario_payload = self._scenario_payload(
            scenario,
            policy_scenario,
            self._policy,
        )
        (pty_root / "scenario.json").write_text(
            json.dumps(scenario_payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        timeout = int(self._policy.payload["terminal"]["timeout_seconds"])
        pty = self._run(
            (
                *self._docker_run_prefix(pty_root),
                "/workspace/.venv/bin/python",
                "/workspace/scripts/docs_terminal_pty.py",
                "/capture/scenario.json",
            ),
            timeout_seconds=timeout + 5,
            failure_code="DOCSHOT_TERMINAL_COMMAND_FAILED",
        )

        self._initialize_capture_state(working_directory)
        (working_directory / "scenario.tape").write_text(tape, encoding="utf-8")
        rendered = self._run(
            (
                *self._docker_run_prefix(working_directory),
                "/usr/bin/vhs",
                "/capture/scenario.tape",
            ),
            timeout_seconds=timeout + 30,
            failure_code="DOCSHOT_TERMINAL_COMMAND_FAILED",
        )
        return ScenarioCaptureResult(
            transcript=pty.stdout,
            exit_code=rendered.returncode,
            network_isolated=True,
        )


def _fail(code: str, message: str) -> Never:
    raise TerminalCaptureError(code, message)


def _read_json(path: Path, *, code: str, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code, f"{label} is missing or is not valid UTF-8 JSON")


def _validate_schema(instance: Any, schema: Any) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except SchemaError:
        _fail(
            "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
            "terminal capture policy schema is invalid",
        )
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        _fail(
            "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
            f"terminal capture policy does not satisfy its closed schema at {location}",
        )


def _contains_unsafe_syntax(value: str) -> bool:
    return any(marker in value for marker in (*_URL_MARKERS, *_SHELL_MARKERS))


def validate_capture_policy(
    payload: dict[str, Any],
    *,
    schema_path: Path,
) -> ValidatedCapturePolicy:
    """Validate schema v1 plus cross-field terminal capture invariants."""
    if type(payload) is not dict or type(payload.get("schema_version")) is not int:
        _fail(
            "DOCSHOT_TERMINAL_POLICY_UNSUPPORTED",
            "terminal capture policy schema_version must be integer 1",
        )
    if payload["schema_version"] != SCHEMA_VERSION:
        _fail(
            "DOCSHOT_TERMINAL_POLICY_UNSUPPORTED",
            "terminal capture policy schema_version is not supported",
        )

    schema = _read_json(
        schema_path,
        code="DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
        label="terminal capture policy schema",
    )
    _validate_schema(payload, schema)

    scenario_ids: set[str] = set()
    for scenario in payload["scenarios"]:
        scenario_id = str(scenario["id"])
        if scenario_id.casefold() in scenario_ids:
            _fail(
                "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
                "terminal capture scenario identifiers must be unique",
            )
        scenario_ids.add(scenario_id.casefold())
        interactions = scenario["interactions"]
        capture_after_step = scenario["capture_after_step"]
        if capture_after_step > len(interactions):
            _fail(
                "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
                "capture_after_step exceeds the scenario interaction count",
            )
        for interaction in interactions:
            if _contains_unsafe_syntax(str(interaction["input"])):
                _fail(
                    "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
                    "terminal interaction contains URL or shell syntax",
                )

    return ValidatedCapturePolicy(payload=deepcopy(payload))


def load_capture_policy(path: Path) -> ValidatedCapturePolicy:
    """Load and validate the checked-in schema-v1 terminal capture policy."""
    policy_path = path.resolve()
    payload = _read_json(
        policy_path,
        code="DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
        label="terminal capture policy",
    )
    if type(payload) is not dict:
        _fail(
            "DOCSHOT_TERMINAL_POLICY_UNSUPPORTED",
            "terminal capture policy root must be an object",
        )
    schema_path = policy_path.with_name(Path(_POLICY_SCHEMA).name)
    return validate_capture_policy(payload, schema_path=schema_path)


def normalize_container_platform(system: str, machine: str) -> str:
    """Map a host identifier to one supported native Linux container platform."""
    if system.casefold() != "linux":
        _fail(
            "DOCSHOT_TERMINAL_PLATFORM_UNSUPPORTED",
            "terminal captures require a native Linux container platform",
        )
    architecture = machine.casefold()
    if architecture in {"x86_64", "amd64"}:
        return "linux/amd64"
    if architecture in {"aarch64", "arm64"}:
        return "linux/arm64"
    _fail(
        "DOCSHOT_TERMINAL_PLATFORM_UNSUPPORTED",
        "terminal capture host architecture is not supported",
    )


def _vhs_string(value: str) -> str:
    if _contains_unsafe_syntax(value) or '"' in value or "\\" in value:
        _fail(
            "DOCSHOT_TERMINAL_TAPE_UNSAFE",
            "VHS tape values must not contain shell, URL, quote, or escape syntax",
        )
    return f'"{value}"'


def _vhs_regex(value: str) -> str:
    if not value or _contains_unsafe_syntax(value):
        _fail(
            "DOCSHOT_TERMINAL_TAPE_UNSAFE",
            "VHS screen expectations must not contain shell or URL syntax",
        )
    escaped = re.sub(r"([\\.^$|?*+(){}\[\]/])", r"\\\1", value)
    return f"/{escaped}/"


def render_vhs_tape(
    *,
    scenario: dict[str, Any],
    policy_scenario: dict[str, Any],
    policy: ValidatedCapturePolicy,
    screenshot_path: str,
) -> str:
    """Render a deterministic VHS tape from validated manifest and policy values."""
    terminal = policy.payload["terminal"]
    theme = terminal["theme"]
    theme_json = json.dumps(
        {
            "background": theme["background"],
            "cursorColor": theme["cursor"],
            "foreground": theme["foreground"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    command = " ".join(str(token) for token in scenario["launch"])
    lines = [
        f"Set Shell {_vhs_string(str(terminal['shell']))}",
        f"Set FontFamily {_vhs_string(str(policy.font['family']))}",
        f"Set FontSize {terminal['font_size']}",
        f"Set Width {terminal['width']}",
        f"Set Height {terminal['height']}",
        f"Set TypingSpeed {terminal['typing_speed']}",
        "Set CursorBlink false",
        f"Set Theme {theme_json}",
        "",
        f"Type {_vhs_string(command)}",
        "Enter",
        f"Sleep {terminal['startup_wait']}",
        f"Wait+Screen {_vhs_regex(str(scenario['ready_signal']['value']))}",
        f"Sleep {terminal['settle_wait']}",
    ]
    capture_after_step = int(policy_scenario["capture_after_step"])
    if capture_after_step == 0:
        lines.extend(
            (
                f"Screenshot {_vhs_string(screenshot_path)}",
                f"Sleep {terminal['settle_wait']}",
            )
        )

    for step_number, interaction in enumerate(policy_scenario["interactions"], start=1):
        lines.extend(
            (
                f"Type {_vhs_string(str(interaction['input']))}",
                "Enter",
            )
        )
        if "expect" in interaction:
            lines.append(f"Wait+Screen {_vhs_regex(str(interaction['expect']))}")
        lines.append(f"Sleep {interaction['wait_after']}")
        if capture_after_step == step_number:
            lines.extend(
                (
                    f"Screenshot {_vhs_string(screenshot_path)}",
                    f"Sleep {terminal['settle_wait']}",
                )
            )

    lines.append(f"Sleep {terminal['final_wait']}")
    return "\n".join(lines) + "\n"


def _terminal_scenarios(manifest: ValidatedManifest) -> tuple[dict[str, Any], ...]:
    return tuple(
        deepcopy(scenario) for scenario in manifest.scenarios if scenario["surface"] == "terminal"
    )


def _select_terminal_scenarios(
    scenarios: tuple[dict[str, Any], ...],
    scenario_ids: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    if not scenario_ids:
        return scenarios
    normalized = [scenario_id.casefold() for scenario_id in scenario_ids]
    available = {str(scenario["id"]).casefold() for scenario in scenarios}
    if (
        any(not scenario_id for scenario_id in scenario_ids)
        or len(set(normalized)) != len(normalized)
        or not set(normalized).issubset(available)
    ):
        _fail(
            "DOCSHOT_TERMINAL_SCENARIO_SELECTION_INVALID",
            "terminal scenario selection is blank, duplicated, or undeclared",
        )
    selected = set(normalized)
    return tuple(scenario for scenario in scenarios if str(scenario["id"]).casefold() in selected)


def _ensure_closed_scenario_contract(
    manifest: ValidatedManifest,
    manifest_scenarios: tuple[dict[str, Any], ...],
    policy: ValidatedCapturePolicy,
) -> None:
    manifest_ids = {str(scenario["id"]) for scenario in manifest_scenarios}
    if manifest_ids != set(policy.scenarios):
        _fail(
            "DOCSHOT_TERMINAL_SCENARIO_MISMATCH",
            "manifest and terminal capture policy scenario identifiers differ",
        )
    for scenario in manifest_scenarios:
        geometry = scenario["geometry"]
        terminal = policy.payload["terminal"]
        if geometry["columns"] != terminal["columns"] or geometry["rows"] != terminal["rows"]:
            _fail(
                "DOCSHOT_TERMINAL_GEOMETRY_MISMATCH",
                "manifest and terminal capture policy geometry differ",
            )
    determinism = manifest.payload["determinism"]
    terminal_font = determinism["fonts"]["terminal"]
    terminal = policy.payload["terminal"]
    if (
        policy.locale["name"] != determinism["locale"]
        or policy.environment["LANG"] != determinism["locale"]
        or policy.environment["LC_ALL"] != determinism["locale"]
        or policy.environment["TZ"] != determinism["timezone"]
        or policy.font["family"] != terminal_font["family"]
        or terminal["font_size"] != terminal_font["size_px"]
        or determinism["theme"] != "light"
        or determinism["animations"] != "disabled"
        or determinism["network"] != "deny"
    ):
        _fail(
            "DOCSHOT_TERMINAL_DETERMINISM_MISMATCH",
            "manifest and terminal capture determinism controls differ",
        )


def _require_directory(path: Path, *, code: str, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code, f"{label} must already exist")
    if not resolved.is_dir() or path.is_symlink():
        _fail(code, f"{label} must be a regular directory")
    return resolved


def _relative_output_path(raw_path: str) -> Path:
    posix_path = PurePosixPath(raw_path)
    if posix_path.is_absolute() or any(part in {"", ".", ".."} for part in posix_path.parts):
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
            "terminal output path is not normalized and relative",
        )
    return Path(*posix_path.parts)


def _safe_output_destination(output_root: Path, relative_output: Path) -> Path:
    """Resolve one declared output without following a symlink below the root."""
    destination = output_root / relative_output
    current = output_root
    try:
        for part in relative_output.parts[:-1]:
            current /= part
            if current.is_symlink():
                _fail(
                    "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
                    "declared output path contains a symlinked directory",
                )
            if current.exists() and not current.is_dir():
                _fail(
                    "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
                    "declared output parent is not a directory",
                )
        if destination.is_symlink():
            _fail(
                "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
                "declared output destination is a symlink",
            )
        resolved_parent = destination.parent.resolve(strict=False)
    except OSError:
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
            "declared output path could not be resolved safely",
        )
    if not resolved_parent.is_relative_to(output_root):
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
            "declared output path escapes the output root",
        )
    return destination


def _validate_result(
    *,
    manifest: ValidatedManifest,
    scenario: dict[str, Any],
    result: ScenarioCaptureResult,
    image_path: Path,
    run_directory: Path,
) -> bytes:
    if result.exit_code != 0:
        _fail(
            "DOCSHOT_TERMINAL_COMMAND_FAILED",
            f"terminal scenario {scenario['id']} exited nonzero",
        )
    if not result.network_isolated:
        _fail(
            "DOCSHOT_TERMINAL_NETWORK_NOT_DENIED",
            "terminal capture backend did not prove network isolation",
        )
    ready_signal = str(scenario["ready_signal"]["value"])
    if ready_signal not in result.transcript:
        _fail(
            "DOCSHOT_TERMINAL_READY_MISSING",
            f"terminal scenario {scenario['id']} did not emit its readiness signal",
        )
    try:
        validate_capture_text(manifest, result.transcript)
    except ScreenshotManifestError as error:
        raise TerminalCaptureError(
            error.code, "terminal transcript failed privacy validation"
        ) from error

    undeclared_pngs = [
        path
        for path in run_directory.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".png" and path != image_path
    ]
    if undeclared_pngs:
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
            "terminal capture backend produced an undeclared PNG",
        )
    try:
        image = image_path.read_bytes()
    except OSError:
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_INVALID",
            "terminal capture backend did not produce the declared PNG",
        )
    if not image.startswith(PNG_SIGNATURE):
        _fail(
            "DOCSHOT_TERMINAL_OUTPUT_INVALID",
            "terminal capture output does not have a PNG signature",
        )
    return image


def _publish_atomically(
    staged: tuple[tuple[Path, bytes], ...],
    *,
    output_root: Path,
) -> tuple[Path, ...]:
    published: list[Path] = []
    backups: dict[Path, bytes] = {}
    try:
        for destination, image in staged:
            relative_output = destination.relative_to(output_root)
            destination = _safe_output_destination(output_root, relative_output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    _fail(
                        "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED",
                        "declared output destination is not a regular file",
                    )
                backups[destination] = destination.read_bytes()
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(image)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary_path.replace(destination)
            finally:
                temporary_path.unlink(missing_ok=True)
            published.append(destination)
    except (OSError, TerminalCaptureError):
        for destination in reversed(published):
            if destination in backups:
                destination.write_bytes(backups[destination])
            else:
                destination.unlink(missing_ok=True)
        raise
    return tuple(published)


def capture_terminal_screenshots(
    *,
    manifest_path: Path,
    policy_path: Path,
    repository_root: Path,
    output_root: Path,
    temporary_root: Path,
    backend: CaptureBackend,
    scenario_ids: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    """Capture selected terminal scenarios twice, then publish validated PNGs."""
    resolved_repository = _require_directory(
        repository_root,
        code="DOCSHOT_TERMINAL_REPOSITORY_INVALID",
        label="repository root",
    )
    resolved_output = _require_directory(
        output_root,
        code="DOCSHOT_TERMINAL_OUTPUT_INVALID",
        label="output root",
    )
    resolved_temporary = _require_directory(
        temporary_root,
        code="DOCSHOT_TERMINAL_TEMP_INVALID",
        label="temporary root",
    )
    manifest = load_manifest(manifest_path, repository_root=resolved_repository)
    policy = load_capture_policy(policy_path)
    scenarios = _terminal_scenarios(manifest)
    _ensure_closed_scenario_contract(manifest, scenarios, policy)
    scenarios = _select_terminal_scenarios(scenarios, scenario_ids)

    backend.prepare(repository_root=resolved_repository, policy=policy)
    staged: list[tuple[Path, bytes]] = []
    with tempfile.TemporaryDirectory(
        prefix="ancestryllm-terminal-capture-",
        dir=resolved_temporary,
    ) as session_name:
        session_directory = Path(session_name)
        for scenario in scenarios:
            scenario_id = str(scenario["id"])
            policy_scenario = policy.scenarios[scenario_id]
            images: list[bytes] = []
            for run_number in (1, 2):
                run_directory = session_directory / f"{scenario_id}-{run_number}"
                run_directory.mkdir()
                image_path = run_directory / "output.png"
                tape = render_vhs_tape(
                    scenario=scenario,
                    policy_scenario=policy_scenario,
                    policy=policy,
                    screenshot_path="/capture/output.png",
                )
                result = backend.capture(
                    scenario=deepcopy(scenario),
                    policy_scenario=deepcopy(policy_scenario),
                    run_number=run_number,
                    working_directory=run_directory,
                    image_path=image_path,
                    tape=tape,
                )
                images.append(
                    _validate_result(
                        manifest=manifest,
                        scenario=scenario,
                        result=result,
                        image_path=image_path,
                        run_directory=run_directory,
                    )
                )
            if not hmac.compare_digest(
                hashlib.sha256(images[0]).digest(),
                hashlib.sha256(images[1]).digest(),
            ):
                _fail(
                    "DOCSHOT_TERMINAL_REPEATABILITY_FAILED",
                    f"terminal scenario {scenario_id} was not byte-identical across two runs",
                )
            relative_output = _relative_output_path(str(scenario["output_path"]))
            staged.append((_safe_output_destination(resolved_output, relative_output), images[0]))

        return _publish_atomically(tuple(staged), output_root=resolved_output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "validate-policy"))
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/docs-terminal-capture-policy.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/docs-screenshot-manifest.json"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path())
    parser.add_argument("--output-root", type=Path, default=Path())
    parser.add_argument(
        "--temporary-root",
        type=Path,
    )
    parser.add_argument("--scenario", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate policy or capture all declared terminal documentation PNGs."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-policy":
            policy = load_capture_policy(args.policy)
            result = {"schema_version": policy.schema_version, "status": "valid"}
        else:
            captured = capture_terminal_screenshots(
                manifest_path=args.manifest,
                policy_path=args.policy,
                repository_root=args.repository_root,
                output_root=args.output_root,
                temporary_root=(
                    args.temporary_root if args.temporary_root is not None else args.repository_root
                ),
                backend=DockerCaptureBackend(),
                scenario_ids=tuple(args.scenario),
            )
            result = {"captured": len(captured), "status": "ok"}
    except (ScreenshotManifestError, TerminalCaptureError) as error:
        print(error.code, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
