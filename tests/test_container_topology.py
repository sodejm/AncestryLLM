"""Fail-closed contracts for the reviewed OCI and Compose topology."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.container_policy import (
    ContainerPolicyError,
    validate_repository_topology,
    validate_runtime_inputs,
)
from scripts.container_policy import main as container_policy_main

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).parents[1]
BASE = ROOT / "containers" / "compose.yaml"
LOCAL = ROOT / "containers" / "compose.local.yaml"
REMOTE = ROOT / "containers" / "compose.remote.yaml"
DOCKERFILE = ROOT / "containers" / "Dockerfile"


def _base() -> dict[str, object]:
    return json.loads(BASE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("overlay", [LOCAL, REMOTE])
def test_reviewed_topology_and_overlays_pass_policy(overlay: Path) -> None:
    report = validate_repository_topology(BASE, overlay, DOCKERFILE)

    assert report == {
        "schema_version": 1,
        "status": "pass",
        "services": ["gateway", "worker"],
        "worker_default_enabled": False,
        "application_routes_enabled": False,
        "schema_migrations_enabled": False,
        "native_platforms": ["linux/amd64", "linux/arm64"],
    }


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value.update({"x-ancestryllm-schema-version": 2}),
            "COMPOSE_SCHEMA_UNSUPPORTED",
        ),
        (lambda value: value["services"].update({"redis": {}}), "COMPOSE_SERVICE_UNSUPPORTED"),
        (
            lambda value: value["networks"].update({"default": {}}),
            "COMPOSE_DEFAULT_NETWORK_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"ports": ["8000:8000"]}),
            "COMPOSE_PORT_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"network_mode": "host"}),
            "COMPOSE_HOST_NETWORK_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"privileged": True}),
            "COMPOSE_PRIVILEGED_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"devices": ["/dev/null"]}),
            "COMPOSE_DEVICE_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"environment": {"TOKEN": "secret"}}),
            "COMPOSE_ENVIRONMENT_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update(
                {"volumes": ["./data:/var/lib/ancestryllm"]}
            ),
            "COMPOSE_BIND_MOUNT_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update(
                {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]}
            ),
            "COMPOSE_DOCKER_SOCKET_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"user": "0:0"}),
            "COMPOSE_ROOT_USER_FORBIDDEN",
        ),
        (
            lambda value: value["services"]["gateway"].update({"read_only": False}),
            "COMPOSE_READ_ONLY_REQUIRED",
        ),
        (
            lambda value: value["services"]["gateway"].update({"cap_drop": []}),
            "COMPOSE_CAPABILITIES_UNSAFE",
        ),
        (
            lambda value: value["services"]["gateway"].update({"security_opt": []}),
            "COMPOSE_NO_NEW_PRIVILEGES_REQUIRED",
        ),
        (
            lambda value: value["services"]["worker"].update({"profiles": []}),
            "COMPOSE_WORKER_PROFILE_REQUIRED",
        ),
    ],
)
def test_unsafe_topology_mutations_fail_with_stable_codes(
    mutate: Callable[[dict[str, object]], None], code: str
) -> None:
    document = copy.deepcopy(_base())
    mutate(document)

    with pytest.raises(ContainerPolicyError, match=code):
        validate_repository_topology(BASE, LOCAL, DOCKERFILE, base_document=document)


def test_images_and_platforms_are_fail_closed_interpolations() -> None:
    document = _base()
    gateway = document["services"]["gateway"]  # type: ignore[index]
    worker = document["services"]["worker"]  # type: ignore[index]

    assert gateway["image"].startswith("${ANCESTRYLLM_GATEWAY_IMAGE:?")
    assert worker["image"].startswith("${ANCESTRYLLM_WORKER_IMAGE:?")
    assert gateway["platform"] == "${ANCESTRYLLM_PLATFORM:?Set linux/amd64 or linux/arm64}"
    assert worker["profiles"] == ["worker"]
    assert "ports" not in gateway
    assert "expose" not in gateway


@pytest.mark.parametrize(
    ("overlay", "expected_cpus", "expected_memory_gib", "expected_pids"),
    [
        (LOCAL, 2.0, 4, 256),
        (REMOTE, 4.0, 8, 512),
    ],
)
def test_profile_resource_limits_are_aggregate_when_worker_is_enabled(
    overlay: Path,
    expected_cpus: float,
    expected_memory_gib: int,
    expected_pids: int,
) -> None:
    document = json.loads(overlay.read_text(encoding="utf-8"))
    services = document["services"]

    assert sum(service["cpus"] for service in services.values()) == expected_cpus
    assert (
        sum(int(service["mem_limit"].removesuffix("g")) for service in services.values())
        == expected_memory_gib
    )
    assert sum(service["pids_limit"] for service in services.values()) == expected_pids


def test_base_logging_budget_is_aggregate_when_worker_is_enabled() -> None:
    services = _base()["services"]
    options = [service["logging"]["options"] for service in services.values()]

    assert sum(int(value["max-file"]) for value in options) == 5
    assert (
        sum(int(value["max-size"].removesuffix("m")) * int(value["max-file"]) for value in options)
        == 100
    )


def test_dockerfile_pins_multiarch_inputs_and_minimal_runtime() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa816" in text
    assert "python:3.12.11-slim-bookworm@sha256:519591d6871b" in text
    assert "USER 65532:65532" in text
    assert "AS gateway" in text
    assert "AS worker" in text
    assert "container_gateway" in text
    assert "container_worker" in text
    for forbidden in ("apt-get", "curl ", "wget ", "sudo ", "pip install"):
        assert forbidden not in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace(
            "ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:"
            "cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded",
            "ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest\n"
            "# ghcr.io/astral-sh/uv:0.12.1@sha256:"
            "cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded",
        ),
        lambda text: text.replace(
            "USER 65532:65532",
            "USER 0:0\n# USER 65532:65532",
        ),
        lambda text: "ARG UNUSED_IMAGE=example.invalid/unused:latest\n" + text,
        lambda text: text + "\nFROM busybox:latest AS unreviewed\n",
        lambda text: text + "\nRUN true\n",
    ],
)
def test_dockerfile_policy_rejects_comment_bypasses_and_extra_instructions(
    tmp_path: Path,
    mutate: Callable[[str], str],
) -> None:
    candidate = tmp_path / "Dockerfile"
    candidate.write_text(mutate(DOCKERFILE.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(ContainerPolicyError, match="CONTAINER_DOCKERFILE_INVALID"):
        validate_repository_topology(BASE, LOCAL, candidate)


def test_runtime_inputs_require_digest_qualified_images_and_native_platform() -> None:
    digest = "a" * 64
    inputs = validate_runtime_inputs(
        f"ghcr.io/example/team/ancestryllm-gateway:v1@sha256:{digest}",
        f"ghcr.io/example/ancestryllm-worker:v1@sha256:{digest}",
        "linux/arm64",
    )

    assert inputs["platform"] == "linux/arm64"


@pytest.mark.parametrize(
    ("gateway", "worker", "platform", "code"),
    [
        (
            "ancestryllm-gateway:latest",
            "registry.example/worker:v1@sha256:" + "a" * 64,
            "linux/amd64",
            "CONTAINER_IMAGE_REFERENCE_INVALID",
        ),
        (
            "registry.example/gateway:v1@sha256:" + "a" * 64,
            "registry.example/worker:v1@sha256:" + "a" * 64,
            "linux/ppc64le",
            "CONTAINER_PLATFORM_UNSUPPORTED",
        ),
    ],
)
def test_runtime_inputs_fail_closed(gateway: str, worker: str, platform: str, code: str) -> None:
    with pytest.raises(ContainerPolicyError, match=code):
        validate_runtime_inputs(gateway, worker, platform)


def test_runtime_inputs_reject_adversarial_repeated_separators() -> None:
    digest = "a" * 64
    repeated_separators = "!/" * 100_000 + "!"

    with pytest.raises(ContainerPolicyError, match="CONTAINER_IMAGE_REFERENCE_INVALID"):
        validate_runtime_inputs(
            repeated_separators,
            f"registry.example/worker:v1@sha256:{digest}",
            "linux/amd64",
        )


def test_policy_cli_rejects_unresolved_runtime_inputs_before_compose() -> None:
    digest = "a" * 64

    with pytest.raises(ContainerPolicyError, match="CONTAINER_IMAGE_REFERENCE_INVALID"):
        container_policy_main(
            [
                "--base",
                str(BASE),
                "--overlay",
                str(LOCAL),
                "--dockerfile",
                str(DOCKERFILE),
                "--gateway-image",
                "ancestryllm-gateway:latest",
                "--worker-image",
                f"registry.example/worker:v1@sha256:{digest}",
                "--platform",
                "linux/amd64",
            ]
        )


def test_ci_builds_on_native_amd64_and_arm64_runners() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    container_job = workflow.split("\n  container:\n", maxsplit=1)[1].split(
        "\n  workflow-audit:\n", maxsplit=1
    )[0]

    assert container_job.count("arch: amd64") == 1
    assert container_job.count("arch: arm64") == 1
    assert container_job.count("runner: ubuntu-24.04\n") == 1
    assert container_job.count("runner: ubuntu-24.04-arm\n") == 1
    assert container_job.count("platform: linux/amd64") == 1
    assert container_job.count("platform: linux/arm64") == 1
    assert container_job.count('docker build --platform "$PLATFORM"') == 2
    assert "--target gateway" in container_job
    assert "--target worker" in container_job
    assert "make container-compose-config" in container_job
    assert "docker compose \\" not in container_job
    assert "scripts/container_ci_smoke.py" in container_job
    assert "container-policy-evidence-${{ matrix.arch }}" in container_job
    assert "setup-qemu-action" not in workflow
    assert "setup-buildx-action" not in workflow


def test_ci_runs_native_container_rows_only_for_container_owned_changes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    changes_job = workflow.split("\n  changes:\n", maxsplit=1)[1].split(
        "\n  lockfile:\n", maxsplit=1
    )[0]
    container_job = workflow.split("\n  container:\n", maxsplit=1)[1].split(
        "\n  workflow-audit:\n", maxsplit=1
    )[0]
    pr_gate = workflow.split("\n  pr-gate:\n", maxsplit=1)[1]

    assert "containers: ${{ steps.classify.outputs.containers }}" in changes_job
    assert 'echo "containers=true" >> "$GITHUB_OUTPUT"' in changes_job
    for path_pattern in (
        "containers/*",
        "scripts/container_*.py",
        "src/*",
        "tests/test_container_*.py",
        "Makefile|pyproject.toml|uv.lock|README.md|LICENSE",
        ".github/workflows/ci.yml",
    ):
        assert path_pattern in changes_job
    assert "needs: [changes, lockfile]" in container_job
    assert "if: needs.changes.outputs.containers == 'true'" in container_job
    assert 'CONTAINER_RESULT" != "success"' in pr_gate
    assert 'CONTAINER_RESULT" != "skipped"' in pr_gate


def test_make_validates_resolved_inputs_before_rendering_compose() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "\ncontainer-compose-config:" in makefile
    target = makefile.split("\ncontainer-compose-config:", maxsplit=1)[1].split("\n\n", maxsplit=1)[
        0
    ]
    assert target.count("scripts/container_policy.py") == 2
    assert target.count("docker compose") == 2
    assert target.count("--gateway-image") == 2
    assert target.count("--worker-image") == 2
    assert target.count("--platform") == 2
    assert target.index("scripts/container_policy.py") < target.index("docker compose")
    assert target.count("&& \\\n\t\tdocker compose") == 2
