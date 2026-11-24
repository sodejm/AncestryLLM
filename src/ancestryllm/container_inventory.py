"""Deterministic package and license inventory for the minimal runtime image."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from ancestryllm.container_runtime import ContainerRuntimeError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_NAME_SEPARATOR = re.compile(r"[-_.]+")
_DEBIAN_PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _normalized_name(value: str) -> str:
    return _NAME_SEPARATOR.sub("-", value).casefold()


def _bounded_license(value: str | None) -> str:
    if value is None:
        return "UNKNOWN"
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 256:
        return "UNKNOWN"
    return normalized


def _python_license_file_hashes(
    distribution: metadata.Distribution,
    package_metadata: metadata.PackageMetadata,
    raw_name: str,
    version: str,
) -> list[str]:
    """Return retained, declared wheel-license evidence without exposing paths."""

    declared_files = package_metadata.get_all("License-File", [])
    if not declared_files:
        return []
    distribution_files = distribution.files or ()
    dist_info_suffix = f"-{version}.dist-info"
    digests: list[str] = []
    for declared_file in declared_files:
        declared_path = PurePosixPath(declared_file)
        if declared_path.is_absolute() or ".." in declared_path.parts:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_LICENSE_MISSING",
                "An installed Python distribution declared invalid license-file evidence.",
            )
        accepted_suffixes = (
            ("licenses", *declared_path.parts),
            declared_path.parts,
        )
        matching_files = [
            path
            for path in distribution_files
            if (parts := PurePosixPath(str(path)).parts)
            and any(
                len(parts) == len(suffix) + 1
                and parts[-len(suffix) :] == suffix
                and (directory := parts[-len(suffix) - 1])
                .casefold()
                .endswith(dist_info_suffix.casefold())
                and _normalized_name(directory[: -len(dist_info_suffix)])
                == _normalized_name(raw_name)
                for suffix in accepted_suffixes
            )
        ]
        if len(matching_files) != 1:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_LICENSE_MISSING",
                "An installed Python distribution omitted its declared license-file evidence.",
            )
        try:
            license_file = cast("Path", distribution.locate_file(matching_files[0]))
            content = license_file.read_bytes()
        except OSError as exc:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_LICENSE_MISSING",
                "An installed Python distribution omitted its declared license-file evidence.",
            ) from exc
        if not content:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_LICENSE_MISSING",
                "An installed Python distribution declared empty license-file evidence.",
            )
        digests.append(hashlib.sha256(content).hexdigest())
    return sorted(digests)


def _debian_paragraphs(value: str) -> list[dict[str, str]]:
    paragraphs: list[dict[str, str]] = []
    for raw_paragraph in value.split("\n\n"):
        fields: dict[str, str] = {}
        for line in raw_paragraph.splitlines():
            key, separator, field_value = line.partition(":")
            if not separator or not key or not field_value.startswith((" ", "\t")):
                continue
            fields[key] = field_value.strip()
        if fields:
            paragraphs.append(fields)
    return paragraphs


def _debian_license_evidence(path: Path) -> tuple[list[str], str]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ContainerRuntimeError(
            "CONTAINER_INVENTORY_LICENSE_MISSING",
            "An installed operating-system package omitted its retained license evidence.",
        ) from exc
    declared = sorted(
        {
            normalized
            for raw_line in content.decode("utf-8", errors="replace").splitlines()
            if raw_line.startswith("License:")
            and (normalized := _bounded_license(raw_line.removeprefix("License:"))) != "UNKNOWN"
        }
    )
    return declared or ["UNSPECIFIED"], hashlib.sha256(content).hexdigest()


def build_operating_system_inventory(
    status_path: Path = Path("/var/lib/dpkg/status"),
    documentation_root: Path = Path("/usr/share/doc"),
) -> list[dict[str, object]]:
    """Inventory every installed Debian package and its retained copyright evidence."""

    try:
        status = status_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContainerRuntimeError(
            "CONTAINER_INVENTORY_OS_INVALID",
            "The runtime operating-system package database was unavailable.",
        ) from exc
    packages: list[dict[str, object]] = []
    seen: set[str] = set()
    for fields in _debian_paragraphs(status):
        if fields.get("Status") != "install ok installed":
            continue
        name = fields.get("Package", "")
        version = fields.get("Version", "")
        architecture = fields.get("Architecture", "")
        if (
            not _DEBIAN_PACKAGE_NAME.fullmatch(name)
            or not version
            or not architecture
            or name in seen
        ):
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_OS_INVALID",
                "An installed operating-system package has an invalid or duplicate identity.",
            )
        seen.add(name)
        licenses, license_sha256 = _debian_license_evidence(documentation_root / name / "copyright")
        packages.append(
            {
                "name": name,
                "version": version,
                "architecture": architecture,
                "licenses": licenses,
                "license_file_sha256": license_sha256,
            }
        )
    if not packages:
        raise ContainerRuntimeError(
            "CONTAINER_INVENTORY_OS_INVALID",
            "The runtime operating-system package inventory was empty.",
        )
    packages.sort(key=lambda item: str(item["name"]))
    return packages


def build_inventory(
    distributions: Iterable[metadata.Distribution] | None = None,
    operating_system_packages: Iterable[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return a stable inventory without paths, environment values, or timestamps."""

    packages: list[dict[str, object]] = []
    seen: set[str] = set()
    for distribution in distributions if distributions is not None else metadata.distributions():
        package_metadata = distribution.metadata
        raw_name = package_metadata.get("Name")
        version = package_metadata.get("Version")
        if not raw_name or not version:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_INVALID",
                "An installed distribution is missing its reviewed name or version identity.",
            )
        name = _normalized_name(raw_name)
        if name in seen:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_DUPLICATE",
                "The runtime contains duplicate normalized distribution identities.",
            )
        seen.add(name)
        license_classifiers = sorted(
            value.removeprefix("License :: ")
            for value in package_metadata.get_all("Classifier", [])
            if value.startswith("License :: ")
        )
        license_identity = _bounded_license(
            package_metadata.get("License-Expression") or package_metadata.get("License")
        )
        license_file_hashes = _python_license_file_hashes(
            distribution, package_metadata, raw_name, version
        )
        if license_identity == "UNKNOWN" and not license_classifiers and not license_file_hashes:
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_LICENSE_MISSING",
                "An installed Python distribution omitted its reviewed license evidence.",
            )
        packages.append(
            {
                "name": name,
                "version": version,
                "license": license_identity,
                "license_classifiers": license_classifiers,
                "license_file_sha256": license_file_hashes,
            }
        )
    packages.sort(key=lambda item: str(item["name"]).casefold())
    os_packages = list(
        operating_system_packages
        if operating_system_packages is not None
        else build_operating_system_inventory()
    )
    os_seen: set[str] = set()
    for package in os_packages:
        os_name = package.get("name")
        os_version = package.get("version")
        os_architecture = package.get("architecture")
        os_licenses = package.get("licenses")
        digest = package.get("license_file_sha256")
        if (
            set(package)
            != {
                "name",
                "version",
                "architecture",
                "licenses",
                "license_file_sha256",
            }
            or not isinstance(os_name, str)
            or not _DEBIAN_PACKAGE_NAME.fullmatch(os_name)
            or os_name in os_seen
            or not isinstance(os_version, str)
            or not os_version
            or not isinstance(os_architecture, str)
            or not os_architecture
            or not isinstance(os_licenses, list)
            or not os_licenses
            or not all(isinstance(value, str) and value for value in os_licenses)
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise ContainerRuntimeError(
                "CONTAINER_INVENTORY_OS_INVALID",
                "Operating-system package inventory evidence was incomplete or duplicated.",
            )
        os_seen.add(os_name)
    os_packages.sort(key=lambda item: str(item["name"]))
    return {
        "schema_version": 2,
        "status": "complete",
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "python_packages": packages,
        "operating_system_packages": os_packages,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the container inventory command and return its exit status."""
    args = _parser().parse_args(argv)
    payload = json.dumps(build_inventory(), indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised in the image build
    raise SystemExit(main())


__all__ = ["build_inventory", "build_operating_system_inventory", "main"]
