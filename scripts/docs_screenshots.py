#!/usr/bin/env python3
"""Regenerate or verify every manifest-owned documentation screenshot."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Never, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

if __package__:
    from scripts.docs_screenshot_manifest import (
        ScreenshotManifestError,
        ValidatedManifest,
        load_manifest,
        validate_png_bytes,
        validate_published_assets,
    )
    from scripts.docs_terminal_capture import (
        DockerCaptureBackend,
        TerminalCaptureError,
        capture_terminal_screenshots,
    )
else:
    from docs_screenshot_manifest import (
        ScreenshotManifestError,
        ValidatedManifest,
        load_manifest,
        validate_png_bytes,
        validate_published_assets,
    )
    from docs_terminal_capture import (
        DockerCaptureBackend,
        TerminalCaptureError,
        capture_terminal_screenshots,
    )

SCHEMA_VERSION = 1
SUPPORTED_SURFACES = frozenset({"electron", "terminal"})
_DEFAULT_MANIFEST_PATH = Path("config/docs-screenshot-manifest.json")
_DRIFT_REPORT_SCHEMA = "config/docs-screenshot-drift-report-v1.schema.json"
_ELECTRON_COMMAND_TIMEOUT_SECONDS = 600
_ELECTRON_RUNTIME_ENVIRONMENT = frozenset(
    {
        "CI",
        "COMSPEC",
        "ComSpec",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "GITHUB_ACTIONS",
        "LANG",
        "LC_ALL",
        "NODE_EXTRA_CA_CERTS",
        "PATH",
        "PATHEXT",
        "RUNNER_OS",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WAYLAND_DISPLAY",
        "WINDIR",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
    },
)


class DocsScreenshotError(RuntimeError):
    """A screenshot-publication failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class CaptureRunner(Protocol):
    """Injected capture boundary used by production adapters and offline tests."""

    def __call__(
        self,
        *,
        surface: str,
        scenario_ids: tuple[str, ...],
        output_root: Path,
        temporary_root: Path,
        repository_root: Path,
        manifest_path: Path,
    ) -> None:
        """Capture only the selected scenarios beneath ``output_root``."""


def _fail(code: str, message: str) -> Never:
    raise DocsScreenshotError(code, message)


def select_scenarios(
    manifest: ValidatedManifest,
    *,
    surfaces: tuple[str, ...] = (),
    scenario_ids: tuple[str, ...] = (),
) -> tuple[dict[str, Any], ...]:
    """Return a deterministic, closed subset of manifest scenarios."""
    if len(set(surfaces)) != len(surfaces) or any(
        surface not in SUPPORTED_SURFACES for surface in surfaces
    ):
        _fail("DOCSHOT_SURFACE_UNSUPPORTED", "capture surface is duplicated or unsupported")
    if len(set(scenario_ids)) != len(scenario_ids):
        _fail("DOCSHOT_SCENARIO_DUPLICATE", "scenario selection contains a duplicate")

    by_id = {str(scenario["id"]): scenario for scenario in manifest.scenarios}
    unknown = set(scenario_ids) - set(by_id)
    if unknown:
        _fail("DOCSHOT_SCENARIO_UNKNOWN", "scenario selection is not declared")
    selected_surfaces = set(surfaces) if surfaces else set(SUPPORTED_SURFACES)
    if scenario_ids and any(
        str(by_id[scenario_id]["surface"]) not in selected_surfaces for scenario_id in scenario_ids
    ):
        _fail(
            "DOCSHOT_SCENARIO_SURFACE_MISMATCH",
            "scenario selection is outside the selected surfaces",
        )
    selected_ids = set(scenario_ids) if scenario_ids else set(by_id)
    selected = tuple(
        scenario
        for scenario in sorted(manifest.scenarios, key=lambda item: str(item["id"]))
        if str(scenario["id"]) in selected_ids and str(scenario["surface"]) in selected_surfaces
    )
    if not selected:
        _fail("DOCSHOT_SCENARIO_UNKNOWN", "scenario selection is empty")
    return selected


def _copy_repository_snapshot(repository_root: Path, workspace: Path) -> None:
    """Copy tracked working-tree source into an isolated workspace."""
    git_executable = shutil.which("git")
    if git_executable is None:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "Git is unavailable for capture staging")
    try:
        listed = subprocess.run(  # noqa: S603
            (
                git_executable,
                "ls-files",
                "--cached",
                "-z",
            ),
            cwd=repository_root,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot could not be listed")
    if listed.returncode != 0:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot could not be listed")

    try:
        relative_paths = tuple(
            Path(raw_path.decode("utf-8")) for raw_path in listed.stdout.split(b"\0") if raw_path
        )
    except UnicodeError:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot path is invalid")
    if not relative_paths:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot is empty")

    workspace.mkdir(parents=True)
    repository_real = repository_root.resolve()
    for relative_path in relative_paths:
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot path is unsafe")
        source = repository_root / relative_path
        try:
            if source.is_symlink() or not source.is_file():
                _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot entry is unsafe")
            if not source.resolve().is_relative_to(repository_real):
                _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot path escapes")
            destination = workspace / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError:
            _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "repository snapshot could not be copied")


def _electron_build_environment(temporary_root: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in _ELECTRON_RUNTIME_ENVIRONMENT
    }
    home = temporary_root / "home"
    home.mkdir(mode=0o700)
    environment["HOME"] = str(home)
    environment["LANG"] = "en_US.UTF-8"
    environment["LC_ALL"] = "en_US.UTF-8"
    environment["TZ"] = "UTC"
    return environment


def _run_electron_command(
    command: tuple[str, ...],
    *,
    repository_root: Path,
    environment: dict[str, str],
) -> None:
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=repository_root,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=_ELECTRON_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "Electron capture could not be completed")
    if completed.returncode != 0:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "Electron capture exited nonzero")


def _pnpm_command(
    arguments: tuple[str, ...],
    *,
    host_platform: str = sys.platform,
    environment: dict[str, str] | None = None,
) -> tuple[str, ...]:
    if host_platform == "win32":
        values = environment or {}
        command_prompt = values.get("ComSpec") or values.get("COMSPEC") or "cmd.exe"
        return (command_prompt, "/d", "/s", "/c", "pnpm.cmd", *arguments)
    return ("pnpm", *arguments)


def _selected_manifest_path(manifest_path: Path | None, *, repository_root: Path) -> Path:
    selected = manifest_path or _DEFAULT_MANIFEST_PATH
    if not selected.is_absolute():
        selected = repository_root / selected
    return selected


def _stage_electron_manifest(manifest_path: Path, *, workspace: Path) -> None:
    try:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "selected manifest is not a regular file")
        content = manifest_path.read_bytes()
        destination = workspace / _DEFAULT_MANIFEST_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "staged manifest destination is unsafe")
        destination.write_bytes(content)
    except OSError:
        _fail("DOCSHOT_ELECTRON_CAPTURE_FAILED", "selected manifest could not be staged")


def _default_capture_runner(
    *,
    surface: str,
    scenario_ids: tuple[str, ...],
    output_root: Path,
    temporary_root: Path,
    repository_root: Path,
    manifest_path: Path,
) -> None:
    if surface == "terminal":
        capture_terminal_screenshots(
            manifest_path=manifest_path,
            policy_path=repository_root / "config/docs-terminal-capture-policy.json",
            repository_root=repository_root,
            output_root=output_root,
            temporary_root=temporary_root,
            backend=DockerCaptureBackend(),
            scenario_ids=scenario_ids,
        )
        return
    if surface != "electron":
        _fail("DOCSHOT_SURFACE_UNSUPPORTED", "capture surface is unsupported")

    workspace = temporary_root / "electron-workspace"
    _copy_repository_snapshot(repository_root, workspace)
    _stage_electron_manifest(manifest_path, workspace=workspace)
    environment = _electron_build_environment(temporary_root)
    environment["ANCESTRYLLM_DOCS_SCREENSHOT_OUTPUT_ROOT"] = str(output_root)
    environment["ANCESTRYLLM_DOCS_SCREENSHOT_SCENARIOS"] = ",".join(scenario_ids)
    _run_electron_command(
        ("node", "desktop/scripts/install-locked.mjs"),
        repository_root=workspace,
        environment=environment,
    )
    _run_electron_command(
        _pnpm_command(
            ("--dir", "desktop", "capture:docs"),
            environment=environment,
        ),
        repository_root=workspace,
        environment=environment,
    )


def _relative_output(raw_path: str) -> Path:
    output = PurePosixPath(raw_path)
    if output.is_absolute() or any(part in {"", ".", ".."} for part in output.parts):
        _fail("DOCSHOT_OUTPUT_UNDECLARED", "capture output is not normalized")
    return Path(*output.parts)


def _validate_staged_outputs(
    manifest: ValidatedManifest,
    scenarios: tuple[dict[str, Any], ...],
    *,
    output_root: Path,
) -> None:
    expected = {str(scenario["output_path"]) for scenario in scenarios}
    discovered = {
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*")
        if path.is_file()
    }
    if discovered != expected:
        _fail(
            "DOCSHOT_CAPTURE_INVENTORY_MISMATCH",
            "staged output inventory differs from the selected manifest outputs",
        )
    for scenario in scenarios:
        image = output_root / _relative_output(str(scenario["output_path"]))
        try:
            content = image.read_bytes()
        except OSError:
            _fail("DOCSHOT_CAPTURE_INVENTORY_MISMATCH", "staged screenshot is unreadable")
        if any(canary.encode("utf-8") in content for canary in manifest.privacy_canaries):
            _fail("DOCSHOT_PRIVACY_CANARY_LEAKED", "staged screenshot contains a canary")
        try:
            validate_png_bytes(content)
        except ScreenshotManifestError:
            _fail("DOCSHOT_CAPTURE_INVALID", "staged screenshot is not a valid PNG")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _drift_report(
    scenarios: tuple[dict[str, Any], ...],
    *,
    repository_root: Path,
    captured_root: Path,
) -> dict[str, Any]:
    results: list[dict[str, str]] = []
    for scenario in scenarios:
        if scenario.get("comparison") != {"mode": "exact"}:
            _fail(
                "DOCSHOT_COMPARISON_UNSUPPORTED",
                "schema v1 supports exact screenshot comparison only",
            )
        relative = _relative_output(str(scenario["output_path"]))
        expected_sha256 = _sha256(repository_root / relative)
        captured_sha256 = _sha256(captured_root / relative)
        if not expected_sha256:
            status = "expected-missing"
        elif not captured_sha256:
            status = "captured-missing"
        elif hmac.compare_digest(expected_sha256, captured_sha256):
            status = "match"
        else:
            status = "hash-mismatch"
        results.append(
            {
                "captured_sha256": captured_sha256,
                "expected_sha256": expected_sha256,
                "id": str(scenario["id"]),
                "status": status,
                "surface": str(scenario["surface"]),
            },
        )
    success = all(result["status"] == "match" for result in results)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "success" if success else "failure",
        "scenarios": results,
    }
    validate_drift_report(report)
    return report


def validate_drift_report(report: Any) -> None:
    """Validate sanitized screenshot-drift evidence against its closed schema."""
    schema_path = Path(__file__).resolve().parents[1] / _DRIFT_REPORT_SCHEMA
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError):
        _fail("DOCSHOT_REPORT_SCHEMA_INVALID", "drift report schema is invalid")
    errors = sorted(
        validator.iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail("DOCSHOT_REPORT_SCHEMA_INVALID", "drift report violates its closed schema")
    scenario_results = report["scenarios"]
    semantic_success = bool(scenario_results) and all(
        scenario["status"] == "match"
        and hmac.compare_digest(
            scenario["expected_sha256"],
            scenario["captured_sha256"],
        )
        for scenario in scenario_results
    )
    if (report["status"] == "success") is not semantic_success:
        _fail("DOCSHOT_REPORT_SCHEMA_INVALID", "drift report status is inconsistent")


def _write_report(report: dict[str, Any], report_path: Path | None) -> None:
    if report_path is None:
        return
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        _fail("DOCSHOT_REPORT_WRITE_FAILED", "drift report could not be written")


def _capture_to_stage(
    scenarios: tuple[dict[str, Any], ...],
    *,
    output_root: Path,
    temporary_root: Path,
    repository_root: Path,
    manifest_path: Path,
    capture_runner: CaptureRunner,
) -> None:
    for surface in sorted({str(scenario["surface"]) for scenario in scenarios}):
        scenario_ids = tuple(
            str(scenario["id"]) for scenario in scenarios if str(scenario["surface"]) == surface
        )
        capture_runner(
            surface=surface,
            scenario_ids=scenario_ids,
            output_root=output_root,
            temporary_root=temporary_root,
            repository_root=repository_root,
            manifest_path=manifest_path,
        )


def _temporary_directory(
    *,
    prefix: str,
    root: Path,
) -> tempfile.TemporaryDirectory[str]:
    """Create capture staging with a stable failure for invalid host roots."""
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, dir=root)
    except OSError:
        _fail(
            "DOCSHOT_TEMPORARY_ROOT_INVALID",
            "temporary staging directory could not be created",
        )


def check_screenshots(
    manifest: ValidatedManifest,
    *,
    repository_root: Path,
    temporary_root: Path,
    manifest_path: Path | None = None,
    report_path: Path | None = None,
    surfaces: tuple[str, ...] = (),
    scenario_ids: tuple[str, ...] = (),
    capture_runner: CaptureRunner = _default_capture_runner,
) -> dict[str, Any]:
    """Capture into temporary storage and compare without changing the repository."""
    published_error: ScreenshotManifestError | None = None
    try:
        validate_published_assets(manifest, repository_root=repository_root)
    except ScreenshotManifestError as error:
        if error.code not in {"DOCSHOT_ASSET_INVALID", "DOCSHOT_ASSET_MISSING"}:
            raise
        published_error = error
    selected_manifest = _selected_manifest_path(manifest_path, repository_root=repository_root)
    selected = select_scenarios(
        manifest,
        surfaces=surfaces,
        scenario_ids=scenario_ids,
    )
    with _temporary_directory(
        prefix="ancestryllm-docshot-check-",
        root=temporary_root,
    ) as name:
        stage = Path(name) / "output"
        stage.mkdir()
        _capture_to_stage(
            selected,
            output_root=stage,
            temporary_root=Path(name),
            repository_root=repository_root,
            manifest_path=selected_manifest,
            capture_runner=capture_runner,
        )
        _validate_staged_outputs(manifest, selected, output_root=stage)
        report = _drift_report(
            selected,
            repository_root=repository_root,
            captured_root=stage,
        )
        _write_report(report, report_path)
        if report["status"] != "success":
            _fail("DOCSHOT_DRIFT_DETECTED", "captured screenshot hashes differ")
        if published_error is not None:
            raise published_error
        return report


def _publish_atomically(
    manifest: ValidatedManifest,
    scenarios: tuple[dict[str, Any], ...],
    *,
    repository_root: Path,
    captured_root: Path,
) -> tuple[Path, ...]:
    backups: dict[Path, tuple[bytes, int] | None] = {}
    published: list[Path] = []
    try:
        for scenario in scenarios:
            relative = _relative_output(str(scenario["output_path"]))
            source = captured_root / relative
            destination = repository_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                _fail("DOCSHOT_OUTPUT_UNDECLARED", "published destination is not a file")
            backups[destination] = (
                (
                    destination.read_bytes(),
                    stat.S_IMODE(destination.stat().st_mode),
                )
                if destination.exists()
                else None
            )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(source.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary_path.chmod(0o644)
                temporary_path.replace(destination)
            finally:
                temporary_path.unlink(missing_ok=True)
            published.append(destination)
        validate_published_assets(manifest, repository_root=repository_root)
    except (OSError, DocsScreenshotError, ScreenshotManifestError):
        for destination in reversed(published):
            previous = backups[destination]
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                previous_content, previous_mode = previous
                destination.write_bytes(previous_content)
                destination.chmod(previous_mode)
        raise
    return tuple(published)


def regenerate_screenshots(
    manifest: ValidatedManifest,
    *,
    repository_root: Path,
    temporary_root: Path,
    manifest_path: Path | None = None,
    surfaces: tuple[str, ...] = (),
    scenario_ids: tuple[str, ...] = (),
    capture_runner: CaptureRunner = _default_capture_runner,
) -> tuple[Path, ...]:
    """Capture selected screenshots in isolation, then publish them transactionally."""
    selected_manifest = _selected_manifest_path(manifest_path, repository_root=repository_root)
    selected = select_scenarios(
        manifest,
        surfaces=surfaces,
        scenario_ids=scenario_ids,
    )
    with _temporary_directory(
        prefix="ancestryllm-docshot-capture-",
        root=temporary_root,
    ) as name:
        stage = Path(name) / "output"
        stage.mkdir()
        _capture_to_stage(
            selected,
            output_root=stage,
            temporary_root=Path(name),
            repository_root=repository_root,
            manifest_path=selected_manifest,
            capture_runner=capture_runner,
        )
        _validate_staged_outputs(manifest, selected, output_root=stage)
        return _publish_atomically(
            manifest,
            selected,
            repository_root=repository_root,
            captured_root=stage,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "check"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/docs-screenshot-manifest.json"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--temporary-root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--surface",
        action="append",
        choices=sorted(SUPPORTED_SURFACES),
        default=[],
    )
    parser.add_argument("--scenario", action="append", default=[])
    return parser


def resolve_temporary_root(
    *,
    repository_root: Path,
    explicit: Path | None,
    runner_temp: str | None,
    host_platform: str,
    system_temp: Path,
) -> Path:
    """Select a capture root whose Docker bind is visible on the host platform."""
    if explicit is not None:
        selected = explicit
    elif host_platform == "darwin":
        # Colima does not expose macOS's /private/var or /var temporary tree by
        # default. The checked-out repository is already an approved bind root.
        selected = repository_root
    elif runner_temp:
        selected = Path(runner_temp)
    else:
        selected = system_temp
    try:
        if selected.is_symlink() or not selected.is_dir():
            _fail(
                "DOCSHOT_TEMPORARY_ROOT_INVALID",
                "temporary root must be an existing directory",
            )
    except OSError:
        _fail(
            "DOCSHOT_TEMPORARY_ROOT_INVALID",
            "temporary root could not be validated",
        )
    return selected


def main(argv: list[str] | None = None) -> int:
    """Run the repository-level screenshot publication contract."""
    args = _parser().parse_args(argv)
    report_path = args.report
    if report_path is None and os.environ.get("ANCESTRYLLM_DOCS_SCREENSHOT_REPORT"):
        report_path = Path(os.environ["ANCESTRYLLM_DOCS_SCREENSHOT_REPORT"])
    try:
        temporary_root = resolve_temporary_root(
            repository_root=args.repository_root,
            explicit=args.temporary_root,
            runner_temp=os.environ.get("RUNNER_TEMP"),
            host_platform=sys.platform,
            system_temp=Path(tempfile.gettempdir()),
        )
        manifest_path = _selected_manifest_path(
            args.manifest,
            repository_root=args.repository_root,
        )
        manifest = load_manifest(manifest_path, repository_root=args.repository_root)
        if args.command == "capture":
            captured = regenerate_screenshots(
                manifest,
                repository_root=args.repository_root,
                temporary_root=temporary_root,
                manifest_path=manifest_path,
                surfaces=tuple(args.surface),
                scenario_ids=tuple(args.scenario),
            )
            result = {"captured": len(captured), "schema_version": 1, "status": "success"}
        else:
            result = check_screenshots(
                manifest,
                repository_root=args.repository_root,
                temporary_root=temporary_root,
                manifest_path=manifest_path,
                report_path=report_path,
                surfaces=tuple(args.surface),
                scenario_ids=tuple(args.scenario),
            )
    except (DocsScreenshotError, ScreenshotManifestError, TerminalCaptureError) as error:
        print(error.code, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
