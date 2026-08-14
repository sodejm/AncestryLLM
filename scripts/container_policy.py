#!/usr/bin/env python3
"""Validate the reviewed production OCI and Compose contract without Docker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

_GATEWAY_IMAGE = "${ANCESTRYLLM_GATEWAY_IMAGE:?Set a digest-qualified gateway image}"
_WORKER_IMAGE = "${ANCESTRYLLM_WORKER_IMAGE:?Set a digest-qualified worker image}"
_PLATFORM = "${ANCESTRYLLM_PLATFORM:?Set linux/amd64 or linux/arm64}"
_DATA_MOUNT = "ancestryllm-data:/var/lib/ancestryllm:ro"
_TMPFS = [
    "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",  # noqa: S108 - reviewed private tmpfs
    "/run/ancestryllm:rw,noexec,nosuid,nodev,size=1m,mode=0700,uid=65532,gid=65532",
]
_BASE_SERVICE_KEYS = {
    "image",
    "platform",
    "pull_policy",
    "user",
    "read_only",
    "init",
    "cap_drop",
    "security_opt",
    "tmpfs",
    "volumes",
    "networks",
    "healthcheck",
    "restart",
    "stop_signal",
    "stop_grace_period",
    "cpus",
    "mem_limit",
    "pids_limit",
    "logging",
    "labels",
}
_OVERLAY_SERVICE_KEYS = {"cpus", "mem_limit", "pids_limit", "labels"}
_LOWER_HEX_DIGITS = frozenset("0123456789abcdef")
_SUPPORTED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
_BASE_RESOURCES = {
    "gateway": (1.5, "3g", 192),
    "worker": (0.5, "1g", 64),
}
_PROFILE_RESOURCES = {
    "compose.local.yaml": _BASE_RESOURCES,
    "compose.remote.yaml": {
        "gateway": (3.0, "6g", 384),
        "worker": (1.0, "2g", 128),
    },
}
_PROFILE_TOTALS = {
    "compose.local.yaml": (2.0, 4, 256),
    "compose.remote.yaml": (4.0, 8, 512),
}
_LOGGING_OPTIONS = {
    "gateway": {"max-size": "20m", "max-file": "3"},
    "worker": {"max-size": "20m", "max-file": "2"},
}
_DOCKERFILE_INSTRUCTIONS = (
    (
        "ARG",
        "UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:"
        "cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded",
    ),
    (
        "ARG",
        "PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:"
        "519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7",
    ),
    ("FROM", "${UV_IMAGE} AS uv"),
    ("FROM", "${PYTHON_IMAGE} AS builder"),
    (
        "ENV",
        "PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy "
        "UV_PROJECT_ENVIRONMENT=/opt/ancestryllm",
    ),
    ("COPY", "--from=uv /uv /usr/local/bin/uv"),
    ("WORKDIR", "/source"),
    ("COPY", "pyproject.toml uv.lock README.md LICENSE ./"),
    ("RUN", "uv sync --locked --no-default-groups --no-editable --no-install-project"),
    ("COPY", "src ./src"),
    (
        "RUN",
        "uv sync --locked --no-default-groups --no-editable && "
        "/opt/ancestryllm/bin/python -m ancestryllm.container_inventory "
        "--output /opt/ancestryllm/package-inventory.json",
    ),
    ("FROM", "${PYTHON_IMAGE} AS runtime"),
    ("ARG", "APP_VERSION=0.5.0"),
    (
        "LABEL",
        'org.opencontainers.image.source="https://github.com/sodejm/AncestryLLM" '
        'org.opencontainers.image.version="${APP_VERSION}" '
        'org.opencontainers.image.licenses="MIT"',
    ),
    (
        "ENV",
        "PATH=/opt/ancestryllm/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
    ),
    ("COPY", "--from=builder /opt/ancestryllm /opt/ancestryllm"),
    ("COPY", "LICENSE /usr/share/licenses/ancestryllm/LICENSE"),
    ("USER", "65532:65532"),
    ("WORKDIR", "/var/lib/ancestryllm"),
    ("FROM", "runtime AS gateway"),
    (
        "ENTRYPOINT",
        '["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_gateway"]',
    ),
    (
        "HEALTHCHECK",
        "--interval=10s --timeout=3s --start-period=10s --retries=6 "
        'CMD ["/opt/ancestryllm/bin/python", "-m", '
        '"ancestryllm.container_healthcheck"]',
    ),
    ("FROM", "runtime AS worker"),
    (
        "ENTRYPOINT",
        '["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_worker"]',
    ),
    (
        "HEALTHCHECK",
        "--interval=10s --timeout=3s --start-period=5s --retries=6 "
        'CMD ["/opt/ancestryllm/bin/python", "-m", '
        '"ancestryllm.container_healthcheck", "--worker"]',
    ),
)


class ContainerPolicyError(RuntimeError):
    """A stable, sanitized repository container-policy failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise ContainerPolicyError(code, message)


def _is_digest_image_reference(value: str) -> bool:
    """Validate the reviewed image-reference shape in deterministic linear time."""

    repository_and_tag, digest_separator, digest = value.rpartition("@sha256:")
    if (
        digest_separator != "@sha256:"
        or len(digest) != 64
        or any(character not in _LOWER_HEX_DIGITS for character in digest)
    ):
        return False

    repository, tag_separator, tag = repository_and_tag.partition(":")
    components = repository.split("/")
    return (
        tag_separator == ":"
        and bool(tag)
        and len(components) >= 2
        and all(components)
        and all(
            not any(character.isspace() or character in ":@" for character in component)
            for component in components
        )
        and not any(character.isspace() or character in "/:@" for character in tag)
    )


def validate_runtime_inputs(
    gateway_image: str,
    worker_image: str,
    platform: str,
) -> dict[str, str]:
    """Reject ambiguous image and platform inputs before a lifecycle command executes."""

    if not _is_digest_image_reference(gateway_image) or not _is_digest_image_reference(
        worker_image
    ):
        _fail(
            "CONTAINER_IMAGE_REFERENCE_INVALID",
            "Both runtime images must be exact registry paths with tags and SHA-256 digests.",
        )
    if platform not in _SUPPORTED_PLATFORMS:
        _fail(
            "CONTAINER_PLATFORM_UNSUPPORTED",
            "The requested runtime platform is not a reviewed native Linux architecture.",
        )
    return {
        "gateway_image": gateway_image,
        "worker_image": worker_image,
        "platform": platform,
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            _fail("COMPOSE_DUPLICATE_FIELD", "A Compose object contains a duplicate field.")
        document[key] = value
    return document


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerPolicyError(
            "COMPOSE_DOCUMENT_INVALID",
            "A reviewed Compose document could not be decoded as strict JSON-compatible YAML.",
        ) from exc
    if not isinstance(value, dict):
        _fail("COMPOSE_DOCUMENT_INVALID", "A reviewed Compose document must be an object.")
    return cast("dict[str, object]", value)


def _object(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(code, "A reviewed Compose field must be an object with string keys.")
    return cast("dict[str, object]", value)


def _exact_keys(value: dict[str, object], expected: set[str], *, code: str) -> None:
    if set(value) != expected:
        _fail(code, "A reviewed Compose object has missing or unsupported fields.")


def _validate_healthcheck(service: dict[str, object], *, worker: bool) -> None:
    healthcheck = _object(service["healthcheck"], code="COMPOSE_HEALTHCHECK_INVALID")
    _exact_keys(
        healthcheck,
        {"test", "interval", "timeout", "start_period", "retries"},
        code="COMPOSE_HEALTHCHECK_INVALID",
    )
    command = [
        "CMD",
        "/opt/ancestryllm/bin/python",
        "-m",
        "ancestryllm.container_healthcheck",
    ]
    if worker:
        command.append("--worker")
    expected = {
        "test": command,
        "interval": "10s",
        "timeout": "3s",
        "start_period": "5s" if worker else "10s",
        "retries": 6,
    }
    if healthcheck != expected:
        _fail("COMPOSE_HEALTHCHECK_INVALID", "The service healthcheck is not the reviewed command.")


def _validate_service(name: str, raw_service: object) -> None:
    service = _object(raw_service, code="COMPOSE_SERVICE_INVALID")
    if "ports" in service or "expose" in service:
        _fail("COMPOSE_PORT_FORBIDDEN", "Application ports remain unavailable until Issue #350.")
    if "network_mode" in service:
        _fail("COMPOSE_HOST_NETWORK_FORBIDDEN", "Host or shared network modes are forbidden.")
    if service.get("privileged") is not None:
        _fail("COMPOSE_PRIVILEGED_FORBIDDEN", "Privileged containers are forbidden.")
    if "devices" in service:
        _fail("COMPOSE_DEVICE_FORBIDDEN", "Host device access is forbidden.")
    if any(key in service for key in ("environment", "env_file", "secrets", "configs")):
        _fail(
            "COMPOSE_ENVIRONMENT_FORBIDDEN",
            "Static Compose secret and environment delivery remains unavailable until Issue #351.",
        )
    expected_keys = _BASE_SERVICE_KEYS | ({"profiles"} if name == "worker" else set())
    _exact_keys(service, expected_keys, code="COMPOSE_SERVICE_FIELDS_INVALID")

    volumes = service["volumes"]
    if not isinstance(volumes, list) or len(volumes) != 1 or not isinstance(volumes[0], str):
        _fail("COMPOSE_VOLUME_INVALID", "A service must mount only the reviewed named data volume.")
    mount = volumes[0]
    if "/var/run/docker.sock" in mount or "/run/docker.sock" in mount:
        _fail("COMPOSE_DOCKER_SOCKET_FORBIDDEN", "The Docker socket must never enter a container.")
    if mount != _DATA_MOUNT:
        if mount.startswith((".", "/", "~")) or ":/" in mount:
            _fail("COMPOSE_BIND_MOUNT_FORBIDDEN", "Host bind mounts are forbidden.")
        _fail("COMPOSE_VOLUME_INVALID", "Only the reviewed named data volume is permitted.")

    expected_image = _WORKER_IMAGE if name == "worker" else _GATEWAY_IMAGE
    if service["image"] != expected_image:
        _fail(
            "COMPOSE_IMAGE_INVALID", "The service image must fail closed on an unset digest input."
        )
    if service["platform"] != _PLATFORM:
        _fail("COMPOSE_PLATFORM_INVALID", "The service platform must be supplied explicitly.")
    if service["pull_policy"] != "never":
        _fail("COMPOSE_PULL_POLICY_INVALID", "Compose must not resolve or pull an alternate image.")
    if service["user"] != "65532:65532":
        _fail(
            "COMPOSE_ROOT_USER_FORBIDDEN",
            "The service must run as the reviewed numeric non-root user.",
        )
    if service["read_only"] is not True:
        _fail("COMPOSE_READ_ONLY_REQUIRED", "The service root filesystem must remain read-only.")
    if service["init"] is not True:
        _fail("COMPOSE_INIT_REQUIRED", "The service must use an init process.")
    if service["cap_drop"] != ["ALL"]:
        _fail("COMPOSE_CAPABILITIES_UNSAFE", "The service must drop every Linux capability.")
    if service["security_opt"] != ["no-new-privileges:true"]:
        _fail("COMPOSE_NO_NEW_PRIVILEGES_REQUIRED", "The service must prohibit new privileges.")
    if service["tmpfs"] != _TMPFS:
        _fail(
            "COMPOSE_TMPFS_INVALID", "Only the reviewed bounded private tmpfs mounts are permitted."
        )
    if service["networks"] != ["control"]:
        _fail("COMPOSE_NETWORK_INVALID", "The service must use only the explicit internal network.")
    if service["restart"] != "no":
        _fail("COMPOSE_RESTART_INVALID", "Crash loops must remain visible to the supervisor.")
    if service["stop_signal"] != "SIGTERM" or service["stop_grace_period"] != "20s":
        _fail(
            "COMPOSE_SHUTDOWN_INVALID",
            "The service shutdown contract must be SIGTERM within 20 seconds.",
        )
    if (service["cpus"], service["mem_limit"], service["pids_limit"]) != _BASE_RESOURCES[name]:
        _fail(
            "COMPOSE_RESOURCE_LIMIT_INVALID",
            "The base service limits differ from the reviewed budget.",
        )
    if service["logging"] != {
        "driver": "local",
        "options": _LOGGING_OPTIONS[name],
    }:
        _fail(
            "COMPOSE_LOGGING_INVALID",
            "The service logging allocation differs from the aggregate reviewed budget.",
        )
    labels = _object(service["labels"], code="COMPOSE_LABEL_INVALID")
    if labels != {
        "org.ancestryllm.component": name,
        "org.ancestryllm.managed": "true",
    }:
        _fail("COMPOSE_LABEL_INVALID", "The service ownership labels are not exact.")
    if name == "worker" and service["profiles"] != ["worker"]:
        _fail(
            "COMPOSE_WORKER_PROFILE_REQUIRED",
            "The optional worker must require its explicit profile.",
        )
    _validate_healthcheck(service, worker=name == "worker")


def _validate_base(document: dict[str, object]) -> None:
    if document.get("x-ancestryllm-schema-version") != 1:
        _fail("COMPOSE_SCHEMA_UNSUPPORTED", "The Compose policy schema must be exactly v1.")
    _exact_keys(
        document,
        {"name", "x-ancestryllm-schema-version", "services", "networks", "volumes"},
        code="COMPOSE_TOP_LEVEL_INVALID",
    )
    if document["name"] != "ancestryllm":
        _fail("COMPOSE_PROJECT_INVALID", "The Compose project name is not the reviewed identity.")
    services = _object(document["services"], code="COMPOSE_SERVICES_INVALID")
    if set(services) != {"gateway", "worker"}:
        _fail(
            "COMPOSE_SERVICE_UNSUPPORTED",
            "Only gateway and optional worker services are permitted.",
        )
    for name in ("gateway", "worker"):
        _validate_service(name, services[name])

    networks = _object(document["networks"], code="COMPOSE_NETWORK_INVALID")
    if "default" in networks:
        _fail("COMPOSE_DEFAULT_NETWORK_FORBIDDEN", "An implicit default network is forbidden.")
    if networks != {
        "control": {
            "name": "ancestryllm-control",
            "internal": True,
            "attachable": False,
            "labels": {"org.ancestryllm.managed": "true"},
        }
    }:
        _fail("COMPOSE_NETWORK_INVALID", "The explicit internal network is not exact.")
    volumes = _object(document["volumes"], code="COMPOSE_VOLUME_INVALID")
    if volumes != {
        "ancestryllm-data": {
            "name": "ancestryllm-data",
            "labels": {"org.ancestryllm.managed": "true"},
        }
    }:
        _fail("COMPOSE_VOLUME_INVALID", "The named SQLCipher data volume is not exact.")


def _validate_overlay(document: dict[str, object], overlay_path: Path) -> None:
    _exact_keys(document, {"services"}, code="COMPOSE_OVERLAY_INVALID")
    services = _object(document["services"], code="COMPOSE_OVERLAY_INVALID")
    if set(services) != {"gateway", "worker"}:
        _fail("COMPOSE_OVERLAY_INVALID", "The overlay must constrain both reviewed services.")
    filename = overlay_path.name
    if filename not in _PROFILE_RESOURCES:
        _fail("COMPOSE_OVERLAY_INVALID", "The overlay filename is not reviewed.")
    remote = filename == "compose.remote.yaml"
    profile = "host-remote" if remote else "local-desktop"
    resources = _PROFILE_RESOURCES[filename]
    cpu_total = 0.0
    memory_total = 0
    pids_total = 0
    for name in ("gateway", "worker"):
        service = _object(services[name], code="COMPOSE_OVERLAY_INVALID")
        _exact_keys(service, _OVERLAY_SERVICE_KEYS, code="COMPOSE_OVERLAY_INVALID")
        expected = resources[name]
        if (service["cpus"], service["mem_limit"], service["pids_limit"]) != expected:
            _fail("COMPOSE_OVERLAY_INVALID", "The profile resource budget is not exact.")
        if service["labels"] != {"org.ancestryllm.deployment-profile": profile}:
            _fail("COMPOSE_OVERLAY_INVALID", "The profile label is not exact.")
        cpu_total += cast("float", service["cpus"])
        memory_total += int(cast("str", service["mem_limit"]).removesuffix("g"))
        pids_total += cast("int", service["pids_limit"])
    if (cpu_total, memory_total, pids_total) != _PROFILE_TOTALS[filename]:
        _fail(
            "COMPOSE_OVERLAY_INVALID",
            "The enabled service set exceeds the aggregate profile resource budget.",
        )


def _parse_dockerfile_instructions(text: str) -> tuple[tuple[str, str], ...]:
    instructions: list[tuple[str, str]] = []
    continuation: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            if continuation:
                _fail(
                    "CONTAINER_DOCKERFILE_INVALID",
                    "The Dockerfile contains an interrupted continued instruction.",
                )
            continue

        continues = stripped.endswith("\\")
        fragment = stripped[:-1].rstrip() if continues else stripped
        continuation.append(fragment)
        if continues:
            continue

        logical_line = " ".join(" ".join(continuation).split())
        continuation = []
        parts = logical_line.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isalpha():
            _fail(
                "CONTAINER_DOCKERFILE_INVALID",
                "The Dockerfile contains a malformed executable instruction.",
            )
        instructions.append((parts[0].upper(), parts[1]))

    if continuation:
        _fail(
            "CONTAINER_DOCKERFILE_INVALID",
            "The Dockerfile ends with an incomplete continued instruction.",
        )
    return tuple(instructions)


def _validate_dockerfile(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContainerPolicyError(
            "CONTAINER_DOCKERFILE_INVALID", "The production Dockerfile could not be read."
        ) from exc
    instructions = _parse_dockerfile_instructions(text)
    executable_text = "\n".join(
        f"{instruction} {arguments}" for instruction, arguments in instructions
    ).casefold()
    forbidden = ("apt-get", "curl ", "wget ", "sudo ", "pip install")
    if any(value in executable_text for value in forbidden):
        _fail(
            "CONTAINER_DOCKERFILE_INVALID",
            "The Dockerfile contains an unreviewed installer or downloader.",
        )
    if instructions != _DOCKERFILE_INSTRUCTIONS:
        _fail(
            "CONTAINER_DOCKERFILE_INVALID",
            "The executable Dockerfile instructions differ from the reviewed closed grammar.",
        )


def validate_repository_topology(
    base_path: Path,
    overlay_path: Path,
    dockerfile_path: Path,
    *,
    base_document: dict[str, object] | None = None,
) -> dict[str, object]:
    """Validate one base-plus-profile topology and return deterministic evidence."""

    base = base_document if base_document is not None else _load(base_path)
    _validate_base(base)
    _validate_overlay(_load(overlay_path), overlay_path)
    _validate_dockerfile(dockerfile_path)
    return {
        "schema_version": 1,
        "status": "pass",
        "services": ["gateway", "worker"],
        "worker_default_enabled": False,
        "application_routes_enabled": False,
        "schema_migrations_enabled": False,
        "native_platforms": ["linux/amd64", "linux/arm64"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--gateway-image")
    parser.add_argument("--worker-image")
    parser.add_argument("--platform")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the container policy command and return its exit status."""
    args = _parser().parse_args(argv)
    runtime_inputs = (args.gateway_image, args.worker_image, args.platform)
    if any(value is not None for value in runtime_inputs):
        if not all(isinstance(value, str) for value in runtime_inputs):
            _fail(
                "CONTAINER_RUNTIME_INPUTS_INCOMPLETE",
                "Gateway image, worker image, and platform must be validated together.",
            )
        validate_runtime_inputs(
            cast("str", args.gateway_image),
            cast("str", args.worker_image),
            cast("str", args.platform),
        )
    report = validate_repository_topology(args.base, args.overlay, args.dockerfile)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContainerPolicyError",
    "main",
    "validate_repository_topology",
    "validate_runtime_inputs",
]
