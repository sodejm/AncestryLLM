"""Container lifecycle, evidence, and failure-mode contracts."""

from __future__ import annotations

import errno
import json
import subprocess
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from scripts.container_ci_smoke import ContainerLifecycleError, _prove_logs_exclude_probe_token

from ancestryllm.container_inventory import build_inventory, build_operating_system_inventory
from ancestryllm.container_runtime import ContainerRuntimeError, publish_private_runtime_file

if TYPE_CHECKING:
    from importlib import metadata

    from scripts.container_ci_smoke import Docker


class _Distribution:
    def __init__(
        self,
        name: str | None,
        version: str | None,
        *,
        license_expression: str | None = None,
        classifiers: tuple[str, ...] = (),
    ) -> None:
        package_metadata = Message()
        if name is not None:
            package_metadata["Name"] = name
        if version is not None:
            package_metadata["Version"] = version
        if license_expression is not None:
            package_metadata["License-Expression"] = license_expression
        for classifier in classifiers:
            package_metadata["Classifier"] = classifier
        self.metadata = package_metadata


def _distribution(
    name: str | None,
    version: str | None,
    *,
    license_expression: str | None = None,
    classifiers: tuple[str, ...] = (),
) -> metadata.Distribution:
    return cast(
        "metadata.Distribution",
        _Distribution(
            name,
            version,
            license_expression=license_expression,
            classifiers=classifiers,
        ),
    )


def test_private_runtime_file_is_published_atomically_with_owner_only_mode(tmp_path: Path) -> None:
    target = tmp_path / "run" / "probe-token"
    target.parent.mkdir()

    publish_private_runtime_file(target, "fictional-token")

    assert target.read_text(encoding="utf-8") == "fictional-token"
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(target.parent.iterdir()) == [target]


def test_read_only_runtime_root_fails_closed_without_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "probe-token"
    target.write_text("known-good", encoding="utf-8")

    def deny_open(*args: object, **kwargs: object) -> int:
        raise OSError(errno.EROFS, "read-only file system")

    monkeypatch.setattr("ancestryllm.container_runtime.os.open", deny_open)
    with pytest.raises(ContainerRuntimeError, match="CONTAINER_RUNTIME_READ_ONLY"):
        publish_private_runtime_file(target, "replacement")

    assert target.read_text(encoding="utf-8") == "known-good"


def test_full_runtime_tmpfs_fails_closed_without_exposing_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "probe-token"

    def no_space(*args: object, **kwargs: object) -> int:
        raise OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr("ancestryllm.container_runtime.os.open", no_space)
    with pytest.raises(ContainerRuntimeError, match="CONTAINER_RUNTIME_DISK_FULL") as exc_info:
        publish_private_runtime_file(target, "must-not-leak")

    assert "must-not-leak" not in str(exc_info.value)
    assert not target.exists()


def test_package_inventory_is_schema_versioned_sorted_and_secret_free() -> None:
    inventory = build_inventory(
        (
            _distribution("uvicorn", "0.35.0", license_expression="BSD-3-Clause"),
            _distribution(
                "AncestryLLM",
                "0.5.0",
                classifiers=("License :: OSI Approved :: MIT License",),
            ),
        ),
        operating_system_packages=(
            {
                "name": "base-files",
                "version": "12.4+deb12u12",
                "architecture": "arm64",
                "licenses": ["GPL"],
                "license_file_sha256": "a" * 64,
            },
        ),
    )

    assert inventory["schema_version"] == 1
    assert inventory["status"] == "complete"
    packages = inventory["python_packages"]
    names = [package["name"] for package in packages]
    assert names == sorted(names, key=str.casefold)
    assert "ancestryllm" in names
    assert "uvicorn" in names
    assert packages[0]["license_classifiers"] == ["OSI Approved :: MIT License"]
    assert packages[1]["license"] == "BSD-3-Clause"
    serialized = json.dumps(inventory, sort_keys=True)
    assert str(Path.home()) not in serialized
    assert "environment" not in serialized.casefold()


def test_operating_system_inventory_is_complete_sorted_and_license_bound(tmp_path: Path) -> None:
    status = tmp_path / "status"
    documentation = tmp_path / "doc"
    status.write_text(
        """Package: zlib1g
Status: install ok installed
Architecture: arm64
Version: 1:1.2.13.dfsg-1

Package: base-files
Status: install ok installed
Architecture: arm64
Version: 12.4+deb12u12

Package: removed-package
Status: deinstall ok config-files
Architecture: arm64
Version: 1.0
""",
        encoding="utf-8",
    )
    for name, license_name in (("zlib1g", "Zlib"), ("base-files", "GPL")):
        license_path = documentation / name / "copyright"
        license_path.parent.mkdir(parents=True)
        license_path.write_text(f"License: {license_name}\n", encoding="utf-8")

    packages = build_operating_system_inventory(status, documentation)

    assert [package["name"] for package in packages] == ["base-files", "zlib1g"]
    assert packages[0]["licenses"] == ["GPL"]
    assert packages[1]["licenses"] == ["Zlib"]
    assert all(len(str(package["license_file_sha256"])) == 64 for package in packages)


def test_operating_system_inventory_fails_when_license_evidence_is_missing(
    tmp_path: Path,
) -> None:
    status = tmp_path / "status"
    status.write_text(
        """Package: base-files
Status: install ok installed
Architecture: amd64
Version: 12.4
""",
        encoding="utf-8",
    )

    with pytest.raises(ContainerRuntimeError, match="CONTAINER_INVENTORY_LICENSE_MISSING"):
        build_operating_system_inventory(status, tmp_path / "doc")


def test_inventory_rejects_missing_package_identity() -> None:
    with pytest.raises(ContainerRuntimeError, match="CONTAINER_INVENTORY_INVALID"):
        build_inventory((_distribution(None, "1.0"),), operating_system_packages=())


def test_inventory_rejects_missing_python_license_evidence() -> None:
    with pytest.raises(ContainerRuntimeError, match="CONTAINER_INVENTORY_LICENSE_MISSING"):
        build_inventory(
            (_distribution("fictional-package", "1.0"),),
            operating_system_packages=(),
        )


def test_inventory_rejects_duplicate_normalized_package_identity() -> None:
    with pytest.raises(ContainerRuntimeError, match="CONTAINER_INVENTORY_DUPLICATE"):
        build_inventory(
            (
                _distribution("fictional_package", "1.0", license_expression="MIT"),
                _distribution("fictional-package", "1.0", license_expression="MIT"),
            ),
            operating_system_packages=(),
        )


class _SplitStreamLogDocker:
    def run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout
        if arguments[0] == "exec":
            return subprocess.CompletedProcess(arguments, 0, "fictional-token\n", "")
        return subprocess.CompletedProcess(
            arguments,
            0,
            "",
            "Authorization: Bearer fictional-token\n",
        )


def test_log_redaction_proof_scans_docker_stderr() -> None:
    with pytest.raises(ContainerLifecycleError, match="CONTAINER_LOG_SECRET_EXPOSURE"):
        _prove_logs_exclude_probe_token(
            cast("Docker", _SplitStreamLogDocker()), "fictional-gateway"
        )


def test_lifecycle_evidence_does_not_claim_an_unexercised_migration_path() -> None:
    lifecycle_source = (Path(__file__).parents[1] / "scripts" / "container_ci_smoke.py").read_text(
        encoding="utf-8"
    )

    assert '"schema-migration-write-blocked"' not in lifecycle_source
