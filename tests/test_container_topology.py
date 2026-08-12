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

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).parents[1]
BASE = ROOT / "containers" / "compose.yaml"
LOCAL = ROOT / "containers" / "compose.local.yaml"
REMOTE = ROOT / "containers" / "compose.remote.yaml"
DOCKERFILE = ROOT / "containers" / "Dockerfile"


def _base() -> dict[str, object]:
    text = BASE.read_text(encoding="utf-8")
    stripped = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    return json.loads(stripped)


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


def test_runtime_inputs_require_digest_qualified_images_and_native_platform() -> None:
    digest = "a" * 64
    inputs = validate_runtime_inputs(
        f"ghcr.io/example/ancestryllm-gateway:v1@sha256:{digest}",
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
    assert "make container-policy" in container_job
    assert container_job.count("docker compose \\") == 2
    assert "scripts/container_ci_smoke.py" in container_job
    assert "container-policy-evidence-${{ matrix.arch }}" in container_job
    assert "setup-qemu-action" not in workflow
    assert "setup-buildx-action" not in workflow
