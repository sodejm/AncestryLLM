#!/usr/bin/env python3
"""Audit every locked dependency represented by uv's complete export."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

EXPORT_ARGUMENTS = ("export", "--locked", "--all-extras", "--all-groups")
ALLOWLIST_SCHEMA_VERSION = 1
_NORMALIZE_NAME = re.compile(r"[-_.]+")
_MARKER_VARIABLE = (
    r"(?:implementation_name|implementation_version|os_name|platform_machine|"
    r"platform_python_implementation|platform_release|platform_system|"
    r"platform_version|python_full_version|python_version|sys_platform|extra)"
)
_MARKER_OPERATOR = r"(?:===|==|!=|<=|>=|~=|<|>|not in|in)"
_MARKER_VALUE = r"(?:'[^'\r\n]*'|\"[^\"\r\n]*\")"
_MARKER_TERM = (
    rf"(?:{_MARKER_VARIABLE} {_MARKER_OPERATOR} {_MARKER_VALUE}|"
    rf"{_MARKER_VALUE} {_MARKER_OPERATOR} {_MARKER_VARIABLE})"
)
_MARKER_EXPRESSION = rf"{_MARKER_TERM}(?: (?:and|or) {_MARKER_TERM})*"
_PINNED_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^]]+\])?=="
    r"(?P<version>[^\s;\\]+)"
    rf"(?: ; {_MARKER_EXPRESSION})? \\"
)
_HASH_CONTINUATION = re.compile(r"^ {4}--hash=sha256:[0-9a-f]{64}(?: \\)?$")


class DependencyAuditError(RuntimeError):
    """A stable coded dependency-audit contract failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AuditExclusion:
    """One formally unrepresentable uv.lock package."""

    name: str
    source: dict[str, str]
    export_requirement: str
    reason: str


@dataclass(frozen=True)
class LockedPackage:
    """The lock fields needed for export parity."""

    name: str
    version: str
    source: dict[str, str]


@dataclass(frozen=True)
class PreparedAuditRequirements:
    """A parity-checked requirements document suitable for pip-audit."""

    requirements_text: str
    exported_pairs: frozenset[tuple[str, str]]
    excluded_pairs: frozenset[tuple[str, str]]


def _normalize_package_name(name: str) -> str:
    return _NORMALIZE_NAME.sub("-", name).lower()


def load_exclusions(path: Path) -> tuple[AuditExclusion, ...]:
    """Load the closed-schema audit-export exclusion allowlist."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_INVALID",
            "The dependency-audit exclusion file is not valid UTF-8 JSON.",
        ) from error

    if not isinstance(payload, dict):
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_FIELDS_INVALID",
            "The dependency-audit exclusion document must be an object.",
        )

    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != ALLOWLIST_SCHEMA_VERSION:
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_SCHEMA_UNSUPPORTED",
            "The dependency-audit exclusion schema version is unsupported.",
        )
    if set(payload) != {"schema_version", "excluded_lock_packages"}:
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_FIELDS_INVALID",
            "The dependency-audit exclusion document has missing or unknown fields.",
        )

    raw_entries = payload["excluded_lock_packages"]
    if not isinstance(raw_entries, list):
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_FIELDS_INVALID",
            "The excluded_lock_packages field must be a list.",
        )

    exclusions: list[AuditExclusion] = []
    seen_names: set[str] = set()
    seen_requirements: set[str] = set()
    expected_fields = {"name", "source", "export_requirement", "reason"}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_fields:
            raise DependencyAuditError(
                "DEPAUDIT_ALLOWLIST_ENTRY_INVALID",
                "An audit exclusion has missing or unknown fields.",
            )
        name = raw_entry["name"]
        source = raw_entry["source"]
        export_requirement = raw_entry["export_requirement"]
        reason = raw_entry["reason"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(source, dict)
            or source != {"editable": "."}
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in source.items()
            )
            or export_requirement != "-e ."
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise DependencyAuditError(
                "DEPAUDIT_ALLOWLIST_ENTRY_INVALID",
                "An audit exclusion is not the supported editable workspace-root form.",
            )
        normalized_name = _normalize_package_name(name)
        if normalized_name in seen_names or export_requirement in seen_requirements:
            raise DependencyAuditError(
                "DEPAUDIT_ALLOWLIST_ENTRY_INVALID",
                "Audit exclusions must have unique names and export requirements.",
            )
        seen_names.add(normalized_name)
        seen_requirements.add(export_requirement)
        exclusions.append(
            AuditExclusion(
                name=normalized_name,
                source=dict(source),
                export_requirement=export_requirement,
                reason=reason,
            )
        )
    return tuple(exclusions)


def _load_locked_packages(path: Path) -> tuple[LockedPackage, ...]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise DependencyAuditError(
            "DEPAUDIT_LOCK_INVALID",
            "The uv lockfile is not valid UTF-8 TOML.",
        ) from error

    if payload.get("version") != 1 or not isinstance(payload.get("package"), list):
        raise DependencyAuditError(
            "DEPAUDIT_LOCK_INVALID",
            "The uv lockfile schema is unsupported or incomplete.",
        )

    packages: list[LockedPackage] = []
    for raw_package in payload["package"]:
        if not isinstance(raw_package, dict):
            raise DependencyAuditError(
                "DEPAUDIT_LOCK_INVALID",
                "The uv lockfile contains an invalid package record.",
            )
        name = raw_package.get("name")
        version = raw_package.get("version")
        source = raw_package.get("source")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(source, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in source.items()
            )
        ):
            raise DependencyAuditError(
                "DEPAUDIT_LOCK_INVALID",
                "A uv lockfile package is missing its name, version, or source.",
            )
        packages.append(
            LockedPackage(
                name=_normalize_package_name(name),
                version=version,
                source=dict(source),
            )
        )
    return tuple(packages)


def prepare_audit_requirements(
    lock_path: Path,
    export_text: str,
    exclusions: tuple[AuditExclusion, ...],
) -> PreparedAuditRequirements:
    """Verify exact lock/export parity and remove only approved editable entries."""
    packages = _load_locked_packages(lock_path)
    locked_pairs = {(package.name, package.version) for package in packages}
    packages_by_name: dict[str, list[LockedPackage]] = {}
    for package in packages:
        packages_by_name.setdefault(package.name, []).append(package)

    exclusion_by_requirement = {exclusion.export_requirement: exclusion for exclusion in exclusions}
    excluded_pairs: set[tuple[str, str]] = set()
    for exclusion in exclusions:
        matches = [
            package
            for package in packages_by_name.get(exclusion.name, [])
            if package.source == exclusion.source
        ]
        if len(matches) != 1:
            raise DependencyAuditError(
                "DEPAUDIT_ALLOWLIST_LOCK_MISMATCH",
                "An audit exclusion does not match exactly one lockfile package and source.",
            )
        excluded_pairs.add((matches[0].name, matches[0].version))

    filtered_lines: list[str] = []
    exported_pairs: set[tuple[str, str]] = set()
    seen_exclusions: set[str] = set()
    for line in export_text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped in exclusion_by_requirement:
            if stripped in seen_exclusions:
                raise DependencyAuditError(
                    "DEPAUDIT_EXPORT_UNSUPPORTED_ENTRY",
                    "The uv export repeats an allowlisted editable requirement.",
                )
            seen_exclusions.add(stripped)
            continue
        if not stripped or stripped.startswith("#"):
            filtered_lines.append(line)
            continue
        if line[:1].isspace():
            line_body = line.rstrip("\r\n")
            if _HASH_CONTINUATION.fullmatch(line_body) is None:
                raise DependencyAuditError(
                    "DEPAUDIT_EXPORT_UNSUPPORTED_ENTRY",
                    "The uv export contains an unapproved indented entry.",
                )
            filtered_lines.append(line)
            continue
        match = _PINNED_REQUIREMENT.fullmatch(stripped)
        if match is None:
            raise DependencyAuditError(
                "DEPAUDIT_EXPORT_UNSUPPORTED_ENTRY",
                "The uv export contains an unpinned or unapproved top-level entry.",
            )
        exported_pair = (
            _normalize_package_name(match.group("name")),
            match.group("version"),
        )
        if exported_pair in exported_pairs:
            raise DependencyAuditError(
                "DEPAUDIT_EXPORT_DUPLICATE",
                "The uv export repeats a pinned package requirement.",
            )
        exported_pairs.add(exported_pair)
        filtered_lines.append(line)

    if seen_exclusions != set(exclusion_by_requirement):
        raise DependencyAuditError(
            "DEPAUDIT_ALLOWLIST_EXPORT_MISMATCH",
            "An approved lockfile exclusion was not emitted by the complete uv export.",
        )

    expected_pairs = locked_pairs - excluded_pairs
    if exported_pairs != expected_pairs:
        missing_count = len(expected_pairs - exported_pairs)
        extra_count = len(exported_pairs - expected_pairs)
        raise DependencyAuditError(
            "DEPAUDIT_EXPORT_LOCK_MISMATCH",
            "The complete uv export does not match uv.lock "
            f"(missing={missing_count}, extra={extra_count}).",
        )

    return PreparedAuditRequirements(
        requirements_text="".join(filtered_lines),
        exported_pairs=frozenset(exported_pairs),
        excluded_pairs=frozenset(excluded_pairs),
    )


def run_export(uv_path: Path, root: Path) -> str:
    """Run the one permitted complete locked uv export command."""
    try:
        result = subprocess.run(  # noqa: S603
            (str(uv_path), *EXPORT_ARGUMENTS),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise DependencyAuditError(
            "DEPAUDIT_EXPORT_FAILED",
            "The verified uv executable could not run the dependency export.",
        ) from error
    if result.returncode != 0:
        raise DependencyAuditError(
            "DEPAUDIT_EXPORT_FAILED",
            "The complete locked uv export failed.",
        )
    return result.stdout


def build_pip_audit_command(requirements_path: Path) -> tuple[str, ...]:
    """Build the fail-closed pip-audit command for the hash-locked export."""
    return (
        sys.executable,
        "-m",
        "pip_audit",
        "--strict",
        "--require-hashes",
        "--disable-pip",
        "-r",
        str(requirements_path),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path, default=Path("uv.lock"))
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path("config/dependency-audit-exclusions.json"),
    )
    parser.add_argument(
        "--parity-only",
        action="store_true",
        help="Verify lock/export parity without invoking pip-audit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify export parity, then run pip-audit from the locked security group."""
    args = _parser().parse_args(argv)
    root = Path.cwd()
    try:
        exclusions = load_exclusions(args.allowlist)
        export_text = run_export(args.uv, root)
        prepared = prepare_audit_requirements(args.lockfile, export_text, exclusions)
    except DependencyAuditError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 2

    print(
        "DEPAUDIT_PARITY_OK: "
        f"exported={len(prepared.exported_pairs)} excluded={len(prepared.excluded_pairs)}"
    )
    if args.parity_only:
        return 0

    with tempfile.TemporaryDirectory(prefix="ancestryllm-dependency-audit-") as temp_dir:
        requirements_path = Path(temp_dir) / "requirements.txt"
        requirements_path.write_text(prepared.requirements_text, encoding="utf-8")
        try:
            result = subprocess.run(  # noqa: S603
                build_pip_audit_command(requirements_path),
                cwd=root,
                check=False,
            )
        except OSError as error:
            print(
                "DEPAUDIT_AUDIT_FAILED: pip-audit could not be executed.",
                file=sys.stderr,
            )
            raise SystemExit(2) from error
    if result.returncode != 0:
        print(
            f"DEPAUDIT_AUDIT_FAILED: pip-audit exited with status {result.returncode}.",
            file=sys.stderr,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
