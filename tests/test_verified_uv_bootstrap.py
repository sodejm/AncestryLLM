"""Exercise the fail-closed uv bootstrap policy, artifacts, and workflow boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "uv-bootstrap-policy.json"
SCRIPT_PATH = ROOT / "scripts" / "bootstrap_uv.py"
ACTION_PATH = ROOT / ".github" / "actions" / "setup-verified-uv" / "action.yml"

UV_VERSION = "0.12.1"
UV_SOURCE_COMMIT = "329541a503de8a4d9bb021814f9c0875efe033c8"
UV_SIGNER = "https://github.com/astral-sh/uv/.github/workflows/release.yml@refs/heads/main"
SETUP_UV_COMMIT = "c771a70e6277c0a99b617c7a806ffedaca235ff9"

EXPECTED_UV_ARCHIVES = {
    "linux-x86_64": (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "90b2f223fb69d19db49e117da601f64978593417988530aa733d456141b4bcbb",
    ),
    "linux-arm64": (
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "769d373e146692c639b5fbaae33b331c297a32e03d30448772051902df52bbf4",
    ),
    "macos-x86_64": (
        "uv-x86_64-apple-darwin.tar.gz",
        "69d9f9a00337f25a50dcb13882052da08b8469bac11091c98c5694c3c6721467",
    ),
    "macos-arm64": (
        "uv-aarch64-apple-darwin.tar.gz",
        "77d2906988e8074fd43f2f329ec452ebbf9b0c257ba1c66451c71de70a6baf42",
    ),
    "windows-x86_64": (
        "uv-x86_64-pc-windows-msvc.zip",
        "8fcb0cb46e1229065e344758980924e569bef5882ef45f46fada8fb24e06b74a",
    ),
    "windows-arm64": (
        "uv-aarch64-pc-windows-msvc.zip",
        "9bc7c18e616230fa2dc6fb24bc3afde18a95c2b5c9433de747e9502c66041568",
    ),
}

EXPECTED_GH_ARCHIVES = {
    "linux-x86_64": (
        "gh_2.97.0_linux_amd64.tar.gz",
        "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112",
    ),
    "linux-arm64": (
        "gh_2.97.0_linux_arm64.tar.gz",
        "73ea440ecad9c9e284429997ee6f93577bc6f7bc6fba357ef62c53ad8fb641a5",
    ),
    "macos-x86_64": (
        "gh_2.97.0_macOS_amd64.zip",
        "63298c998cc2a924c9e254c6af6a1caad6ece281122687a91f079bc0a462700e",
    ),
    "macos-arm64": (
        "gh_2.97.0_macOS_arm64.zip",
        "a58b8fd77b417a38f47a0b54d1370c59b0fcdb324ccc9ca002b0998f7c4c999e",
    ),
    "windows-x86_64": (
        "gh_2.97.0_windows_amd64.zip",
        "35d7fe05c4dd1411ffda1e73dfc7c6f44b75c936ca51fa6595c657fdc0350cec",
    ),
    "windows-arm64": (
        "gh_2.97.0_windows_arm64.zip",
        "3e2d4a166da4ee5020c592737b65eec0e724946d5d5b962f5fe59d99116dc4bf",
    ),
}


@pytest.fixture(scope="module")
def bootstrap_module() -> Any:
    assert SCRIPT_PATH.is_file(), "the verified uv bootstrap utility has not been implemented"
    spec = importlib.util.spec_from_file_location("bootstrap_uv", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _tar_archive(member: str, payload: bytes, *, mode: int = 0o755) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        info.mode = mode
        archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _zip_archive(member: str, payload: bytes, *, mode: int = 0o755) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w") as archive:
        info = zipfile.ZipInfo(member)
        info.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
        archive.writestr(info, payload)
    return stream.getvalue()


def _fixture_policy(
    tmp_path: Path,
    *,
    uv_archive: bytes,
    gh_archive: bytes,
    uv_binary: bytes,
) -> Path:
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    payload["uv"]["assets"]["linux-x86_64"]["sha256"] = _sha256(uv_archive)
    payload["uv"]["assets"]["linux-x86_64"]["binary_sha256"] = _sha256(uv_binary)
    payload["github_cli"]["assets"]["linux-x86_64"]["sha256"] = _sha256(gh_archive)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _attestation_payload(
    archive_name: str,
    archive_sha256: str,
    *,
    overrides: dict[str, str] | None = None,
) -> str:
    certificate = {
        "subjectAlternativeName": UV_SIGNER,
        "issuer": "https://token.actions.githubusercontent.com",
        "githubWorkflowSHA": UV_SOURCE_COMMIT,
        "githubWorkflowRepository": "astral-sh/uv",
        "githubWorkflowRef": "refs/heads/main",
        "buildSignerURI": UV_SIGNER,
        "buildSignerDigest": UV_SOURCE_COMMIT,
        "sourceRepositoryURI": "https://github.com/astral-sh/uv",
        "sourceRepositoryDigest": UV_SOURCE_COMMIT,
        "sourceRepositoryRef": "refs/heads/main",
    }
    predicate_type = "https://slsa.dev/provenance/v1"
    subject_name = archive_name
    subject_digest = archive_sha256
    for key, value in (overrides or {}).items():
        if key == "predicateType":
            predicate_type = value
        elif key == "subjectName":
            subject_name = value
        elif key == "subjectDigest":
            subject_digest = value
        else:
            certificate[key] = value
    return json.dumps(
        [
            {
                "verificationResult": {
                    "signature": {"certificate": certificate},
                    "statement": {
                        "predicateType": predicate_type,
                        "subject": [
                            {
                                "name": subject_name,
                                "digest": {"sha256": subject_digest},
                            }
                        ],
                    },
                }
            }
        ]
    )


class FixtureDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.urls: list[str] = []

    def __call__(self, url: str, destination: Path) -> None:
        self.urls.append(url)
        destination.write_bytes(self.payloads[url])


class FixtureRunner:
    def __init__(
        self,
        attestation_stdout: str,
        *,
        gh_version: str = "gh version 2.97.0 (fixture)",
        uv_version: str = "uv 0.12.1",
    ) -> None:
        self.attestation_stdout = attestation_stdout
        self.gh_version = gh_version
        self.uv_version = uv_version
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        normalized = tuple(str(token) for token in command)
        self.commands.append(normalized)
        if normalized[-1] == "--version" and "gh" in Path(normalized[0]).name:
            stdout = self.gh_version
        elif "attestation" in normalized:
            stdout = self.attestation_stdout
        else:
            stdout = self.uv_version
        return subprocess.CompletedProcess(normalized, 0, stdout=stdout, stderr="")


def _valid_fixture(
    tmp_path: Path,
) -> tuple[Path, FixtureDownloader, FixtureRunner, bytes]:
    gh_binary = b"verified fixture gh"
    uv_binary = b"verified fixture uv"
    gh_archive = _tar_archive("gh_2.97.0_linux_amd64/bin/gh", gh_binary)
    uv_archive = _tar_archive("uv-x86_64-unknown-linux-gnu/uv", uv_binary)
    policy_path = _fixture_policy(
        tmp_path,
        uv_archive=uv_archive,
        gh_archive=gh_archive,
        uv_binary=uv_binary,
    )
    gh_url = "https://github.com/cli/cli/releases/download/v2.97.0/gh_2.97.0_linux_amd64.tar.gz"
    uv_url = (
        "https://github.com/astral-sh/uv/releases/download/0.12.1/"
        "uv-x86_64-unknown-linux-gnu.tar.gz"
    )
    downloader = FixtureDownloader({gh_url: gh_archive, uv_url: uv_archive})
    runner = FixtureRunner(
        _attestation_payload("uv-x86_64-unknown-linux-gnu.tar.gz", _sha256(uv_archive))
    )
    return policy_path, downloader, runner, uv_binary


def test_policy_pins_every_reviewed_trust_root_and_supported_asset() -> None:
    assert POLICY_PATH.is_file()
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    uv = payload["uv"]
    assert uv["version"] == UV_VERSION
    assert uv["release_repository"] == "astral-sh/uv"
    assert uv["source_repository"] == "https://github.com/astral-sh/uv"
    assert uv["source_commit"] == UV_SOURCE_COMMIT
    assert uv["source_ref"] == "refs/heads/main"
    assert uv["oidc_issuer"] == "https://token.actions.githubusercontent.com"
    assert uv["signer_workflow_identity"] == UV_SIGNER
    assert uv["predicate_type"] == "https://slsa.dev/provenance/v1"
    assert {
        key: (asset["archive_name"], asset["sha256"]) for key, asset in uv["assets"].items()
    } == EXPECTED_UV_ARCHIVES

    gh = payload["github_cli"]
    assert gh["version"] == "2.97.0"
    assert gh["release_repository"] == "cli/cli"
    assert {
        key: (asset["archive_name"], asset["sha256"]) for key, asset in gh["assets"].items()
    } == EXPECTED_GH_ARCHIVES

    assert payload["setup_uv_action"] == {
        "version": "v9.0.0",
        "commit": SETUP_UV_COMMIT,
    }
    verifier = payload["python_verifiers"]["pypi-attestations"]
    assert verifier["version"] == "0.0.30"
    assert verifier["source_repository"] == "https://github.com/pypi/pypi-attestations"
    assert {(artifact["filename"], artifact["sha256"]) for artifact in verifier["artifacts"]} == {
        (
            "pypi_attestations-0.0.30-py3-none-any.whl",
            "b3a9c53f6cb89e5e7b5b70e6cfca97cfc66008c1ed54087355e06e40071cef21",
        ),
        (
            "pypi_attestations-0.0.30.tar.gz",
            "14ff13c76bbef02a483c2b77532da02bfd746b5141b881f355e06b9807434423",
        ),
    }
    assert verifier["reviewed_update_procedure"].startswith("docs/")


def test_python_verifier_artifacts_are_mirrored_by_uv_lock() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    verifier = policy["python_verifiers"]["pypi-attestations"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    locked_verifier = next(
        (package for package in lock["package"] if package["name"] == "pypi-attestations"),
        None,
    )
    assert locked_verifier is not None
    assert locked_verifier["version"] == verifier["version"]

    locked_artifacts = {
        (Path(artifact["url"]).name, artifact["hash"].removeprefix("sha256:"))
        for artifact in [
            locked_verifier["sdist"],
            *locked_verifier["wheels"],
        ]
    }
    policy_artifacts = {
        (artifact["filename"], artifact["sha256"]) for artifact in verifier["artifacts"]
    }
    assert locked_artifacts == policy_artifacts


def test_attestation_accepts_reviewed_asset_in_multi_asset_statement(
    bootstrap_module: Any,
) -> None:
    archive_name = "uv-x86_64-unknown-linux-gnu.tar.gz"
    archive_sha256 = EXPECTED_UV_ARCHIVES["linux-x86_64"][1]
    payload = json.loads(_attestation_payload(archive_name, archive_sha256))
    payload[0]["verificationResult"]["statement"]["subject"].append(
        {
            "name": "uv-aarch64-unknown-linux-gnu.tar.gz",
            "digest": {"sha256": EXPECTED_UV_ARCHIVES["linux-arm64"][1]},
        }
    )

    bootstrap_module._verify_attestation_payload(
        json.dumps(payload),
        archive_name=archive_name,
        archive_sha256=archive_sha256,
    )


def test_uv_version_accepts_pinned_build_metadata(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    runner = FixtureRunner(
        "[]",
        uv_version=("uv 0.12.1 (329541a50 2026-07-31 aarch64-apple-darwin)"),
    )

    bootstrap_module._assert_uv_version(tmp_path / "uv", runner)


@pytest.mark.parametrize(
    "reported_version",
    [
        "uv 0.12.2 (329541a50 2026-07-31 aarch64-apple-darwin)",
        "uv 0.12.1 (000000000 2026-07-31 aarch64-apple-darwin)",
        "uv 0.12.1 substitute",
    ],
)
def test_uv_version_rejects_unreviewed_identity(
    tmp_path: Path,
    bootstrap_module: Any,
    reported_version: str,
) -> None:
    runner = FixtureRunner("[]", uv_version=reported_version)

    with pytest.raises(bootstrap_module.BootstrapError, match="UV_VERSION_MISMATCH"):
        bootstrap_module._assert_uv_version(tmp_path / "uv", runner)


def test_valid_local_artifacts_complete_bootstrap_and_emit_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, uv_binary = _valid_fixture(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    install_dir = tmp_path / "tools" / "uv"

    receipt = bootstrap_module.bootstrap_uv(
        policy_path=policy_path,
        install_dir=install_dir,
        receipt_path=receipt_path,
        downloader=downloader,
        runner=runner,
        platform_id=("linux", "x86_64"),
        temporary_root=tmp_path / "temporary",
        now=lambda: datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )

    installed = install_dir / "uv"
    assert installed.read_bytes() == uv_binary
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "success"
    assert receipt["failure_category"] is None
    assert receipt["tool"]["version"] == UV_VERSION
    assert receipt["provenance"]["source_commit"] == UV_SOURCE_COMMIT
    attestation_index = next(
        index for index, command in enumerate(runner.commands) if "attestation" in command
    )
    uv_version_index = next(
        index
        for index, command in enumerate(runner.commands)
        if Path(command[0]).name == "uv" and command[-1] == "--version"
    )
    assert receipt_path.is_file()
    assert attestation_index < uv_version_index
    assert "--cert-identity" in runner.commands[attestation_index]
    assert "--signer-workflow" not in runner.commands[attestation_index]


def test_bad_github_cli_is_never_executed(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    gh_url = next(url for url in downloader.payloads if "cli/cli" in url)
    downloader.payloads[gh_url] += b"corruption"

    with pytest.raises(bootstrap_module.BootstrapError, match="VERIFIER_ARCHIVE_DIGEST_MISMATCH"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert runner.commands == []
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failure"
    assert receipt["failure_category"] == "VERIFIER_ARCHIVE_DIGEST_MISMATCH"


def test_corrupted_uv_never_reaches_attestation(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    uv_url = next(url for url in downloader.payloads if "astral-sh/uv" in url)
    downloader.payloads[uv_url] += b"corruption"

    with pytest.raises(bootstrap_module.BootstrapError, match="UV_ARCHIVE_DIGEST_MISMATCH"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert all("attestation" not in command for command in runner.commands)
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failure"
    assert receipt["failure_category"] == "UV_ARCHIVE_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceRepositoryURI", "https://github.com/example/substitute"),
        ("subjectAlternativeName", "https://github.com/example/release.yml@refs/heads/main"),
        ("buildSignerURI", "https://github.com/example/release.yml@refs/heads/main"),
        ("sourceRepositoryDigest", "0" * 40),
        ("sourceRepositoryRef", "refs/tags/0.12.1"),
        ("predicateType", "https://slsa.dev/provenance/v0.2"),
        ("issuer", "https://issuer.example.test"),
        ("subjectName", "uv-substitute.tar.gz"),
        ("subjectDigest", "0" * 64),
    ],
)
def test_attestation_identity_mismatches_fail_closed(
    tmp_path: Path,
    bootstrap_module: Any,
    field: str,
    value: str,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    uv_url = next(url for url in downloader.payloads if "astral-sh/uv" in url)
    runner.attestation_stdout = _attestation_payload(
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        _sha256(downloader.payloads[uv_url]),
        overrides={field: value},
    )

    with pytest.raises(bootstrap_module.BootstrapError, match="ATTESTATION_IDENTITY_MISMATCH"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert not any(Path(command[0]).name == "uv" for command in runner.commands)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda policy: policy.update(schema_version=2), "POLICY_SCHEMA_UNSUPPORTED"),
        (lambda policy: policy["uv"].update(version="latest"), "POLICY_VALUE_INVALID"),
        (
            lambda policy: policy["uv"].update(
                release_url_template="https://mirror.example.test/{asset}"
            ),
            "POLICY_VALUE_INVALID",
        ),
        (
            lambda policy: policy["uv"]["assets"]["linux-x86_64"].update(
                archive_name="uv-substitute.tar.gz"
            ),
            "POLICY_VALUE_INVALID",
        ),
        (
            lambda policy: policy["python_verifiers"]["pypi-attestations"]["artifacts"][0].update(
                filename="substitute.whl"
            ),
            "POLICY_VALUE_INVALID",
        ),
        (lambda policy: policy.update(unreviewed_field=True), "POLICY_FIELDS_INVALID"),
        (lambda policy: policy["uv"].pop("source_commit"), "POLICY_FIELDS_INVALID"),
    ],
)
def test_unknown_schema_versions_fields_assets_urls_and_index_artifacts_fail(
    tmp_path: Path,
    bootstrap_module: Any,
    mutation: Callable[[dict[str, Any]], Any],
    error: str,
) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    mutation(policy)
    path = tmp_path / "invalid-policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(bootstrap_module.BootstrapError, match=error):
        bootstrap_module.load_policy(path)


@pytest.mark.parametrize(
    ("platform_id", "error"),
    [
        (("freebsd", "x86_64"), "PLATFORM_UNSUPPORTED"),
        (("linux", "sparc64"), "ARCHITECTURE_UNSUPPORTED"),
    ],
)
def test_unknown_platforms_and_architectures_fail(
    bootstrap_module: Any,
    platform_id: tuple[str, str],
    error: str,
) -> None:
    policy = bootstrap_module.load_policy(POLICY_PATH)
    with pytest.raises(bootstrap_module.BootstrapError, match=error):
        bootstrap_module.select_platform(policy, platform_id)


@pytest.mark.parametrize(
    "member",
    [
        "/absolute/uv",
        "../outside/uv",
        "nested/../../outside/uv",
        "C:/outside/uv.exe",
        "nested\\..\\..\\outside\\uv.exe",
    ],
)
def test_unsafe_tar_and_zip_paths_never_escape_temporary_root(
    tmp_path: Path,
    bootstrap_module: Any,
    member: str,
) -> None:
    tar_path = tmp_path / "unsafe.tar.gz"
    tar_path.write_bytes(_tar_archive(member, b"unsafe"))
    zip_path = tmp_path / "unsafe.zip"
    zip_path.write_bytes(_zip_archive(member, b"unsafe"))

    for archive_path in (tar_path, zip_path):
        destination = tmp_path / archive_path.stem
        with pytest.raises(bootstrap_module.BootstrapError, match="ARCHIVE_MEMBER_UNSAFE"):
            bootstrap_module.safe_extract_archive(archive_path, destination)

    assert not (tmp_path / "outside" / "uv").exists()
    assert not (tmp_path / "outside" / "uv.exe").exists()


def test_tar_links_devices_and_zip_symlinks_are_rejected(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    tar_path = tmp_path / "unsafe-links.tar.gz"
    with tarfile.open(tar_path, mode="w:gz") as archive:
        for kind, linkname in (
            (tarfile.SYMTYPE, "../outside"),
            (tarfile.LNKTYPE, "../outside"),
            (tarfile.CHRTYPE, ""),
        ):
            info = tarfile.TarInfo(f"member-{kind!r}")
            info.type = kind
            info.linkname = linkname
            archive.addfile(info)

    zip_path = tmp_path / "unsafe-symlink.zip"
    with zipfile.ZipFile(zip_path, mode="w") as archive:
        info = zipfile.ZipInfo("uv-link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../outside")

    for archive_path in (tar_path, zip_path):
        with pytest.raises(bootstrap_module.BootstrapError, match="ARCHIVE_MEMBER_UNSAFE"):
            bootstrap_module.safe_extract_archive(archive_path, tmp_path / archive_path.stem)


@pytest.mark.parametrize("archive_kind", ("tar", "zip"))
def test_archive_child_can_precede_its_explicit_parent_directory(
    tmp_path: Path,
    bootstrap_module: Any,
    archive_kind: str,
) -> None:
    archive_path = tmp_path / ("ordered.tar.gz" if archive_kind == "tar" else "ordered.zip")
    if archive_kind == "tar":
        with tarfile.open(archive_path, mode="w:gz") as archive:
            child = tarfile.TarInfo("nested/uv")
            child.size = len(b"verified")
            child.mode = 0o755
            archive.addfile(child, io.BytesIO(b"verified"))
            directory = tarfile.TarInfo("nested")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            archive.addfile(directory)
    else:
        with zipfile.ZipFile(archive_path, mode="w") as archive:
            child = zipfile.ZipInfo("nested/uv")
            child.external_attr = ((stat.S_IFREG | 0o755) & 0xFFFF) << 16
            archive.writestr(child, b"verified")
            directory = zipfile.ZipInfo("nested/")
            directory.external_attr = ((stat.S_IFDIR | 0o755) & 0xFFFF) << 16
            archive.writestr(directory, b"")

    destination = tmp_path / f"extracted-{archive_kind}"
    bootstrap_module.safe_extract_archive(archive_path, destination)

    assert (destination / "nested" / "uv").read_bytes() == b"verified"


def test_corrupted_cached_binary_is_not_reused_or_deleted(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    install_dir = tmp_path / "tools"
    install_dir.mkdir()
    cached = install_dir / "uv"
    cached.write_bytes(b"untrusted cached executable")

    with pytest.raises(bootstrap_module.BootstrapError, match="CACHED_BINARY_DIGEST_MISMATCH"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=install_dir,
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert cached.read_bytes() == b"untrusted cached executable"
    assert downloader.urls == []
    assert runner.commands == []
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failure"
    assert receipt["failure_category"] == "CACHED_BINARY_DIGEST_MISMATCH"


def test_receipt_is_deterministic_except_timestamp_and_excludes_local_context(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_BOOTSTRAP_FIXTURE_TOKEN", "fixture-secret-token")
    receipts: list[dict[str, Any]] = []
    for index in range(2):
        root = tmp_path / f"local-user-path-{index}"
        root.mkdir()
        policy_path, downloader, runner, _ = _valid_fixture(root)
        receipt = bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=root / "tools",
            receipt_path=root / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=root / "temporary",
            now=lambda index=index: datetime(2026, 8, 9, 12, index, tzinfo=UTC),
        )
        receipts.append(receipt)
        rendered = json.dumps(receipt, sort_keys=True)
        assert str(root) not in rendered
        assert "fixture-secret-token" not in rendered
        assert "UV_BOOTSTRAP_FIXTURE_TOKEN" not in rendered

    for receipt in receipts:
        receipt.pop("verified_at")
    assert receipts[0] == receipts[1]


def test_verify_installed_rehashes_before_running_uv(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, _, _, uv_binary = _valid_fixture(tmp_path)
    uv_path = tmp_path / "uv"
    uv_path.write_bytes(uv_binary)
    runner = FixtureRunner("[]")

    bootstrap_module.verify_installed_uv(
        policy_path=policy_path,
        uv_path=uv_path,
        runner=runner,
        platform_id=("linux", "x86_64"),
    )
    assert runner.commands == [(str(uv_path), "--version")]

    uv_path.write_bytes(b"substitute")
    runner.commands.clear()
    with pytest.raises(bootstrap_module.BootstrapError, match="INSTALLED_BINARY_DIGEST_MISMATCH"):
        bootstrap_module.verify_installed_uv(
            policy_path=policy_path,
            uv_path=uv_path,
            runner=runner,
            platform_id=("linux", "x86_64"),
        )
    assert runner.commands == []


def test_composite_action_bootstraps_local_uv_without_third_party_action() -> None:
    assert ACTION_PATH.is_file()
    action = ACTION_PATH.read_text(encoding="utf-8")

    assert "astral-sh/setup-uv@" not in action
    assert "python-version" in action
    assert "runner.os" in action
    assert "runner.arch" in action
    assert "bootstrap_uv.py verify-installed" in action
    assert "--uv-path" in action
    assert "GITHUB_PATH" in action
    assert "value: ${{ steps.receipt.outputs.receipt_path }}" in action
    assert "if: ${{ always() }}" in action
    assert "if-no-files-found: ignore" in action
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in action


def test_verify_installed_cli_requires_an_explicit_binary_path(
    bootstrap_module: Any,
) -> None:
    with pytest.raises(SystemExit):
        bootstrap_module._parser().parse_args(["verify-installed"])

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'shutil.which("uv")' not in source
