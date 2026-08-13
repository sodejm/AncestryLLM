#!/usr/bin/env python3
"""Compare setuptools and uv_build artifacts without changing the production backend."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import hmac
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email import policy
from email.parser import BytesParser
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
UV_VERSION = "0.12.1"
UV_BUILD_REQUIREMENT = "uv_build>=0.12.0,<0.13"
SETUPTOOLS_REQUIREMENT = "setuptools>=83"
PRODUCTION_BUILD_SYSTEM = (
    '[build-system]\nrequires = ["setuptools>=83"]\nbuild-backend = "setuptools.build_meta"\n'
)
CANDIDATE_BUILD_SYSTEM = (
    '[build-system]\nrequires = ["uv_build>=0.12.0,<0.13"]\nbuild-backend = "uv_build"\n'
)
CANDIDATE_SOURCE_INCLUDES = [
    "CHANGELOG.md",
    "MANIFEST.in",
    "docs/reference/CLI.md",
    "docs/CONSOLE.md",
    "docs/reference/FILE_INGRESS.md",
    "docs/reference/GEDCOM_COMPATIBILITY.md",
    "docs/reference/PROVIDERS.md",
    "docs/RELEASING.md",
    "docs/SETUP_DIAGNOSTICS.md",
    "docs/reference/VERSIONING.md",
    "docs/release-evidence/README.md",
    "docs/release-evidence/issue-10-import-smoke-tests.md",
]
ALLOWED_SDIST_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST.in",
    "PKG-INFO",
    "README.md",
    *CANDIDATE_SOURCE_INCLUDES[2:],
    "pyproject.toml",
    "setup.cfg",
}
ALLOWED_SDIST_PREFIXES = ("src/ancestryllm/", "src/ancestryllm.egg-info/")
REQUIRED_SDIST_PATHS = {
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    *CANDIDATE_SOURCE_INCLUDES[2:],
    "pyproject.toml",
    "src/ancestryllm/__init__.py",
    "src/ancestryllm/cli.py",
    "src/ancestryllm/storage/migrations/versions/0001_initial.py",
    "src/ancestryllm/storage/migrations/versions/0002_job_persistence.py",
}
BLOCKED_ARCHIVE_PARTS = {
    ".env",
    ".git",
    ".github",
    "family_trees",
    "scripts",
    "tests",
}
PROJECT_FILE_PATHS = {
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    *CANDIDATE_SOURCE_INCLUDES[2:],
}
DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
UV_VERSION_OUTPUT = re.compile(r"^uv (?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?: \([^()\r\n]+\))?$")
INSTALL_SMOKE_CHECK = """\
from importlib.metadata import distribution
from pathlib import Path
import sys

target = Path(sys.argv[1]).resolve()
expected_version = sys.argv[2]
sys.path.insert(0, str(target))

installed = distribution("ancestryllm")
assert installed.version == expected_version
entrypoints = [
    entrypoint
    for entrypoint in installed.entry_points
    if entrypoint.group == "console_scripts" and entrypoint.name == "ancestry"
]
assert [entrypoint.value for entrypoint in entrypoints] == ["ancestryllm.cli:main"]

import ancestryllm

assert Path(ancestryllm.__file__).resolve().is_relative_to(target)
main = entrypoints[0].load()
sys.argv = ["ancestry", "--help"]
try:
    result = main()
except SystemExit as error:
    if error.code not in (0, None):
        raise
else:
    assert result in (0, None)
"""
ACCEPTED_INPUT_DIFFERENCES = [
    {
        "code": "UVBEVAL_CANDIDATE_BUILD_CONFIGURATION",
        "paths": ["pyproject.toml"],
    }
]
ACCEPTED_OUTPUT_NORMALIZATIONS = [
    {
        "code": "UVBEVAL_ARCHIVE_MEMBER_ORDER",
        "scope": ["sdist", "wheel"],
    },
    {
        "code": "UVBEVAL_ARCHIVE_METADATA_TIMESTAMPS",
        "scope": ["sdist", "wheel"],
    },
]
FILE_MAP_COMPARISONS = {
    "entry_points",
    "migration_paths",
    "module_paths",
    "package_data_paths",
    "project_files",
    "project_metadata",
    "record",
    "sdist_payload_except_controlled_build_configuration",
    "setuptools_sdist_reconstruction",
    "uv_build_sdist_reconstruction",
    "wheel_archive_contents",
    "wheel_descriptor",
    "wheel_metadata",
    "wheel_package_payload",
}
BOOLEAN_COMPARISONS = {
    "setuptools_install_import_entrypoint",
    "uv_build_install_import_entrypoint",
    "uv_build_reproducibility",
}
ALLOWLIST_COMPARISONS = {
    "setuptools_allowlists",
    "uv_build_allowlists",
}
COMPARISON_NAMES = FILE_MAP_COMPARISONS | BOOLEAN_COMPARISONS | ALLOWLIST_COMPARISONS
ARTIFACT_SETS = {
    "setuptools": {"sdist", "wheel"},
    "setuptools_from_sdist": {"wheel"},
    "uv_build_first": {"sdist", "wheel"},
    "uv_build_from_sdist": {"wheel"},
    "uv_build_second": {"sdist", "wheel"},
}


class EvaluationError(RuntimeError):
    """A stable coded evaluation failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_uv_version(output: str) -> None:
    """Require the pinned uv release while accepting its official build annotation."""
    match = UV_VERSION_OUTPUT.fullmatch(output)
    if match is None or match.group("version") != UV_VERSION:
        raise EvaluationError(
            "UVBEVAL_UV_VERSION",
            f"expected uv {UV_VERSION}, received {output!r}",
        )


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed evaluation commands; no shell
            command,
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = "\n".join(part.strip() for part in (error.stdout, error.stderr) if part.strip())
        raise EvaluationError(
            "UVBEVAL_COMMAND_FAILED",
            f"evaluation command failed: {Path(command[0]).name}\n{detail}",
        ) from error
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or DRIVE_PATH.match(name):
        raise EvaluationError("UVBEVAL_UNSAFE_ARCHIVE", f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvaluationError("UVBEVAL_UNSAFE_ARCHIVE", f"unsafe archive member: {name!r}")
    return path.parts


def read_zip_files(path: Path) -> dict[str, bytes]:
    """Read regular wheel members after validating their lexical paths."""
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                _safe_parts(member.filename.rstrip("/"))
                if member.is_dir():
                    continue
                if member.filename in files:
                    raise EvaluationError(
                        "UVBEVAL_UNSAFE_ARCHIVE",
                        f"duplicate archive member: {member.filename}",
                    )
                files[member.filename] = archive.read(member)
    except zipfile.BadZipFile as error:
        raise EvaluationError(
            "UVBEVAL_INVALID_WHEEL", "wheel is not a valid ZIP archive"
        ) from error
    return files


def _read_tar_files(path: Path, *, strip_single_root: bool) -> dict[str, bytes]:
    raw: dict[str, bytes] = {}
    roots: set[str] = set()
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                parts = _safe_parts(member.name.rstrip("/"))
                roots.add(parts[0])
                if member.isdir():
                    continue
                if not member.isfile():
                    raise EvaluationError(
                        "UVBEVAL_UNSAFE_ARCHIVE",
                        f"unsafe archive member type: {member.name}",
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise EvaluationError(
                        "UVBEVAL_INVALID_SDIST",
                        f"could not read sdist member: {member.name}",
                    )
                if member.name in raw:
                    raise EvaluationError(
                        "UVBEVAL_UNSAFE_ARCHIVE",
                        f"duplicate archive member: {member.name}",
                    )
                raw[member.name] = extracted.read()
    except tarfile.TarError as error:
        raise EvaluationError(
            "UVBEVAL_INVALID_SDIST", "sdist is not a valid tar archive"
        ) from error

    if not strip_single_root:
        return raw
    if len(roots) != 1:
        raise EvaluationError(
            "UVBEVAL_INVALID_SDIST",
            "sdist must contain exactly one top-level directory",
        )
    root = next(iter(roots))
    prefix = f"{root}/"
    if any(not name.startswith(prefix) for name in raw):
        raise EvaluationError(
            "UVBEVAL_INVALID_SDIST",
            "sdist file is outside its top-level directory",
        )
    return {name.removeprefix(prefix): data for name, data in raw.items()}


def read_sdist_files(path: Path) -> dict[str, bytes]:
    """Read regular sdist members with their single root directory removed."""
    return _read_tar_files(path, strip_single_root=True)


def _extract_tar(path: Path, destination: Path, *, strip_single_root: bool) -> Path:
    files = _read_tar_files(path, strip_single_root=strip_single_root)
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in sorted(files.items()):
        output = destination.joinpath(*PurePosixPath(relative).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    return destination


def candidate_pyproject(source: str) -> str:
    """Return the exact reviewed candidate overlay without mutating the source tree."""
    if source.count(PRODUCTION_BUILD_SYSTEM) != 1:
        raise EvaluationError(
            "UVBEVAL_BUILD_CONFIGURATION",
            "production build-system declaration is missing or ambiguous",
        )
    if "[tool.uv.build-backend]" in source:
        raise EvaluationError(
            "UVBEVAL_BUILD_CONFIGURATION",
            "candidate backend configuration already exists in production pyproject.toml",
        )
    quoted_includes = "\n".join(f'    "{path}",' for path in CANDIDATE_SOURCE_INCLUDES)
    candidate = source.replace(PRODUCTION_BUILD_SYSTEM, CANDIDATE_BUILD_SYSTEM, 1)
    candidate += f"\n[tool.uv.build-backend]\nsource-include = [\n{quoted_includes}\n]\n"
    try:
        parsed = tomllib.loads(candidate)
    except tomllib.TOMLDecodeError as error:
        raise EvaluationError(
            "UVBEVAL_BUILD_CONFIGURATION",
            "candidate pyproject.toml is invalid",
        ) from error
    if parsed["build-system"] != {
        "requires": [UV_BUILD_REQUIREMENT],
        "build-backend": "uv_build",
    }:
        raise EvaluationError(
            "UVBEVAL_BUILD_CONFIGURATION",
            "candidate build-system declaration does not match policy",
        )
    return candidate


def compare_file_maps(
    baseline: dict[str, bytes], candidate: dict[str, bytes]
) -> dict[str, list[str]]:
    baseline_paths = set(baseline)
    candidate_paths = set(candidate)
    return {
        "added": sorted(candidate_paths - baseline_paths),
        "changed": sorted(
            path for path in baseline_paths & candidate_paths if baseline[path] != candidate[path]
        ),
        "removed": sorted(baseline_paths - candidate_paths),
    }


def semantic_message(data: bytes) -> dict[str, Any]:
    """Normalize only RFC header ordering, preserving every value and the body."""
    message = BytesParser(policy=policy.compat32).parsebytes(data)
    headers = sorted((name.lower(), str(value)) for name, value in message.items())
    payload = message.get_payload(decode=True)
    if payload is None:
        raw_payload = message.get_payload()
        body = raw_payload if isinstance(raw_payload, str) else str(raw_payload)
    else:
        body = payload.decode("utf-8")
    return {"body": body, "headers": [list(item) for item in headers]}


def semantic_entry_points(data: bytes) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as error:
        raise EvaluationError(
            "UVBEVAL_INVALID_ENTRYPOINTS",
            "entry_points.txt is invalid",
        ) from error
    return {section: dict(sorted(parser.items(section))) for section in sorted(parser.sections())}


def _record_rows(files: dict[str, bytes]) -> dict[str, tuple[str, str]]:
    names = [name for name in files if name.endswith(".dist-info/RECORD")]
    if len(names) != 1:
        raise EvaluationError(
            "UVBEVAL_INVALID_RECORD",
            "wheel must contain exactly one RECORD file",
        )
    record_name = names[0]
    try:
        rows = list(csv.reader(io.StringIO(files[record_name].decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as error:
        raise EvaluationError("UVBEVAL_INVALID_RECORD", "wheel RECORD is invalid") from error
    records: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in records:
            raise EvaluationError("UVBEVAL_INVALID_RECORD", "wheel RECORD rows are invalid")
        records[row[0]] = (row[1], row[2])
    if set(records) != set(files):
        raise EvaluationError(
            "UVBEVAL_INVALID_RECORD",
            "wheel RECORD paths do not cover the complete wheel",
        )
    if records[record_name] != ("", ""):
        raise EvaluationError(
            "UVBEVAL_INVALID_RECORD",
            "wheel RECORD must leave its own digest and size empty",
        )
    for name, data in files.items():
        if name == record_name:
            continue
        digest, size = records[name]
        expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(digest, f"sha256={expected}") or size != str(len(data)):
            raise EvaluationError(
                "UVBEVAL_INVALID_RECORD",
                f"wheel RECORD digest or size mismatch: {name}",
            )
    return records


def validate_record(files: dict[str, bytes]) -> list[str]:
    _record_rows(files)
    return sorted(files)


def _record_semantics(files: dict[str, bytes]) -> dict[str, bytes]:
    return {
        name: f"{digest},{size}".encode() for name, (digest, size) in _record_rows(files).items()
    }


def _single_suffix(files: dict[str, bytes], suffix: str) -> bytes:
    names = [name for name in files if name.endswith(f".dist-info/{suffix}")]
    if len(names) != 1:
        raise EvaluationError(
            "UVBEVAL_INVALID_WHEEL",
            f"wheel must contain exactly one {suffix} file",
        )
    return files[names[0]]


def _semantic_message_files(data: bytes) -> dict[str, bytes]:
    """Expose semantic message fields as reportable paths without values."""
    message = semantic_message(data)
    files = {"body": str(message["body"]).encode()}
    occurrences: dict[str, int] = {}
    for name, value in message["headers"]:
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        files[f"header:{name}[{occurrence}]"] = str(value).encode()
    return files


def _semantic_entry_point_files(data: bytes) -> dict[str, bytes]:
    return {
        f"section:{section}/entry:{name}": value.encode()
        for section, entries in semantic_entry_points(data).items()
        for name, value in entries.items()
    }


def _metadata_contract(data: bytes) -> dict[str, Any]:
    message = BytesParser(policy=policy.compat32).parsebytes(data)
    return {
        "name": str(message["Name"]),
        "version": str(message["Version"]),
        "requires_python": str(message["Requires-Python"]),
        "dependencies": sorted(str(value) for value in message.get_all("Requires-Dist", [])),
        "extras": sorted(str(value) for value in message.get_all("Provides-Extra", [])),
    }


def _metadata_contract_files(data: bytes) -> dict[str, bytes]:
    """Expose project metadata fields and set members as reportable paths."""
    metadata = _metadata_contract(data)
    files = {
        "name": str(metadata["name"]).encode(),
        "version": str(metadata["version"]).encode(),
        "requires-python": str(metadata["requires_python"]).encode(),
    }
    files.update({f"dependency:{value}": b"" for value in metadata["dependencies"]})
    files.update({f"extra:{value}": b"" for value in metadata["extras"]})
    return files


def _wheel_allowlist(files: dict[str, bytes], version: str) -> dict[str, list[str]]:
    distribution_prefix = f"ancestryllm-{version}.dist-info/"
    unexpected = sorted(
        name for name in files if not name.startswith(("ancestryllm/", distribution_prefix))
    )
    blocked = sorted(
        name for name in files if BLOCKED_ARCHIVE_PARTS.intersection(PurePosixPath(name).parts)
    )
    return {"blocked": blocked, "unexpected": unexpected}


def _sdist_allowlist(files: dict[str, bytes]) -> dict[str, list[str]]:
    unexpected = sorted(
        name
        for name in files
        if name not in ALLOWED_SDIST_FILES and not name.startswith(ALLOWED_SDIST_PREFIXES)
    )
    blocked = sorted(
        name for name in files if BLOCKED_ARCHIVE_PARTS.intersection(PurePosixPath(name).parts)
    )
    return {
        "blocked": blocked,
        "missing": sorted(REQUIRED_SDIST_PATHS - set(files)),
        "unexpected": unexpected,
    }


UV_OUTPUT_MARKERS = frozenset({".gitignore"})


def _unexpected_output_entries(directory: Path, artifacts: list[Path]) -> list[str]:
    artifact_names = {path.name for path in artifacts}
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.name not in artifact_names
        and not (path.name in UV_OUTPUT_MARKERS and path.is_file() and not path.is_symlink())
    )


def _artifact_paths(directory: Path) -> tuple[Path, Path]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    others = _unexpected_output_entries(directory, wheels + sdists)
    artifacts = wheels + sdists
    unsafe_artifact = any(not path.is_file() or path.is_symlink() for path in artifacts)
    if len(wheels) != 1 or len(sdists) != 1 or others or unsafe_artifact:
        raise EvaluationError(
            "UVBEVAL_ARTIFACT_SET",
            "build must produce exactly one wheel and one sdist",
        )
    return wheels[0], sdists[0]


def _wheel_only(directory: Path) -> Path:
    wheels = sorted(directory.glob("*.whl"))
    others = _unexpected_output_entries(directory, wheels)
    unsafe_artifact = any(not path.is_file() or path.is_symlink() for path in wheels)
    if len(wheels) != 1 or others or unsafe_artifact:
        raise EvaluationError(
            "UVBEVAL_ARTIFACT_SET",
            "sdist reconstruction must produce exactly one wheel",
        )
    return wheels[0]


def _build(
    uv: Path,
    source: Path,
    output: Path,
    environment: dict[str, str],
    *,
    wheel_only: bool = False,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    command = [
        str(uv),
        "build",
        "--no-build-isolation",
        "--out-dir",
        str(output),
    ]
    if wheel_only:
        command.append("--wheel")
    command.append(str(source))
    _run(command, cwd=source, env=environment)


def _artifact_record(path: Path) -> dict[str, str]:
    return {"filename": path.name, "sha256": _sha256(path)}


def _result(differences: dict[str, list[str]], failure_code: str) -> dict[str, Any]:
    passed = not any(differences.values())
    return {
        "differences": differences,
        "failure_code": failure_code,
        "passed": passed,
    }


def _boolean_result(passed: bool, failure_code: str, differences: list[str]) -> dict[str, Any]:
    return {
        "differences": differences,
        "failure_code": failure_code,
        "passed": passed,
    }


def _add_file_comparison(
    comparisons: dict[str, dict[str, Any]],
    name: str,
    baseline: dict[str, bytes],
    candidate: dict[str, bytes],
    failure_code: str,
) -> None:
    comparisons[name] = _result(compare_file_maps(baseline, candidate), failure_code)


def _extract_source_archive(archive: Path, destination: Path) -> None:
    _extract_tar(archive, destination, strip_single_root=False)


def _extract_sdist(archive: Path, destination: Path) -> Path:
    files = read_sdist_files(archive)
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in sorted(files.items()):
        output = destination.joinpath(*PurePosixPath(relative).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    return destination


def _install_smoke(
    uv: Path,
    wheel: Path,
    version: str,
    destination: Path,
    environment: dict[str, str],
) -> list[str]:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        _run(
            [
                str(uv),
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--python",
                sys.executable,
                "--target",
                str(destination),
                str(wheel),
            ],
            cwd=destination,
            env=environment,
        )
    except EvaluationError:
        return ["install"]
    try:
        _run(
            [
                sys.executable,
                "-I",
                "-c",
                INSTALL_SMOKE_CHECK,
                str(destination),
                version,
            ],
            cwd=destination,
            env=environment,
        )
    except EvaluationError:
        return ["import-metadata-entrypoint-help"]
    return []


def _safe_report_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _safe_report_value(key)
            _safe_report_value(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _safe_report_value(item)
        return
    if not isinstance(value, str):
        return
    if value.startswith("/") or DRIVE_PATH.match(value) or PureWindowsPath(value).is_absolute():
        raise EvaluationError(
            "UVBEVAL_UNSAFE_REPORT",
            "evaluation report contains an absolute local path",
        )
    root = str(ROOT)
    if root in value:
        raise EvaluationError(
            "UVBEVAL_UNSAFE_REPORT",
            "evaluation report contains the repository path",
        )


def _exact_report_fields(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE",
            f"{context} must be an object",
        )
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        raise EvaluationError(
            "UVBEVAL_REPORT_FIELDS",
            f"{context} fields are invalid; missing={missing!r}, unknown={unknown!r}",
        )
    if not all(isinstance(key, str) for key in value):
        raise EvaluationError(
            "UVBEVAL_REPORT_FIELDS",
            f"{context} field names must be strings",
        )
    return value


def _report_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE",
            f"{context} must be a non-empty string",
        )
    return value


def _report_string_list(value: Any, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) for item in value)
        or value != sorted(set(value))
    ):
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE",
            f"{context} must be a sorted list of unique strings",
        )
    return value


def _validate_artifacts(value: Any) -> None:
    artifacts = _exact_report_fields(value, set(ARTIFACT_SETS), "artifacts")
    for build_name, artifact_names in ARTIFACT_SETS.items():
        build = _exact_report_fields(
            artifacts[build_name], artifact_names, f"artifacts.{build_name}"
        )
        for artifact_name, raw_record in build.items():
            record = _exact_report_fields(
                raw_record,
                {"filename", "sha256"},
                f"artifacts.{build_name}.{artifact_name}",
            )
            filename = _report_string(
                record["filename"], f"artifacts.{build_name}.{artifact_name}.filename"
            )
            expected_suffix = ".whl" if artifact_name == "wheel" else ".tar.gz"
            if Path(filename).name != filename or not filename.endswith(expected_suffix):
                raise EvaluationError(
                    "UVBEVAL_REPORT_VALUE",
                    f"artifacts.{build_name}.{artifact_name}.filename is invalid",
                )
            digest = _report_string(
                record["sha256"], f"artifacts.{build_name}.{artifact_name}.sha256"
            )
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise EvaluationError(
                    "UVBEVAL_REPORT_VALUE",
                    f"artifacts.{build_name}.{artifact_name}.sha256 is invalid",
                )


def _validate_comparisons(value: Any) -> dict[str, dict[str, Any]]:
    comparisons = _exact_report_fields(value, COMPARISON_NAMES, "comparisons")
    validated: dict[str, dict[str, Any]] = {}
    for name, raw_result in comparisons.items():
        result = _exact_report_fields(
            raw_result,
            {"differences", "failure_code", "passed"},
            f"comparisons.{name}",
        )
        _report_string(result["failure_code"], f"comparisons.{name}.failure_code")
        if not isinstance(result["passed"], bool):
            raise EvaluationError(
                "UVBEVAL_REPORT_VALUE",
                f"comparisons.{name}.passed must be a boolean",
            )
        differences = result["differences"]
        if name in FILE_MAP_COMPARISONS:
            difference_map = _exact_report_fields(
                differences,
                {"added", "changed", "removed"},
                f"comparisons.{name}.differences",
            )
            for category, paths in difference_map.items():
                _report_string_list(paths, f"comparisons.{name}.differences.{category}")
            has_differences = any(difference_map.values())
        elif name in ALLOWLIST_COMPARISONS:
            difference_map = _exact_report_fields(
                differences,
                {"blocked", "missing", "unexpected", "wheel_blocked", "wheel_unexpected"},
                f"comparisons.{name}.differences",
            )
            for category, paths in difference_map.items():
                _report_string_list(paths, f"comparisons.{name}.differences.{category}")
            has_differences = any(difference_map.values())
        else:
            difference_list = _report_string_list(differences, f"comparisons.{name}.differences")
            has_differences = bool(difference_list)
        if result["passed"] == has_differences:
            raise EvaluationError(
                "UVBEVAL_REPORT_VALUE",
                f"comparisons.{name}.passed does not match its differences",
            )
        validated[name] = result
    return validated


def validate_report(report: Any) -> None:
    """Validate the closed schema-v1 comparison report contract."""
    if not isinstance(report, dict):
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "report must be an object")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError(
            "UVBEVAL_REPORT_SCHEMA",
            f"unsupported report schema: {report.get('schema_version')!r}",
        )
    status = report.get("status")
    if status == "error":
        error_report = _exact_report_fields(
            report,
            {"evaluation", "failure_codes", "schema_version", "status"},
            "report",
        )
        if error_report["evaluation"] != "setuptools-vs-uv_build":
            raise EvaluationError("UVBEVAL_REPORT_VALUE", "report evaluation identity is invalid")
        failure_codes = _report_string_list(error_report["failure_codes"], "failure_codes")
        if len(failure_codes) != 1 or not failure_codes[0].startswith("UVBEVAL_"):
            raise EvaluationError(
                "UVBEVAL_REPORT_VALUE",
                "an error report must contain one stable failure code",
            )
        return
    if status not in {"compatible", "incompatible"}:
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "report status is invalid")
    full_report = _exact_report_fields(
        report,
        {
            "accepted_input_differences",
            "accepted_output_normalizations",
            "artifacts",
            "comparisons",
            "evaluation",
            "failure_codes",
            "schema_version",
            "source",
            "status",
            "tools",
        },
        "report",
    )
    if full_report["evaluation"] != "setuptools-vs-uv_build":
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "report evaluation identity is invalid")
    if full_report["accepted_input_differences"] != ACCEPTED_INPUT_DIFFERENCES:
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE", "accepted input differences do not match schema v1"
        )
    if full_report["accepted_output_normalizations"] != ACCEPTED_OUTPUT_NORMALIZATIONS:
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE", "accepted output normalizations do not match schema v1"
        )
    _validate_artifacts(full_report["artifacts"])
    comparisons = _validate_comparisons(full_report["comparisons"])
    failure_codes = _report_string_list(full_report["failure_codes"], "failure_codes")
    if failure_codes != _comparison_failures(comparisons):
        raise EvaluationError(
            "UVBEVAL_REPORT_VALUE", "failure_codes do not match failed comparisons"
        )
    if (status == "compatible") != (not failure_codes):
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "report status does not match failure_codes")
    source = _exact_report_fields(full_report["source"], {"commit", "source_date_epoch"}, "source")
    if re.fullmatch(r"[0-9a-f]{40}", _report_string(source["commit"], "source.commit")) is None:
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "source.commit is invalid")
    if (
        not isinstance(source["source_date_epoch"], int)
        or isinstance(source["source_date_epoch"], bool)
        or source["source_date_epoch"] < 0
    ):
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "source.source_date_epoch is invalid")
    tools = _exact_report_fields(
        full_report["tools"], {"build_frontend", "python", "setuptools", "uv_build"}, "tools"
    )
    if tools["build_frontend"] != f"uv {UV_VERSION}":
        raise EvaluationError("UVBEVAL_REPORT_VALUE", "tools.build_frontend is invalid")
    _report_string(tools["python"], "tools.python")
    for name, requirement in (
        ("setuptools", SETUPTOOLS_REQUIREMENT),
        ("uv_build", UV_BUILD_REQUIREMENT),
    ):
        tool = _exact_report_fields(tools[name], {"requirement", "version"}, f"tools.{name}")
        if tool["requirement"] != requirement:
            raise EvaluationError("UVBEVAL_REPORT_VALUE", f"tools.{name}.requirement is invalid")
        _report_string(tool["version"], f"tools.{name}.version")


def serialize_report(report: dict[str, Any]) -> str:
    validate_report(report)
    _safe_report_value(report)
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(serialize_report(report), encoding="utf-8")
    temporary.replace(path)


def _comparison_failures(comparisons: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        {str(result["failure_code"]) for result in comparisons.values() if not result["passed"]}
    )


def evaluate(uv: Path) -> dict[str, Any]:
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
    )
    if status:
        raise EvaluationError(
            "UVBEVAL_DIRTY_CHECKOUT",
            "uv_build evaluation requires a clean checkout",
        )
    uv_output = _run([str(uv), "--version"], cwd=ROOT)
    validate_uv_version(uv_output)

    source_commit = _run(["git", "rev-parse", "HEAD"], cwd=ROOT)
    epoch = _run(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT)
    with (ROOT / "pyproject.toml").open("rb") as handle:
        version = str(tomllib.load(handle)["project"]["version"])
    environment = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": epoch,
        "UV_NO_CONFIG": "1",
        "UV_OFFLINE": "1",
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
    }

    with tempfile.TemporaryDirectory(prefix="ancestryllm-uv-build-evaluation-") as name:
        temporary = Path(name)
        source_archive = temporary / "source.tar"
        _run(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={source_archive}",
                source_commit,
            ],
            cwd=ROOT,
        )

        setuptools_source = temporary / "source-setuptools"
        uv_build_source_a = temporary / "source-uv-build-a"
        uv_build_source_b = temporary / "source-uv-build-b"
        for source in (setuptools_source, uv_build_source_a, uv_build_source_b):
            _extract_source_archive(source_archive, source)
        for source in (uv_build_source_a, uv_build_source_b):
            pyproject = source / "pyproject.toml"
            pyproject.write_text(
                candidate_pyproject(pyproject.read_text(encoding="utf-8")),
                encoding="utf-8",
            )

        setuptools_output = temporary / "artifacts-setuptools"
        uv_build_output_a = temporary / "artifacts-uv-build-a"
        uv_build_output_b = temporary / "artifacts-uv-build-b"
        _build(uv, setuptools_source, setuptools_output, environment)
        _build(uv, uv_build_source_a, uv_build_output_a, environment)
        _build(uv, uv_build_source_b, uv_build_output_b, environment)

        setuptools_wheel, setuptools_sdist = _artifact_paths(setuptools_output)
        uv_build_wheel_a, uv_build_sdist_a = _artifact_paths(uv_build_output_a)
        uv_build_wheel_b, uv_build_sdist_b = _artifact_paths(uv_build_output_b)

        setuptools_sdist_source = temporary / "rebuild-source-setuptools"
        uv_build_sdist_source = temporary / "rebuild-source-uv-build"
        _extract_sdist(setuptools_sdist, setuptools_sdist_source)
        _extract_sdist(uv_build_sdist_a, uv_build_sdist_source)
        setuptools_rebuild_output = temporary / "rebuild-artifacts-setuptools"
        uv_build_rebuild_output = temporary / "rebuild-artifacts-uv-build"
        _build(
            uv,
            setuptools_sdist_source,
            setuptools_rebuild_output,
            environment,
            wheel_only=True,
        )
        _build(
            uv,
            uv_build_sdist_source,
            uv_build_rebuild_output,
            environment,
            wheel_only=True,
        )
        setuptools_rebuilt_wheel = _wheel_only(setuptools_rebuild_output)
        uv_build_rebuilt_wheel = _wheel_only(uv_build_rebuild_output)

        setuptools_wheel_files = read_zip_files(setuptools_wheel)
        uv_build_wheel_files = read_zip_files(uv_build_wheel_a)
        uv_build_second_wheel_files = read_zip_files(uv_build_wheel_b)
        setuptools_rebuilt_files = read_zip_files(setuptools_rebuilt_wheel)
        uv_build_rebuilt_files = read_zip_files(uv_build_rebuilt_wheel)
        for wheel_files in (
            setuptools_wheel_files,
            uv_build_wheel_files,
            uv_build_second_wheel_files,
            setuptools_rebuilt_files,
            uv_build_rebuilt_files,
        ):
            validate_record(wheel_files)

        setuptools_sdist_files = read_sdist_files(setuptools_sdist)
        uv_build_sdist_files = read_sdist_files(uv_build_sdist_a)

        comparisons: dict[str, dict[str, Any]] = {}
        reproducible = _sha256(uv_build_wheel_a) == _sha256(uv_build_wheel_b) and _sha256(
            uv_build_sdist_a
        ) == _sha256(uv_build_sdist_b)
        comparisons["uv_build_reproducibility"] = _boolean_result(
            reproducible,
            "UVBEVAL_CANDIDATE_NOT_REPRODUCIBLE",
            [] if reproducible else ["wheel-or-sdist-sha256"],
        )

        _add_file_comparison(
            comparisons,
            "wheel_archive_contents",
            setuptools_wheel_files,
            uv_build_wheel_files,
            "UVBEVAL_WHEEL_ARTIFACT_DRIFT",
        )
        setuptools_payload = {
            name: data
            for name, data in setuptools_wheel_files.items()
            if name.startswith("ancestryllm/")
        }
        uv_build_payload = {
            name: data
            for name, data in uv_build_wheel_files.items()
            if name.startswith("ancestryllm/")
        }
        _add_file_comparison(
            comparisons,
            "wheel_package_payload",
            setuptools_payload,
            uv_build_payload,
            "UVBEVAL_WHEEL_PAYLOAD_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "module_paths",
            {name: b"" for name in setuptools_payload if name.endswith(".py")},
            {name: b"" for name in uv_build_payload if name.endswith(".py")},
            "UVBEVAL_MODULE_PATH_DRIFT",
        )
        migration_prefix = "ancestryllm/storage/migrations/"
        _add_file_comparison(
            comparisons,
            "migration_paths",
            {name: b"" for name in setuptools_payload if name.startswith(migration_prefix)},
            {name: b"" for name in uv_build_payload if name.startswith(migration_prefix)},
            "UVBEVAL_MIGRATION_PATH_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "package_data_paths",
            {name: b"" for name in setuptools_payload if not name.endswith(".py")},
            {name: b"" for name in uv_build_payload if not name.endswith(".py")},
            "UVBEVAL_PACKAGE_DATA_DRIFT",
        )

        setuptools_metadata = _single_suffix(setuptools_wheel_files, "METADATA")
        uv_build_metadata = _single_suffix(uv_build_wheel_files, "METADATA")
        _add_file_comparison(
            comparisons,
            "project_metadata",
            _metadata_contract_files(setuptools_metadata),
            _metadata_contract_files(uv_build_metadata),
            "UVBEVAL_PROJECT_METADATA_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "wheel_metadata",
            _semantic_message_files(setuptools_metadata),
            _semantic_message_files(uv_build_metadata),
            "UVBEVAL_WHEEL_METADATA_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "wheel_descriptor",
            _semantic_message_files(_single_suffix(setuptools_wheel_files, "WHEEL")),
            _semantic_message_files(_single_suffix(uv_build_wheel_files, "WHEEL")),
            "UVBEVAL_WHEEL_DESCRIPTOR_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "entry_points",
            _semantic_entry_point_files(_single_suffix(setuptools_wheel_files, "entry_points.txt")),
            _semantic_entry_point_files(_single_suffix(uv_build_wheel_files, "entry_points.txt")),
            "UVBEVAL_ENTRYPOINT_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "record",
            _record_semantics(setuptools_wheel_files),
            _record_semantics(uv_build_wheel_files),
            "UVBEVAL_RECORD_DRIFT",
        )

        setuptools_sdist_comparable = {
            name: data for name, data in setuptools_sdist_files.items() if name != "pyproject.toml"
        }
        uv_build_sdist_comparable = {
            name: data for name, data in uv_build_sdist_files.items() if name != "pyproject.toml"
        }
        _add_file_comparison(
            comparisons,
            "sdist_payload_except_controlled_build_configuration",
            setuptools_sdist_comparable,
            uv_build_sdist_comparable,
            "UVBEVAL_SDIST_PAYLOAD_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "project_files",
            {
                name: data
                for name, data in setuptools_sdist_files.items()
                if name in PROJECT_FILE_PATHS
            },
            {
                name: data
                for name, data in uv_build_sdist_files.items()
                if name in PROJECT_FILE_PATHS
            },
            "UVBEVAL_PROJECT_FILE_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "setuptools_sdist_reconstruction",
            setuptools_wheel_files,
            setuptools_rebuilt_files,
            "UVBEVAL_SETUPTOOLS_RECONSTRUCTION_DRIFT",
        )
        _add_file_comparison(
            comparisons,
            "uv_build_sdist_reconstruction",
            uv_build_wheel_files,
            uv_build_rebuilt_files,
            "UVBEVAL_UV_BUILD_RECONSTRUCTION_DRIFT",
        )

        setuptools_allowlist = {
            **_sdist_allowlist(setuptools_sdist_files),
            **{
                f"wheel_{key}": value
                for key, value in _wheel_allowlist(setuptools_wheel_files, version).items()
            },
        }
        uv_build_allowlist = {
            **_sdist_allowlist(uv_build_sdist_files),
            **{
                f"wheel_{key}": value
                for key, value in _wheel_allowlist(uv_build_wheel_files, version).items()
            },
        }
        comparisons["setuptools_allowlists"] = _result(
            setuptools_allowlist,
            "UVBEVAL_SETUPTOOLS_ALLOWLIST_VIOLATION",
        )
        comparisons["uv_build_allowlists"] = _result(
            uv_build_allowlist,
            "UVBEVAL_UV_BUILD_ALLOWLIST_VIOLATION",
        )

        setuptools_smoke_failures = _install_smoke(
            uv,
            setuptools_wheel,
            version,
            temporary / "install-setuptools",
            environment,
        )
        uv_build_smoke_failures = _install_smoke(
            uv,
            uv_build_wheel_a,
            version,
            temporary / "install-uv-build",
            environment,
        )
        comparisons["setuptools_install_import_entrypoint"] = _boolean_result(
            not setuptools_smoke_failures,
            "UVBEVAL_SETUPTOOLS_INSTALL_SMOKE_FAILED",
            setuptools_smoke_failures,
        )
        comparisons["uv_build_install_import_entrypoint"] = _boolean_result(
            not uv_build_smoke_failures,
            "UVBEVAL_UV_BUILD_INSTALL_SMOKE_FAILED",
            uv_build_smoke_failures,
        )

        artifact_records = {
            "setuptools": {
                "sdist": _artifact_record(setuptools_sdist),
                "wheel": _artifact_record(setuptools_wheel),
            },
            "setuptools_from_sdist": {
                "wheel": _artifact_record(setuptools_rebuilt_wheel),
            },
            "uv_build_first": {
                "sdist": _artifact_record(uv_build_sdist_a),
                "wheel": _artifact_record(uv_build_wheel_a),
            },
            "uv_build_from_sdist": {
                "wheel": _artifact_record(uv_build_rebuilt_wheel),
            },
            "uv_build_second": {
                "sdist": _artifact_record(uv_build_sdist_b),
                "wheel": _artifact_record(uv_build_wheel_b),
            },
        }

    failure_codes = _comparison_failures(comparisons)
    return {
        "accepted_input_differences": ACCEPTED_INPUT_DIFFERENCES,
        "accepted_output_normalizations": ACCEPTED_OUTPUT_NORMALIZATIONS,
        "artifacts": artifact_records,
        "comparisons": comparisons,
        "evaluation": "setuptools-vs-uv_build",
        "failure_codes": failure_codes,
        "schema_version": SCHEMA_VERSION,
        "source": {
            "commit": source_commit,
            "source_date_epoch": int(epoch),
        },
        "status": "compatible" if not failure_codes else "incompatible",
        "tools": {
            "build_frontend": f"uv {UV_VERSION}",
            "python": platform.python_version(),
            "setuptools": {
                "requirement": SETUPTOOLS_REQUIREMENT,
                "version": distribution_version("setuptools"),
            },
            "uv_build": {
                "requirement": UV_BUILD_REQUIREMENT,
                "version": distribution_version("uv_build"),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", type=Path, default=ROOT / ".tools" / "uv" / "uv")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "build" / "uv-build-evaluation.json",
    )
    args = parser.parse_args()
    try:
        report = evaluate(args.uv.resolve())
    except EvaluationError as error:
        report = {
            "evaluation": "setuptools-vs-uv_build",
            "failure_codes": [error.code],
            "schema_version": SCHEMA_VERSION,
            "status": "error",
        }
        _write_report(args.report, report)
        print(f"{error.code}: {error}", file=sys.stderr)
        return 2
    _write_report(args.report, report)
    if report["status"] != "compatible":
        print(
            "UVBEVAL_INCOMPATIBLE: candidate artifacts differ from the accepted contract",
            file=sys.stderr,
        )
        return 1
    print("UVBEVAL_COMPATIBLE: candidate artifacts match the accepted contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
