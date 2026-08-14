#!/usr/bin/env python3
"""Validate and normalize deterministic documentation screenshot plans."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

if __package__:
    from scripts.docs_linking import source_anchors
else:
    from docs_linking import source_anchors

SCHEMA_VERSION = 1
_MANIFEST_SCHEMA = "config/docs-screenshot-manifest-v1.schema.json"
_FIXTURE_SCHEMA = "config/docs-screenshot-fixture-v1.schema.json"
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SHELL_METACHARACTERS = re.compile(r"[|;&><`]|\$\(")
_FORBIDDEN_LAUNCHERS = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "curl",
        "fish",
        "powershell",
        "powershell.exe",
        "pwsh",
        "sh",
        "wget",
        "zsh",
    }
)
_REQUIRED_FIXTURE_STATES = frozenset({"success", "degraded", "privacy-canary"})


class ScreenshotManifestError(ValueError):
    """A screenshot-contract failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ValidatedManifest:
    """Validated screenshot plan plus privacy canaries kept out of plan output."""

    payload: dict[str, Any]
    privacy_canaries: tuple[str, ...]

    @property
    def schema_version(self) -> int:
        """Return the validated schema version."""
        return int(self.payload["schema_version"])

    @property
    def fixtures(self) -> tuple[dict[str, Any], ...]:
        """Return the validated fixture descriptors."""
        return tuple(self.payload["fixtures"])

    @property
    def scenarios(self) -> tuple[dict[str, Any], ...]:
        """Return the validated capture scenarios."""
        return tuple(self.payload["scenarios"])


def _fail(code: str, message: str) -> None:
    raise ScreenshotManifestError(code, message)


def _read_json(path: Path, code: str, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code, f"{label} is missing or is not valid UTF-8 JSON")


def _validate_schema(instance: Any, schema: Any, code: str, label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except SchemaError:
        _fail(code, f"{label} schema is invalid")
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        _fail(code, f"{label} does not satisfy its closed schema at {location}")


def _safe_repository_path(
    raw_path: str,
    *,
    repository_root: Path,
    prefix: str,
    suffix: str,
    code: str,
) -> Path:
    if (
        not raw_path
        or "\\" in raw_path
        or raw_path.startswith("/")
        or _WINDOWS_DRIVE.match(raw_path)
        or any(part in {"", ".", ".."} for part in raw_path.split("/"))
    ):
        _fail(code, "path must be a normalized repository-relative path")

    relative = PurePosixPath(raw_path)
    required_prefix = PurePosixPath(prefix)
    if (
        relative.parts[: len(required_prefix.parts)] != required_prefix.parts
        or relative.suffix.casefold() != suffix.casefold()
    ):
        _fail(code, f"path must stay below {prefix} and use the {suffix} suffix")

    resolved_root = repository_root.resolve()
    candidate = repository_root.joinpath(*relative.parts)
    current = repository_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            _fail(code, "symlinked path components are not permitted")
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        _fail(code, "path could not be resolved safely")
    if not resolved_candidate.is_relative_to(resolved_root):
        _fail(code, "path escapes the repository root")
    return candidate


def _validate_launch_allowlist(payload: dict[str, Any]) -> frozenset[str]:
    allowlist = frozenset(payload["launch_allowlist"])
    for executable in allowlist:
        basename = PurePosixPath(executable).name.casefold()
        if (
            basename in _FORBIDDEN_LAUNCHERS
            or _URL.search(executable)
            or _SHELL_METACHARACTERS.search(executable)
        ):
            _fail("DOCSHOT_LAUNCH_UNSAFE", "launch allowlist contains an unsafe executable")
    return allowlist


def _validate_launch(launch: list[str], allowlist: frozenset[str]) -> None:
    executable = launch[0]
    if executable not in allowlist:
        _fail("DOCSHOT_LAUNCH_UNSAFE", "scenario executable is not allowlisted")
    for token in launch:
        if (
            "\x00" in token
            or "\n" in token
            or "\r" in token
            or _URL.search(token)
            or _SHELL_METACHARACTERS.search(token)
        ):
            _fail("DOCSHOT_LAUNCH_UNSAFE", "scenario launch contains shell or URL syntax")


def _validate_fixture_descriptors(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    fixture_schema: Any,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    fixture_by_id: dict[str, dict[str, Any]] = {}
    casefolded_ids: set[str] = set()
    canaries: list[str] = []

    for descriptor in payload["fixtures"]:
        fixture_id = descriptor["id"]
        normalized_id = fixture_id.casefold()
        if normalized_id in casefolded_ids:
            _fail("DOCSHOT_FIXTURE_ID_DUPLICATE", "fixture IDs must be case-insensitively unique")
        casefolded_ids.add(normalized_id)

        if descriptor["provider"] != "none":
            _fail("DOCSHOT_PROVIDER_NOT_NONE", "documentation fixtures must use provider none")
        if descriptor["network"] != "disabled":
            _fail("DOCSHOT_NETWORK_NOT_DENIED", "documentation fixture network must be disabled")
        if descriptor["fictional"] is not True:
            _fail("DOCSHOT_FIXTURE_NOT_FICTIONAL", "documentation fixtures must be fictional")

        fixture_path = _safe_repository_path(
            descriptor["path"],
            repository_root=repository_root,
            prefix="tests/fixtures/docs_screenshots",
            suffix=".json",
            code="DOCSHOT_FIXTURE_PATH_UNSAFE",
        )
        fixture = _read_json(
            fixture_path,
            "DOCSHOT_FIXTURE_SCHEMA_INVALID",
            f"fixture {fixture_id}",
        )
        _validate_schema(
            fixture,
            fixture_schema,
            "DOCSHOT_FIXTURE_SCHEMA_INVALID",
            f"fixture {fixture_id}",
        )
        expected_identity = {
            "fixture_id": fixture_id,
            "state": descriptor["state"],
            "provider": descriptor["provider"],
            "network": descriptor["network"],
            "fictional": descriptor["fictional"],
        }
        if any(fixture[field] != value for field, value in expected_identity.items()):
            _fail("DOCSHOT_FIXTURE_MISMATCH", "fixture identity differs from its descriptor")

        if fixture["provider"] != "none":
            _fail("DOCSHOT_PROVIDER_NOT_NONE", "fixture document must use provider none")
        if fixture["network"] != "disabled":
            _fail("DOCSHOT_NETWORK_NOT_DENIED", "fixture document network must be disabled")
        if fixture["fictional"] is not True:
            _fail("DOCSHOT_FIXTURE_NOT_FICTIONAL", "fixture document must be fictional")

        serialized_content = json.dumps(fixture["content"], ensure_ascii=False, sort_keys=True)
        for canary in fixture["privacy_canaries"]:
            if canary not in serialized_content:
                _fail(
                    "DOCSHOT_PRIVACY_CANARY_INVALID",
                    "privacy canary must occur in the validation-only fixture content",
                )
            canaries.append(canary)
        fixture_by_id[fixture_id] = fixture

    states = {fixture["state"] for fixture in fixture_by_id.values()}
    if states != _REQUIRED_FIXTURE_STATES:
        _fail(
            "DOCSHOT_FIXTURE_STATE_INCOMPLETE",
            "success, degraded, and privacy-canary fixture states are required",
        )
    return fixture_by_id, tuple(sorted(canaries))


def _validate_scenarios(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    fixture_by_id: dict[str, dict[str, Any]],
) -> None:
    launch_allowlist = _validate_launch_allowlist(payload)
    scenario_ids: set[str] = set()
    output_paths: set[str] = set()
    declared_outputs: set[str] = set()

    for scenario in payload["scenarios"]:
        normalized_id = scenario["id"].casefold()
        if normalized_id in scenario_ids:
            _fail(
                "DOCSHOT_SCENARIO_ID_DUPLICATE",
                "scenario IDs must be case-insensitively unique",
            )
        scenario_ids.add(normalized_id)

        normalized_output = scenario["output_path"].casefold()
        if normalized_output in output_paths:
            _fail(
                "DOCSHOT_OUTPUT_DUPLICATE",
                "scenario destinations must be case-insensitively unique",
            )
        output_paths.add(normalized_output)
        _safe_repository_path(
            scenario["output_path"],
            repository_root=repository_root,
            prefix="docs/assets/screenshots",
            suffix=".png",
            code="DOCSHOT_OUTPUT_PATH_UNSAFE",
        )
        declared_outputs.add(scenario["output_path"])

        _validate_launch(scenario["launch"], launch_allowlist)
        expected_geometry = "viewport" if scenario["surface"] == "electron" else "terminal"
        if scenario["geometry"]["kind"] != expected_geometry:
            _fail(
                "DOCSHOT_GEOMETRY_MISMATCH",
                "scenario geometry does not match its capture surface",
            )

        fixture_id = scenario["fixture_id"]
        fixture = fixture_by_id.get(fixture_id)
        if fixture is None:
            _fail("DOCSHOT_FIXTURE_NOT_FOUND", "scenario references an unknown fixture")
        if fixture["state"] == "privacy-canary":
            _fail(
                "DOCSHOT_PRIVACY_FIXTURE_REFERENCED",
                "privacy-canary fixtures cannot be used by publishable scenarios",
            )

        for reference in scenario["documentation"]:
            documentation_path = _safe_repository_path(
                reference["path"],
                repository_root=repository_root,
                prefix="docs",
                suffix=".md",
                code="DOCSHOT_DOC_PATH_UNSAFE",
            )
            if not documentation_path.is_file():
                _fail("DOCSHOT_DOC_NOT_FOUND", "referenced documentation file does not exist")
            try:
                markdown = documentation_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                _fail("DOCSHOT_DOC_NOT_FOUND", "referenced documentation file is unreadable")
            if reference["anchor"] not in source_anchors(markdown):
                _fail(
                    "DOCSHOT_DOC_ANCHOR_MISSING",
                    "referenced documentation anchor does not exist",
                )

    output_allowlist = payload["output_allowlist"]
    normalized_allowlist = [output.casefold() for output in output_allowlist]
    if len(set(normalized_allowlist)) != len(normalized_allowlist):
        _fail(
            "DOCSHOT_OUTPUT_DUPLICATE",
            "output allowlist destinations must be case-insensitively unique",
        )
    allowlisted_outputs = set(output_allowlist)
    for output_path in allowlisted_outputs:
        _safe_repository_path(
            output_path,
            repository_root=repository_root,
            prefix="docs/assets/screenshots",
            suffix=".png",
            code="DOCSHOT_OUTPUT_PATH_UNSAFE",
        )
    if declared_outputs != allowlisted_outputs:
        _fail(
            "DOCSHOT_OUTPUT_ALLOWLIST_MISMATCH",
            "scenario destinations must exactly match the output allowlist",
        )


def validate_manifest(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    schema_path: Path,
    fixture_schema_path: Path,
) -> ValidatedManifest:
    """Validate a screenshot manifest and every referenced fixture and document."""
    if type(payload) is not dict or type(payload.get("schema_version")) is not int:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest schema_version must be integer 1")
    if payload["schema_version"] != SCHEMA_VERSION:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest schema_version is not supported")

    manifest_schema = _read_json(schema_path, "DOCSHOT_SCHEMA_INVALID", "manifest schema")
    fixture_schema = _read_json(
        fixture_schema_path,
        "DOCSHOT_FIXTURE_SCHEMA_INVALID",
        "fixture schema",
    )
    _validate_schema(payload, manifest_schema, "DOCSHOT_SCHEMA_INVALID", "manifest")
    fixture_by_id, canaries = _validate_fixture_descriptors(
        payload,
        repository_root=repository_root,
        fixture_schema=fixture_schema,
    )
    _validate_scenarios(
        payload,
        repository_root=repository_root,
        fixture_by_id=fixture_by_id,
    )
    return ValidatedManifest(payload=deepcopy(payload), privacy_canaries=canaries)


def load_manifest(path: Path, *, repository_root: Path) -> ValidatedManifest:
    """Load and validate the schema-v1 manifest from a repository root."""
    manifest_path = path if path.is_absolute() else repository_root / path
    payload = _read_json(manifest_path, "DOCSHOT_SCHEMA_INVALID", "manifest")
    if type(payload) is not dict:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest root must be an object")
    return validate_manifest(
        payload,
        repository_root=repository_root,
        schema_path=repository_root / _MANIFEST_SCHEMA,
        fixture_schema_path=repository_root / _FIXTURE_SCHEMA,
    )


def normalized_plan_json(manifest: ValidatedManifest) -> str:
    """Serialize a deterministic capture plan without privacy-canary fixture content."""
    referenced_fixture_ids = {scenario["fixture_id"] for scenario in manifest.scenarios}
    plan = {
        "schema_version": manifest.schema_version,
        "determinism": manifest.payload["determinism"],
        "fixtures": sorted(
            (fixture for fixture in manifest.fixtures if fixture["id"] in referenced_fixture_ids),
            key=lambda fixture: fixture["id"],
        ),
        "scenarios": sorted(manifest.scenarios, key=lambda scenario: scenario["id"]),
    }
    return json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_capture_text(manifest: ValidatedManifest, text: str) -> None:
    """Fail closed when textual capture evidence contains a privacy canary."""
    if any(canary in text for canary in manifest.privacy_canaries):
        _fail(
            "DOCSHOT_PRIVACY_CANARY_LEAKED",
            "capture text contains an unpublishable privacy canary",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "validate"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/docs-screenshot-manifest.json"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the validation-only documentation screenshot contract CLI."""
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, repository_root=args.repository_root)
    except ScreenshotManifestError as error:
        print(error.code, file=sys.stderr)
        return 2
    if args.command == "plan":
        print(normalized_plan_json(manifest), end="")
    else:
        print('{"schema_version":1,"status":"valid"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
