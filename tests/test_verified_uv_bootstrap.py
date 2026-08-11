"""Exercise the fail-closed uv bootstrap policy, artifacts, and workflow boundary."""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import io
import json
import stat
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

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
        21760555,
    ),
    "linux-arm64": (
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "769d373e146692c639b5fbaae33b331c297a32e03d30448772051902df52bbf4",
        20534826,
    ),
    "macos-x86_64": (
        "uv-x86_64-apple-darwin.tar.gz",
        "69d9f9a00337f25a50dcb13882052da08b8469bac11091c98c5694c3c6721467",
        19622543,
    ),
    "macos-arm64": (
        "uv-aarch64-apple-darwin.tar.gz",
        "77d2906988e8074fd43f2f329ec452ebbf9b0c257ba1c66451c71de70a6baf42",
        17679560,
    ),
    "windows-x86_64": (
        "uv-x86_64-pc-windows-msvc.zip",
        "8fcb0cb46e1229065e344758980924e569bef5882ef45f46fada8fb24e06b74a",
        19073343,
    ),
    "windows-arm64": (
        "uv-aarch64-pc-windows-msvc.zip",
        "9bc7c18e616230fa2dc6fb24bc3afde18a95c2b5c9433de747e9502c66041568",
        18030168,
    ),
}

EXPECTED_GH_ARCHIVES = {
    "linux-x86_64": (
        "gh_2.97.0_linux_amd64.tar.gz",
        "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112",
        14770812,
    ),
    "linux-arm64": (
        "gh_2.97.0_linux_arm64.tar.gz",
        "73ea440ecad9c9e284429997ee6f93577bc6f7bc6fba357ef62c53ad8fb641a5",
        13428558,
    ),
    "macos-x86_64": (
        "gh_2.97.0_macOS_amd64.zip",
        "63298c998cc2a924c9e254c6af6a1caad6ece281122687a91f079bc0a462700e",
        15418698,
    ),
    "macos-arm64": (
        "gh_2.97.0_macOS_arm64.zip",
        "a58b8fd77b417a38f47a0b54d1370c59b0fcdb324ccc9ca002b0998f7c4c999e",
        13845290,
    ),
    "windows-x86_64": (
        "gh_2.97.0_windows_amd64.zip",
        "35d7fe05c4dd1411ffda1e73dfc7c6f44b75c936ca51fa6595c657fdc0350cec",
        14938517,
    ),
    "windows-arm64": (
        "gh_2.97.0_windows_arm64.zip",
        "3e2d4a166da4ee5020c592737b65eec0e724946d5d5b962f5fe59d99116dc4bf",
        13391688,
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


def _corrupt_same_size(payload: bytes) -> bytes:
    assert payload
    return bytes([payload[0] ^ 1]) + payload[1:]


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
    payload["uv"]["assets"]["linux-x86_64"]["size_bytes"] = len(uv_archive)
    payload["uv"]["assets"]["linux-x86_64"]["binary_sha256"] = _sha256(uv_binary)
    payload["github_cli"]["assets"]["linux-x86_64"]["sha256"] = _sha256(gh_archive)
    payload["github_cli"]["assets"]["linux-x86_64"]["size_bytes"] = len(gh_archive)
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
        self.calls: list[tuple[str, int | None]] = []

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]

    def __call__(self, url: str, destination: Path, expected_size: int | None = None) -> None:
        self.calls.append((url, expected_size))
        destination.write_bytes(self.payloads[url])


class FixtureRunner:
    def __init__(
        self,
        attestation_stdout: str,
        *,
        gh_version: str = "gh version 2.97.0 (fixture)",
        uv_version: str = "uv 0.12.1",
        attestation_returncode: int = 0,
        attestation_stderr: str = "",
    ) -> None:
        self.attestation_stdout = attestation_stdout
        self.gh_version = gh_version
        self.uv_version = uv_version
        self.attestation_returncode = attestation_returncode
        self.attestation_stderr = attestation_stderr
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.timeouts: list[float | None] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        normalized = tuple(str(token) for token in command)
        self.commands.append(normalized)
        self.environments.append(dict(env))
        self.timeouts.append(timeout)
        if normalized[-1] == "--version" and "gh" in Path(normalized[0]).name:
            stdout = self.gh_version
        elif "attestation" in normalized:
            stdout = self.attestation_stdout
            return subprocess.CompletedProcess(
                normalized,
                self.attestation_returncode,
                stdout=stdout,
                stderr=self.attestation_stderr,
            )
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


def _verified_success_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
    receipt_path: Path,
) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    return bootstrap_module.bootstrap_uv(
        policy_path=policy_path,
        install_dir=tmp_path / "tools",
        receipt_path=receipt_path,
        downloader=downloader,
        runner=runner,
        platform_id=("linux", "x86_64"),
        temporary_root=tmp_path / "temporary",
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
    )


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
        key: (asset["archive_name"], asset["sha256"], asset["size_bytes"])
        for key, asset in uv["assets"].items()
    } == EXPECTED_UV_ARCHIVES

    gh = payload["github_cli"]
    assert gh["version"] == "2.97.0"
    assert gh["release_repository"] == "cli/cli"
    assert {
        key: (asset["archive_name"], asset["sha256"], asset["size_bytes"])
        for key, asset in gh["assets"].items()
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
    assert uv["assets"]["windows-x86_64"]["binary_path"] == "uv.exe"
    assert uv["assets"]["windows-arm64"]["binary_path"] == "uv.exe"


@pytest.mark.parametrize(
    ("architecture", "platform_key"),
    [
        ("AMD64", "windows-x86_64"),
        ("ARM64", "windows-arm64"),
    ],
)
def test_windows_uv_archives_use_the_reviewed_flat_executable_member(
    tmp_path: Path,
    bootstrap_module: Any,
    architecture: str,
    platform_key: str,
) -> None:
    gh_binary = b"verified fixture gh.exe"
    uv_binary = b"verified fixture uv.exe"
    gh_archive = _zip_archive("bin/gh.exe", gh_binary)
    uv_archive = _zip_archive("uv.exe", uv_binary)

    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    uv_asset = payload["uv"]["assets"][platform_key]
    uv_asset["sha256"] = _sha256(uv_archive)
    uv_asset["size_bytes"] = len(uv_archive)
    uv_asset["binary_path"] = "uv.exe"
    uv_asset["binary_sha256"] = _sha256(uv_binary)
    gh_asset = payload["github_cli"]["assets"][platform_key]
    gh_asset["sha256"] = _sha256(gh_archive)
    gh_asset["size_bytes"] = len(gh_archive)

    policy_path = tmp_path / "windows-policy.json"
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    gh_url = f"https://github.com/cli/cli/releases/download/v2.97.0/{gh_asset['archive_name']}"
    uv_url = f"https://github.com/astral-sh/uv/releases/download/0.12.1/{uv_asset['archive_name']}"
    downloader = FixtureDownloader({gh_url: gh_archive, uv_url: uv_archive})
    runner = FixtureRunner(_attestation_payload(uv_asset["archive_name"], _sha256(uv_archive)))

    receipt = bootstrap_module.bootstrap_uv(
        policy_path=policy_path,
        install_dir=tmp_path / "tools",
        receipt_path=tmp_path / "receipt.json",
        downloader=downloader,
        runner=runner,
        platform_id=("win32", architecture),
        temporary_root=tmp_path / "temporary",
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
    )

    assert receipt["status"] == "success"
    assert (tmp_path / "tools" / "uv.exe").read_bytes() == uv_binary
    assert runner.commands[-1][-1] == "--version"


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


def test_release_verifier_is_locked_but_excluded_from_general_setup() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert project["dependency-groups"]["release-verifier"] == ["pypi-attestations==0.0.30"]
    assert "dev" not in project["project"]["optional-dependencies"]
    assert all(
        "pypi-attestations==0.0.30" not in dependencies
        for dependencies in project["project"]["optional-dependencies"].values()
    )
    assert "uv sync --locked --no-default-groups --group release-verifier" in release
    assert (
        "uv run --locked --no-default-groups --group release-verifier "
        "python scripts/verify_pypi_attestations.py"
    ) in release
    assert "uv sync --locked --extra dev" not in release


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

    bootstrap_module._assert_uv_version(
        tmp_path / "uv",
        runner,
        expected_target="aarch64-apple-darwin",
    )


def test_uv_version_accepts_pinned_linux_target_metadata(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    runner = FixtureRunner(
        "[]",
        uv_version="uv 0.12.1 (x86_64-unknown-linux-gnu)",
    )

    bootstrap_module._assert_uv_version(
        tmp_path / "uv",
        runner,
        expected_target="x86_64-unknown-linux-gnu",
    )


@pytest.mark.parametrize(
    "reported_version",
    [
        "uv 0.12.2 (329541a50 2026-07-31 aarch64-apple-darwin)",
        "uv 0.12.1 (000000000 2026-07-31 aarch64-apple-darwin)",
        "uv 0.12.1 (x86_64-unknown-linux-gnu)",
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
        bootstrap_module._assert_uv_version(
            tmp_path / "uv",
            runner,
            expected_target="aarch64-apple-darwin",
        )


def test_valid_local_artifacts_complete_bootstrap_and_emit_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, uv_binary = _valid_fixture(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    install_dir = tmp_path / "tools" / "uv"
    receipts_before_uv_execution: list[dict[str, Any]] = []

    def receipt_observing_runner(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        normalized = tuple(str(token) for token in command)
        if Path(normalized[0]).name == "uv" and normalized[-1] == "--version":
            receipts_before_uv_execution.append(
                json.loads(receipt_path.read_text(encoding="utf-8"))
            )
        return runner(command, env=env, timeout=timeout)

    receipt = bootstrap_module.bootstrap_uv(
        policy_path=policy_path,
        install_dir=install_dir,
        receipt_path=receipt_path,
        downloader=downloader,
        runner=receipt_observing_runner,
        platform_id=("linux", "x86_64"),
        temporary_root=tmp_path / "temporary",
        now=lambda: datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )

    installed = install_dir / "uv"
    assert installed.read_bytes() == uv_binary
    if sys.platform != "win32":
        assert stat.S_IMODE(installed.stat().st_mode) == 0o700
    assert receipt == json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "success"
    assert receipt["failure_category"] is None
    assert receipt["tool"]["version"] == UV_VERSION
    assert receipt["provenance"]["source_commit"] == UV_SOURCE_COMMIT
    assert receipts_before_uv_execution == [receipt]
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
    gh_url = next(url for url in downloader.payloads if "cli/cli" in url)
    uv_url = next(url for url in downloader.payloads if "astral-sh/uv" in url)
    assert downloader.calls == [
        (gh_url, len(downloader.payloads[gh_url])),
        (uv_url, len(downloader.payloads[uv_url])),
    ]


def test_github_token_is_available_only_to_attestation_verifier(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "fixture-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-github-token")
    monkeypatch.setenv("GH_HOST", "enterprise.example.com")
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)

    bootstrap_module.bootstrap_uv(
        policy_path=policy_path,
        install_dir=tmp_path / "tools",
        receipt_path=tmp_path / "receipt.json",
        downloader=downloader,
        runner=runner,
        platform_id=("linux", "x86_64"),
        temporary_root=tmp_path / "temporary",
    )

    assert len(runner.commands) == len(runner.environments)
    for command, environment in zip(runner.commands, runner.environments, strict=True):
        if "attestation" in command:
            assert environment["GH_TOKEN"] == "fixture-gh-token"
            assert environment["GITHUB_TOKEN"] == "fixture-github-token"
            hostname_index = command.index("--hostname")
            assert command[hostname_index + 1] == "github.com"
        else:
            assert "GH_TOKEN" not in environment
            assert "GITHUB_TOKEN" not in environment


def test_attestation_timeout_is_coded_receipted_and_blocks_uv_execution(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, fixture_runner, _ = _valid_fixture(tmp_path)
    observed_timeout: float | None = None

    def timeout_runner(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal observed_timeout
        if "attestation" in command:
            observed_timeout = timeout
            raise subprocess.TimeoutExpired(command, timeout)
        return fixture_runner(command, env=env, timeout=timeout)

    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="ATTESTATION_VERIFICATION_TIMEOUT",
    ):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=timeout_runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert observed_timeout == bootstrap_module.ATTESTATION_TIMEOUT_SECONDS
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["failure_category"] == (
        "ATTESTATION_VERIFICATION_TIMEOUT"
    )
    assert not any(Path(command[0]).name == "uv" for command in fixture_runner.commands)


def test_wrong_uv_identity_is_never_published_to_the_cache(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    runner.uv_version = "uv 0.12.2"
    install_dir = tmp_path / "tools" / "uv"
    installed_uv = install_dir / "uv"
    receipt_path = tmp_path / "receipt.json"

    with pytest.raises(bootstrap_module.BootstrapError, match="UV_VERSION_MISMATCH"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=install_dir,
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    uv_version_commands = [
        command
        for command in runner.commands
        if Path(command[0]).name == "uv" and command[-1] == "--version"
    ]
    assert len(uv_version_commands) == 1
    assert Path(uv_version_commands[0][0]) != installed_uv
    assert not installed_uv.exists()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "failure"
    assert receipt["failure_category"] == "UV_VERSION_MISMATCH"


def test_bad_github_cli_is_never_executed(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    gh_url = next(url for url in downloader.payloads if "cli/cli" in url)
    downloader.payloads[gh_url] = _corrupt_same_size(downloader.payloads[gh_url])

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
    downloader.payloads[uv_url] = _corrupt_same_size(downloader.payloads[uv_url])

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
        (lambda policy: policy.update(schema_version=True), "POLICY_SCHEMA_UNSUPPORTED"),
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
        (
            lambda policy: policy["uv"]["assets"]["linux-x86_64"].pop("size_bytes"),
            "POLICY_FIELDS_INVALID",
        ),
        (
            lambda policy: policy["github_cli"]["assets"]["linux-x86_64"].update(size_bytes=0),
            "POLICY_VALUE_INVALID",
        ),
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


def test_symlinked_install_ancestor_fails_before_download(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".tools").symlink_to(outside, target_is_directory=True)

    with pytest.raises(bootstrap_module.BootstrapError, match="INSTALL_PATH_UNSAFE"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=repository / ".tools" / "uv",
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert downloader.calls == []
    assert runner.commands == []
    assert not (outside / "uv" / "uv").exists()
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["failure_category"] == "INSTALL_PATH_UNSAFE"


def test_non_directory_install_ancestor_fails_with_coded_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".tools").write_bytes(b"not a directory")
    receipt_path = tmp_path / "receipt.json"

    with pytest.raises(bootstrap_module.BootstrapError, match="INSTALL_PATH_UNSAFE"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=repository / ".tools" / "uv",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert downloader.calls == []
    assert runner.commands == []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["failure_category"] == "INSTALL_PATH_UNSAFE"


def test_install_path_inspection_failure_emits_coded_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    install_dir = tmp_path / "tools"
    installed_uv = install_dir / "uv"
    receipt_path = tmp_path / "receipt.json"
    real_lstat = bootstrap_module.os.lstat

    def fail_install_inspection(path: str | Path) -> Any:
        if Path(path) == installed_uv:
            raise PermissionError("unreadable install path")
        return real_lstat(path)

    monkeypatch.setattr(bootstrap_module.os, "lstat", fail_install_inspection)

    with pytest.raises(bootstrap_module.BootstrapError, match="INSTALL_PATH_UNSAFE"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=install_dir,
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert downloader.calls == []
    assert runner.commands == []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["failure_category"] == "INSTALL_PATH_UNSAFE"


def test_atomic_install_io_failure_is_sanitized_and_receipted(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    install_dir = tmp_path / "tools"
    installed_uv = install_dir / "uv"
    receipt_path = tmp_path / "receipt.json"
    sensitive_detail = str(tmp_path / "private-host-detail")
    real_replace = bootstrap_module.os.replace

    def fail_install_replace(
        source: str | Path,
        destination: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(destination).name == installed_uv.name and kwargs.get("dst_dir_fd"):
            raise PermissionError(sensitive_detail)
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(bootstrap_module.os, "replace", fail_install_replace)

    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="INSTALL_WRITE_FAILED",
    ) as failure:
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=install_dir,
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert json.loads(receipt_text)["failure_category"] == "INSTALL_WRITE_FAILED"
    assert sensitive_detail not in str(failure.value)
    assert sensitive_detail not in receipt_text
    assert not installed_uv.exists()
    assert list(install_dir.glob(".uv.*.tmp")) == []


def test_receipt_cleanup_failure_does_not_mask_stable_write_error(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    sensitive_detail = str(tmp_path / "private-receipt-detail")

    def fail_replace(
        _source: str | Path,
        _destination: str | Path,
        *args: object,
        **kwargs: object,
    ) -> NoReturn:
        del args, kwargs
        raise PermissionError(sensitive_detail)

    def fail_cleanup(
        _path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> NoReturn:
        del args, kwargs
        raise PermissionError(sensitive_detail)

    monkeypatch.setattr(bootstrap_module.os, "replace", fail_replace)
    monkeypatch.setattr(bootstrap_module.os, "unlink", fail_cleanup)

    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="RECEIPT_WRITE_FAILED",
    ) as failure:
        bootstrap_module._write_receipt(
            receipt_path,
            {"schema_version": 1, "status": "failure", "failure_category": "FIXTURE"},
        )

    assert failure.value.code == "RECEIPT_WRITE_FAILED"
    assert sensitive_detail not in str(failure.value)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory-descriptor regression")
def test_receipt_parent_swap_cannot_redirect_atomic_commit(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    tools = repository / ".tools"
    receipts = tools / "receipts"
    receipts.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "receipts").mkdir(parents=True)
    parked_tools = repository / "parked-tools"
    receipt_path = receipts / "uv-bootstrap.json"
    real_replace = bootstrap_module.os.replace
    swapped = False

    def swap_parent_then_replace(
        source: str | Path,
        destination: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        if not swapped and Path(destination).name == receipt_path.name:
            swapped = True
            real_replace(tools, parked_tools)
            tools.symlink_to(outside, target_is_directory=True)
            redirected_temporary = outside / "receipts" / Path(source).name
            redirected_temporary.write_text("attacker-controlled\n", encoding="utf-8")
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(bootstrap_module.os, "replace", swap_parent_then_replace)
    receipt = {
        "schema_version": 1,
        "status": "failure",
        "failure_category": "FIXTURE",
    }

    bootstrap_module._write_receipt(receipt_path, receipt)

    assert swapped
    assert (
        json.loads((parked_tools / "receipts" / receipt_path.name).read_text(encoding="utf-8"))
        == receipt
    )
    assert not (outside / "receipts" / receipt_path.name).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory-descriptor regression")
def test_install_parent_swap_cannot_redirect_atomic_commit(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    tools = repository / ".tools"
    install_dir = tools / "uv"
    install_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "uv").mkdir(parents=True)
    parked_tools = repository / "parked-tools"
    source = tmp_path / "verified-uv"
    source.write_bytes(b"verified executable")
    destination = install_dir / "uv"
    real_replace = bootstrap_module.os.replace
    swapped = False

    def swap_parent_then_replace(
        temporary: str | Path,
        target: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        if not swapped and Path(target).name == destination.name:
            swapped = True
            real_replace(tools, parked_tools)
            tools.symlink_to(outside, target_is_directory=True)
            redirected_temporary = outside / "uv" / Path(temporary).name
            redirected_temporary.write_bytes(b"attacker-controlled")
        real_replace(temporary, target, *args, **kwargs)

    monkeypatch.setattr(bootstrap_module.os, "replace", swap_parent_then_replace)

    bootstrap_module._atomic_install(source, destination)

    assert swapped
    assert (parked_tools / "uv" / destination.name).read_bytes() == source.read_bytes()
    assert not (outside / "uv" / destination.name).exists()


def test_symlinked_receipt_ancestor_blocks_success_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".tools").symlink_to(outside, target_is_directory=True)
    receipt_path = repository / ".tools" / "receipts" / "uv-bootstrap.json"

    with pytest.raises(bootstrap_module.BootstrapError, match="RECEIPT_PATH_UNSAFE"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "safe-tools",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert not (outside / "receipts" / "uv-bootstrap.json").exists()
    assert not any(Path(command[0]).name == "uv" for command in runner.commands)


def test_symlinked_receipt_ancestor_blocks_failure_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".tools").symlink_to(outside, target_is_directory=True)
    receipt_path = repository / ".tools" / "receipts" / "uv-bootstrap.json"
    runner.attestation_returncode = 1

    with pytest.raises(bootstrap_module.BootstrapError, match="RECEIPT_PATH_UNSAFE"):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "safe-tools",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    assert not (outside / "receipts" / "uv-bootstrap.json").exists()
    assert not any(Path(command[0]).name == "uv" for command in runner.commands)


def test_attestation_authentication_failure_is_distinct_and_sanitized(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    secret = "github_pat_fixture-secret"
    local_path = str(tmp_path / "sensitive-local-path")
    runner.attestation_returncode = 4
    runner.attestation_stderr = f"not logged in; token={secret}; path={local_path}"

    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="VERIFIER_AUTHENTICATION_FAILED",
    ) as failure:
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=tmp_path / "receipt.json",
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    failure_text = str(failure.value)
    assert "gh auth login --hostname github.com" in failure_text
    assert "GH_TOKEN" in failure_text
    assert secret not in failure_text
    assert local_path not in failure_text
    receipt_text = (tmp_path / "receipt.json").read_text(encoding="utf-8")
    assert json.loads(receipt_text)["failure_category"] == "VERIFIER_AUTHENTICATION_FAILED"
    assert secret not in receipt_text
    assert local_path not in receipt_text
    assert not any(Path(command[0]).name == "uv" for command in runner.commands)


class _DownloadResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_length: str | None) -> None:
        super().__init__(payload)
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.fp = SimpleNamespace(
            raw=SimpleNamespace(
                _sock=SimpleNamespace(settimeout=lambda _timeout: None),
            )
        )


class _ClosingDownloadResponse(_DownloadResponse):
    def read1(self, size: int = -1) -> bytes:
        chunk = super().read1(size)
        if self.tell() == len(self.getvalue()):
            self.fp = None
        return chunk


class _TrackedDownloadResponse(_DownloadResponse):
    def __init__(self, payload: bytes, content_length: str | None) -> None:
        super().__init__(payload, content_length)
        self.closed_event = threading.Event()

    def close(self) -> None:
        super().close()
        self.closed_event.set()


class _RecordingSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class _DeadlineResponse:
    def __init__(self, clock: dict[str, float]) -> None:
        self.headers = {"Content-Length": "2"}
        self.clock = clock
        self.socket = _RecordingSocket()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))
        self.read_count = 0

    def __enter__(self) -> _DeadlineResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        raise AssertionError("bounded downloads must not use accumulating read()")

    def read1(self, _size: int) -> bytes:
        self.read_count += 1
        if self.read_count == 1:
            assert self.socket.timeouts[-1] == pytest.approx(10.0)
            self.clock["now"] = 59.5
            return b"a"
        assert self.socket.timeouts[-1] == pytest.approx(0.5)
        self.clock["now"] = 60.0
        raise TimeoutError("bounded fixture read timed out")


@pytest.mark.parametrize(
    ("payload", "content_length", "expected_size"),
    [
        (b"abc", "4", 3),
        (b"abcd", "3", 3),
        (b"ab", "3", 3),
        (b"abc", None, 3),
    ],
)
def test_download_rejects_unreviewed_or_mismatched_sizes(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    content_length: str | None,
    expected_size: int,
) -> None:
    monkeypatch.setattr(
        bootstrap_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, content_length),
    )
    destination = tmp_path / "asset.tar.gz"

    with pytest.raises(bootstrap_module.BootstrapError, match="DOWNLOAD_SIZE_MISMATCH"):
        bootstrap_module._download(
            "https://github.com/example/release/asset.tar.gz",
            destination,
            expected_size,
        )

    assert not destination.exists()


def test_download_normalizes_malformed_http_status_line(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def malformed_response(*_args: object, **_kwargs: object) -> NoReturn:
        raise http.client.BadStatusLine("not-http")

    monkeypatch.setattr(bootstrap_module.urllib.request, "urlopen", malformed_response)
    destination = tmp_path / "asset.tar.gz"

    with pytest.raises(bootstrap_module.BootstrapError, match="DOWNLOAD_FAILED"):
        bootstrap_module._download(
            "https://github.com/example/release/asset.tar.gz",
            destination,
            3,
        )

    assert not destination.exists()


def test_download_normalizes_incomplete_http_body(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IncompleteResponse(_DownloadResponse):
        def read1(self, _size: int) -> bytes:
            raise http.client.IncompleteRead(b"a", 3)

    monkeypatch.setattr(
        bootstrap_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: IncompleteResponse(b"abc", "3"),
    )
    destination = tmp_path / "asset.tar.gz"

    with pytest.raises(bootstrap_module.BootstrapError, match="DOWNLOAD_FAILED"):
        bootstrap_module._download(
            "https://github.com/example/release/asset.tar.gz",
            destination,
            3,
        )

    assert not destination.exists()


def test_download_stops_when_reviewed_content_length_closes_transport(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"abc"
    monkeypatch.setattr(
        bootstrap_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _ClosingDownloadResponse(payload, str(len(payload))),
    )
    destination = tmp_path / "asset.tar.gz"

    bootstrap_module._download(
        "https://github.com/example/release/asset.tar.gz",
        destination,
        len(payload),
    )

    assert destination.read_bytes() == payload


def test_download_deadline_includes_response_opening(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"abc"
    response = _TrackedDownloadResponse(payload, str(len(payload)))
    release_response = threading.Event()

    def delayed_urlopen(*_args: object, **_kwargs: object) -> _TrackedDownloadResponse:
        release_response.wait(timeout=1.0)
        return response

    monkeypatch.setattr(bootstrap_module.urllib.request, "urlopen", delayed_urlopen)
    monkeypatch.setattr(bootstrap_module, "DOWNLOAD_DEADLINE_SECONDS", 0.05)
    destination = tmp_path / "asset.tar.gz"
    started_at = time.perf_counter()

    try:
        with pytest.raises(
            bootstrap_module.BootstrapError,
            match="DOWNLOAD_DEADLINE_EXCEEDED",
        ):
            bootstrap_module._download(
                "https://github.com/example/release/asset.tar.gz",
                destination,
                len(payload),
            )
    finally:
        release_response.set()

    assert time.perf_counter() - started_at < 0.5
    assert response.closed_event.wait(timeout=0.5)
    assert not destination.exists()


def test_download_fails_when_the_bounded_deadline_expires(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"abc"
    monotonic_values = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(
        bootstrap_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(payload, str(len(payload))),
    )
    monkeypatch.setattr(bootstrap_module.time, "monotonic", lambda: next(monotonic_values))
    destination = tmp_path / "asset.tar.gz"

    with pytest.raises(bootstrap_module.BootstrapError, match="DOWNLOAD_DEADLINE_EXCEEDED"):
        bootstrap_module._download(
            "https://github.com/example/release/asset.tar.gz",
            destination,
            len(payload),
        )

    assert not destination.exists()


def test_download_enforces_remaining_deadline_inside_each_read(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    response = _DeadlineResponse(clock)
    monkeypatch.setattr(
        bootstrap_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )
    monkeypatch.setattr(
        bootstrap_module.time,
        "monotonic",
        lambda: clock["now"],
    )
    destination = tmp_path / "asset.tar.gz"

    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="DOWNLOAD_DEADLINE_EXCEEDED",
    ):
        bootstrap_module._download(
            "https://github.com/example/release/asset.tar.gz",
            destination,
            2,
        )

    assert response.socket.timeouts == pytest.approx([10.0, 0.5])
    assert not destination.exists()


def test_download_finds_socket_through_urllib_response_wrapper(
    bootstrap_module: Any,
) -> None:
    clock = {"now": 0.0}
    response = _DeadlineResponse(clock)
    response.fp = SimpleNamespace(fp=response.fp)

    bootstrap_module._set_response_read_timeout(response, 4.5)

    assert response.socket.timeouts == pytest.approx([4.5])


@pytest.mark.parametrize(
    ("failure_point", "error"),
    [
        ("unreadable-policy", "POLICY_READ_FAILED"),
        ("invalid-policy", "POLICY_SCHEMA_UNSUPPORTED"),
        ("unsupported-platform", "PLATFORM_UNSUPPORTED"),
        ("policy-hash", "ARTIFACT_READ_FAILED"),
        ("clock", "CLOCK_INVALID"),
    ],
)
def test_initialization_failures_emit_minimal_sanitized_receipts(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    error: str,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    platform_id = ("linux", "x86_64")

    def aware_now() -> datetime:
        return datetime(2026, 8, 10, tzinfo=UTC)

    now: Callable[[], datetime] = aware_now

    if failure_point == "unreadable-policy":
        policy_path = tmp_path / "private-policy-location.json"
    elif failure_point == "invalid-policy":
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["schema_version"] = 2
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
    elif failure_point == "unsupported-platform":
        platform_id = ("private-operating-system", "x86_64")
    elif failure_point == "policy-hash":

        def fail_policy_hash(_path: Path) -> str:
            raise bootstrap_module.BootstrapError(
                "ARTIFACT_READ_FAILED",
                f"sensitive hash path: {tmp_path}",
            )

        monkeypatch.setattr(bootstrap_module, "_sha256_file", fail_policy_hash)
    elif failure_point == "clock":

        def naive_now() -> datetime:
            return datetime(2026, 8, 10)

        now = naive_now

    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(bootstrap_module.BootstrapError, match=error):
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=platform_id,
            temporary_root=tmp_path / "temporary",
            now=now,
        )

    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert json.loads(receipt_text) == {
        "schema_version": 1,
        "status": "failure",
        "failure_category": error,
    }
    assert str(tmp_path) not in receipt_text
    assert downloader.calls == []
    assert runner.commands == []


def test_temporary_workspace_failure_is_coded_and_receipted(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path, downloader, runner, _ = _valid_fixture(tmp_path)
    sensitive_detail = str(tmp_path / "private-temporary-root")

    def fail_temporary_directory(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError(sensitive_detail)

    monkeypatch.setattr(
        bootstrap_module.tempfile,
        "TemporaryDirectory",
        fail_temporary_directory,
    )
    receipt_path = tmp_path / "receipt.json"

    with pytest.raises(
        bootstrap_module.BootstrapError,
        match="TEMPORARY_WORKSPACE_FAILED",
    ) as failure:
        bootstrap_module.bootstrap_uv(
            policy_path=policy_path,
            install_dir=tmp_path / "tools",
            receipt_path=receipt_path,
            downloader=downloader,
            runner=runner,
            platform_id=("linux", "x86_64"),
            temporary_root=tmp_path / "temporary",
        )

    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert json.loads(receipt_text)["failure_category"] == "TEMPORARY_WORKSPACE_FAILED"
    assert sensitive_detail not in str(failure.value)
    assert sensitive_detail not in receipt_text
    assert downloader.calls == []
    assert runner.commands == []


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


@pytest.mark.parametrize(
    ("architecture", "platform_key"),
    [
        ("AMD64", "windows-x86_64"),
        ("ARM64", "windows-arm64"),
    ],
)
def test_verify_installed_resolves_setup_uv_windows_output_to_executable(
    tmp_path: Path,
    bootstrap_module: Any,
    architecture: str,
    platform_key: str,
) -> None:
    uv_binary = b"verified setup-uv Windows executable"
    setup_uv_output_path = tmp_path / "uv"
    installed_uv = tmp_path / "uv.exe"
    installed_uv.write_bytes(uv_binary)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["uv"]["assets"][platform_key]["binary_sha256"] = _sha256(uv_binary)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    runner = FixtureRunner("[]")

    bootstrap_module.verify_installed_uv(
        policy_path=policy_path,
        uv_path=setup_uv_output_path,
        runner=runner,
        platform_id=("win32", architecture),
    )

    assert runner.commands == [(str(installed_uv), "--version")]


def test_verify_installed_rejects_unexpected_windows_executable_name(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    runner = FixtureRunner("[]")

    with pytest.raises(bootstrap_module.BootstrapError, match="INSTALLED_UV_PATH_INVALID"):
        bootstrap_module.verify_installed_uv(
            policy_path=POLICY_PATH,
            uv_path=tmp_path / "uvx.exe",
            runner=runner,
            platform_id=("win32", "ARM64"),
        )

    assert runner.commands == []


def test_record_post_preflight_failure_overwrites_success_status(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    receipt = _verified_success_receipt(tmp_path, bootstrap_module, receipt_path)

    failed_receipt = bootstrap_module.record_post_preflight_failure(
        receipt_path,
        "SETUP_UV_ACTION_FAILED",
    )

    expected = {
        **receipt,
        "status": "failure",
        "failure_category": "SETUP_UV_ACTION_FAILED",
    }
    assert failed_receipt == expected
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == expected


def test_record_post_preflight_failure_rejects_unknown_category(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    _verified_success_receipt(tmp_path, bootstrap_module, receipt_path)

    with pytest.raises(bootstrap_module.BootstrapError, match="FAILURE_CATEGORY_INVALID"):
        bootstrap_module.record_post_preflight_failure(receipt_path, "UNREVIEWED_FAILURE")


@pytest.mark.parametrize("mutation", ["unknown-field", "missing-field"])
def test_record_post_preflight_failure_rejects_noncanonical_schema(
    tmp_path: Path,
    bootstrap_module: Any,
    mutation: str,
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    receipt = _verified_success_receipt(tmp_path, bootstrap_module, receipt_path)
    if mutation == "unknown-field":
        receipt["unreviewed_extension"] = "not permitted in schema v1"
    else:
        del receipt["provenance"]
    bootstrap_module._write_receipt(receipt_path, receipt)

    with pytest.raises(bootstrap_module.BootstrapError, match="RECEIPT_STATE_INVALID"):
        bootstrap_module.record_post_preflight_failure(
            receipt_path,
            "SETUP_UV_ACTION_FAILED",
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("not-json\n", "RECEIPT_READ_FAILED"),
        ("[]\n", "RECEIPT_STATE_INVALID"),
    ],
)
def test_record_post_preflight_failure_rejects_invalid_receipt_content(
    tmp_path: Path,
    bootstrap_module: Any,
    payload: str,
    error: str,
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    receipt_path.write_text(payload, encoding="utf-8")

    with pytest.raises(bootstrap_module.BootstrapError, match=error):
        bootstrap_module.record_post_preflight_failure(
            receipt_path,
            "SETUP_UV_ACTION_FAILED",
        )


def test_record_post_preflight_failure_rejects_missing_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
) -> None:
    with pytest.raises(bootstrap_module.BootstrapError, match="RECEIPT_READ_FAILED"):
        bootstrap_module.record_post_preflight_failure(
            tmp_path / "missing.json",
            "SETUP_UV_ACTION_FAILED",
        )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory-descriptor regression")
def test_record_post_preflight_failure_parent_swap_cannot_redirect_commit(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    tools = repository / ".tools"
    receipt_path = tools / "receipts" / "uv-bootstrap.json"
    receipt_path.parent.mkdir(parents=True)
    receipt = _verified_success_receipt(tmp_path / "fixture", bootstrap_module, receipt_path)
    outside = tmp_path / "outside"
    (outside / "receipts").mkdir(parents=True)
    parked_tools = repository / "parked-tools"
    real_read = bootstrap_module._read_receipt_from_parent

    def read_then_swap(destination: Path, parent_fd: int | None) -> dict[str, Any]:
        parsed = real_read(destination, parent_fd)
        tools.replace(parked_tools)
        tools.symlink_to(outside, target_is_directory=True)
        return parsed

    monkeypatch.setattr(bootstrap_module, "_read_receipt_from_parent", read_then_swap)

    failed_receipt = bootstrap_module.record_post_preflight_failure(
        receipt_path,
        "INSTALLED_UV_VERIFICATION_FAILED",
    )

    assert failed_receipt == {
        **receipt,
        "status": "failure",
        "failure_category": "INSTALLED_UV_VERIFICATION_FAILED",
    }
    assert (
        json.loads((parked_tools / "receipts" / receipt_path.name).read_text(encoding="utf-8"))
        == failed_receipt
    )
    assert not (outside / "receipts" / receipt_path.name).exists()


def test_record_post_preflight_failure_cli_updates_and_prints_receipt(
    tmp_path: Path,
    bootstrap_module: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    receipt = _verified_success_receipt(tmp_path, bootstrap_module, receipt_path)

    assert (
        bootstrap_module.main(
            [
                "record-failure",
                "--receipt",
                str(receipt_path),
                "--failure-category",
                "SETUP_UV_ACTION_FAILED",
            ]
        )
        == 0
    )

    rendered = capsys.readouterr()
    assert rendered.err == ""
    assert json.loads(rendered.out) == {
        **receipt,
        "status": "failure",
        "failure_category": "SETUP_UV_ACTION_FAILED",
    }


def test_bootstrap_cli_records_github_output_write_failure(
    tmp_path: Path,
    bootstrap_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt_path = tmp_path / "uv-bootstrap.json"
    receipt = _verified_success_receipt(
        tmp_path / "fixture",
        bootstrap_module,
        receipt_path,
    )
    github_output = tmp_path / "github-output"
    github_output.mkdir()
    monkeypatch.setattr(bootstrap_module, "bootstrap_uv", lambda **_kwargs: receipt)

    assert (
        bootstrap_module.main(
            [
                "bootstrap",
                "--receipt",
                str(receipt_path),
                "--github-output",
                str(github_output),
            ]
        )
        == 1
    )

    rendered = capsys.readouterr()
    assert "GITHUB_OUTPUT_WRITE_FAILED" in rendered.err
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {
        **receipt,
        "status": "failure",
        "failure_category": "GITHUB_OUTPUT_WRITE_FAILED",
    }


def test_composite_action_preflights_and_rehashes_pinned_setup_uv() -> None:
    assert ACTION_PATH.is_file()
    action = ACTION_PATH.read_text(encoding="utf-8")

    assert f"astral-sh/setup-uv@{SETUP_UV_COMMIT}" in action
    assert "GH_TOKEN: ${{ github.token }}" in action
    assert "python-version" in action
    assert "runner.os" in action
    assert "runner.arch" in action
    assert 'version: "0.12.1"' in action
    assert "checksum: ${{ steps.preflight.outputs.checksum }}" in action
    assert "download-from-astral-mirror: false" in action
    assert "enable-cache: true" in action
    assert "cache-dependency-glob: uv.lock" in action
    assert (
        "cache-suffix: py-${{ inputs.python-version }}-${{ runner.os }}-${{ runner.arch }}"
        in action
    )
    assert "bootstrap_uv.py verify-installed" in action
    assert "bootstrap_uv.py record-failure" in action
    assert "mkdir -p .tools/receipts" not in action
    assert action.count("continue-on-error: true") == 2
    assert "steps.setup-uv.outcome != 'success'" in action
    assert "steps.verify-installed.outcome != 'success'" in action
    assert "SETUP_UV_ACTION_FAILED" in action
    assert "INSTALLED_UV_VERIFICATION_FAILED" in action
    assert "VERIFIED_UV_RECEIPT_PATH: ${{ steps.receipt.outputs.receipt_path }}" in action
    assert "VERIFIED_UV_INSTALLED_PATH: ${{ steps.setup-uv.outputs.uv-path }}" in action
    assert action.count("VERIFIED_UV_RUNNER_OS: ${{ runner.os }}") == 2
    assert action.count("VERIFIED_UV_RUNNER_ARCH: ${{ runner.arch }}") == 2
    assert '--receipt "$VERIFIED_UV_RECEIPT_PATH"' in action
    assert '--uv-path "$VERIFIED_UV_INSTALLED_PATH"' in action
    assert action.count('--platform "$VERIFIED_UV_RUNNER_OS"') == 2
    assert action.count('--architecture "$VERIFIED_UV_RUNNER_ARCH"') == 2
    assert "GITHUB_PATH" not in action
    assert "value: ${{ steps.receipt.outputs.receipt_path }}" in action
    assert "value: ${{ steps.setup-uv.outputs.uv-path }}" in action
    assert "if: ${{ always() }}" in action
    assert "if-no-files-found: error" in action
    assert "include-hidden-files: true" in action
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in action


def test_verify_installed_cli_requires_an_explicit_binary_path(
    bootstrap_module: Any,
) -> None:
    with pytest.raises(SystemExit):
        bootstrap_module._parser().parse_args(["verify-installed"])

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'shutil.which("uv")' not in source
