#!/usr/bin/env python3
"""Exercise the reviewed container lifecycle on one native Docker architecture."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

_IMAGE_ID = "sha256:"
_EXPECTED_PACKAGES = frozenset({"ancestryllm", "sqlcipher3", "uvicorn"})
_FORBIDDEN_PACKAGES = frozenset(
    {
        "anthropic",
        "google-genai",
        "mypy",
        "ollama",
        "openai",
        "pre-commit",
        "pyinstaller",
        "pytest",
        "ruff",
        "ty",
    }
)
_MANAGED_LABEL = "org.ancestryllm.ci-owner=container-lifecycle"
_SERVICE_LIMITS = {
    "gateway": ("1.5", 1_500_000_000, "3g", 3_221_225_472, 192, "3"),
    "worker": ("0.5", 500_000_000, "1g", 1_073_741_824, 64, "2"),
}


class ContainerLifecycleError(RuntimeError):
    """A stable, sanitized lifecycle-proof failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise ContainerLifecycleError(code, message)


class Docker:
    """Run only bounded Docker commands against task-owned resources."""

    def __init__(self) -> None:
        executable = shutil.which("docker")
        if executable is None:
            _fail("CONTAINER_DOCKER_UNAVAILABLE", "Docker was not found on the reviewed runner.")
        self.executable = executable

    def run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        """Run a bounded Docker command without invoking a shell."""

        try:
            completed = subprocess.run(  # noqa: S603 - resolved Docker binary, no shell
                [self.executable, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContainerLifecycleError(
                "CONTAINER_DOCKER_TIMEOUT",
                "A bounded Docker lifecycle command exceeded its reviewed timeout.",
            ) from exc
        if check and completed.returncode != 0:
            _fail(
                "CONTAINER_DOCKER_COMMAND_FAILED",
                "A reviewed Docker lifecycle command failed; inspect the CI step logs.",
            )
        return completed

    def json(self, *arguments: str) -> Any:
        """Run a Docker inspection command and decode its JSON response."""

        raw = self.run(*arguments).stdout
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContainerLifecycleError(
                "CONTAINER_DOCKER_RESPONSE_INVALID",
                "Docker returned malformed structured inspection output.",
            ) from exc


def _validate_image_id(value: str) -> str:
    if not value.startswith(_IMAGE_ID) or len(value) != len(_IMAGE_ID) + 64:
        _fail(
            "CONTAINER_IMAGE_ID_INVALID",
            "CI must execute the exact immutable image ID produced by its native build.",
        )
    try:
        bytes.fromhex(value.removeprefix(_IMAGE_ID))
    except ValueError as exc:
        raise ContainerLifecycleError(
            "CONTAINER_IMAGE_ID_INVALID",
            "CI must execute the exact immutable image ID produced by its native build.",
        ) from exc
    return value


def _container_inspect(docker: Docker, name: str) -> dict[str, object]:
    value = docker.json("container", "inspect", name)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        _fail("CONTAINER_INSPECTION_INVALID", "Container inspection was not an exact object.")
    return cast("dict[str, object]", value[0])


def _image_architecture(docker: Docker, image: str) -> str:
    value = docker.json("image", "inspect", image)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        _fail("CONTAINER_IMAGE_INSPECTION_INVALID", "Image inspection was not an exact object.")
    architecture = value[0].get("Architecture")
    if not isinstance(architecture, str):
        _fail("CONTAINER_IMAGE_INSPECTION_INVALID", "The image architecture was unavailable.")
    return architecture


def _assert_hardening(
    inspect: dict[str, object], *, component: str, network: str, volume: str
) -> None:
    config = inspect.get("Config")
    host = inspect.get("HostConfig")
    mounts = inspect.get("Mounts")
    if not isinstance(config, dict) or not isinstance(host, dict) or not isinstance(mounts, list):
        _fail("CONTAINER_HARDENING_INVALID", "Container hardening inspection was incomplete.")
    limits = _SERVICE_LIMITS.get(component)
    if limits is None:
        _fail("CONTAINER_COMPONENT_INVALID", "The lifecycle component was not reviewed.")
    _, nano_cpus, _, memory_bytes, pids_limit, max_files = limits
    expected_host = {
        "ReadonlyRootfs": True,
        "Privileged": False,
        "Init": True,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "NetworkMode": network,
        "NanoCpus": nano_cpus,
        "Memory": memory_bytes,
        "PidsLimit": pids_limit,
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "LogConfig": {
            "Type": "local",
            "Config": {"max-file": max_files, "max-size": "20m"},
        },
    }
    for key, expected in expected_host.items():
        if host.get(key) != expected:
            _fail("CONTAINER_HARDENING_INVALID", "A runtime hardening control was not exact.")
    if config.get("User") != "65532:65532" or config.get("StopSignal") != "SIGTERM":
        _fail("CONTAINER_HARDENING_INVALID", "The runtime user or stop signal was not exact.")
    if config.get("ExposedPorts") not in (None, {}):
        _fail("CONTAINER_PORT_EXPOSURE_FORBIDDEN", "The probe-only image exposed a port.")
    devices = host.get("Devices")
    if devices not in (None, []):
        _fail("CONTAINER_DEVICE_FORBIDDEN", "The container received a host device.")
    binds = host.get("Binds")
    if binds not in (None, []):
        _fail("CONTAINER_BIND_MOUNT_FORBIDDEN", "The container received a host bind mount.")
    if len(mounts) != 1:
        _fail("CONTAINER_MOUNT_INVALID", "The persistent container mount set was not exact.")
    normalized_mounts = {
        (
            mount.get("Type"),
            mount.get("Name"),
            mount.get("Destination"),
            mount.get("RW"),
        )
        for mount in mounts
        if isinstance(mount, dict)
    }
    expected_mounts = {
        ("volume", volume, "/var/lib/ancestryllm", False),
    }
    if normalized_mounts != expected_mounts:
        _fail("CONTAINER_MOUNT_INVALID", "The persistent container mount set was not exact.")
    expected_tmpfs = {
        "/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",  # noqa: S108 - bounded private container tmpfs
        "/run/ancestryllm": "rw,noexec,nosuid,nodev,size=1m,mode=0700,uid=65532,gid=65532",
    }
    if host.get("Tmpfs") != expected_tmpfs:
        _fail("CONTAINER_TMPFS_INVALID", "The private tmpfs mount set was not exact.")


def _wait_for_health(docker: Docker, name: str, *, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inspect = _container_inspect(docker, name)
        state = inspect.get("State")
        if not isinstance(state, dict):
            _fail("CONTAINER_STATE_INVALID", "Container state inspection was incomplete.")
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status") == "healthy":
            return
        if not state.get("Running"):
            _fail("CONTAINER_STARTUP_FAILED", "The container exited before becoming healthy.")
        time.sleep(1)
    _fail("CONTAINER_HEALTH_TIMEOUT", "The container did not become healthy in time.")


def _run_service(
    docker: Docker,
    *,
    component: str,
    name: str,
    image: str,
    platform: str,
    network: str,
    volume: str,
) -> None:
    limits = _SERVICE_LIMITS.get(component)
    if limits is None:
        _fail("CONTAINER_COMPONENT_INVALID", "The lifecycle component was not reviewed.")
    cpu_argument, _, memory_argument, _, pids_limit, max_files = limits
    docker.run(
        "run",
        "--detach",
        "--name",
        name,
        "--platform",
        platform,
        "--network",
        network,
        "--read-only",
        "--init",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",  # noqa: S108 - reviewed private tmpfs
        "--tmpfs",
        "/run/ancestryllm:rw,noexec,nosuid,nodev,size=1m,mode=0700,uid=65532,gid=65532",
        "--mount",
        f"type=volume,source={volume},target=/var/lib/ancestryllm,readonly",
        "--user",
        "65532:65532",
        "--cpus",
        cpu_argument,
        "--memory",
        memory_argument,
        "--pids-limit",
        str(pids_limit),
        "--restart",
        "no",
        "--stop-signal",
        "SIGTERM",
        "--stop-timeout",
        "20",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=20m",
        "--log-opt",
        f"max-file={max_files}",
        "--label",
        _MANAGED_LABEL,
        image,
    )


def _read_inventory(docker: Docker, gateway: str) -> dict[str, object]:
    completed = docker.run(
        "exec",
        gateway,
        "/opt/ancestryllm/bin/python",
        "-c",
        "from pathlib import Path; print(Path('/opt/ancestryllm/package-inventory.json').read_text())",
    )
    try:
        inventory = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContainerLifecycleError(
            "CONTAINER_INVENTORY_INVALID",
            "The embedded package and license inventory was malformed.",
        ) from exc
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        _fail("CONTAINER_INVENTORY_INVALID", "The embedded inventory schema was unsupported.")
    packages = inventory.get("python_packages")
    if not isinstance(packages, list):
        _fail(
            "CONTAINER_INVENTORY_INVALID",
            "The embedded inventory omitted its Python package list.",
        )
    names = {
        item.get("name")
        for item in packages
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if not names >= _EXPECTED_PACKAGES or _FORBIDDEN_PACKAGES & names:
        _fail("CONTAINER_INVENTORY_SCOPE_INVALID", "The runtime dependency set was not minimal.")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("version"), str)
        or not isinstance(item.get("license"), str)
        or not isinstance(item.get("license_classifiers"), list)
        or not all(
            isinstance(value, str) and value for value in item.get("license_classifiers", [])
        )
        or (item.get("license") == "UNKNOWN" and not item.get("license_classifiers"))
        for item in packages
    ):
        _fail("CONTAINER_INVENTORY_INVALID", "A package omitted version or license evidence.")
    os_packages = inventory.get("operating_system_packages")
    if not isinstance(os_packages, list) or not os_packages:
        _fail(
            "CONTAINER_INVENTORY_INVALID",
            "The embedded inventory omitted its operating-system package list.",
        )
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("name"), str)
        or not isinstance(item.get("version"), str)
        or not isinstance(item.get("architecture"), str)
        or not isinstance(item.get("licenses"), list)
        or not item["licenses"]
        or not isinstance(item.get("license_file_sha256"), str)
        or len(item["license_file_sha256"]) != 64
        for item in os_packages
    ):
        _fail(
            "CONTAINER_INVENTORY_INVALID",
            "An operating-system package omitted version or license evidence.",
        )
    return inventory


def _prove_gateway_fail_closed(docker: Docker, gateway: str) -> None:
    for mismatch in ("version", "build"):
        docker.run(
            "exec",
            gateway,
            "/opt/ancestryllm/bin/python",
            "-m",
            "ancestryllm.container_healthcheck",
            "--expect-rejection",
            mismatch,
        )
    root_write = docker.run(
        "exec",
        gateway,
        "/opt/ancestryllm/bin/python",
        "-c",
        "from pathlib import Path; Path('/readonly-proof').write_text('forbidden')",
        check=False,
    )
    data_write = docker.run(
        "exec",
        gateway,
        "/opt/ancestryllm/bin/python",
        "-c",
        "from pathlib import Path; Path('/var/lib/ancestryllm/forbidden').write_text('forbidden')",
        check=False,
    )
    if root_write.returncode == 0 or data_write.returncode == 0:
        _fail("CONTAINER_READ_ONLY_FAIL_OPEN", "A reviewed read-only filesystem accepted a write.")
    docker.run(
        "exec",
        gateway,
        "/opt/ancestryllm/bin/python",
        "-c",
        (
            "import errno; from pathlib import Path; p=Path('/tmp/full-proof'); "
            "f=p.open('wb'); ok=False; "
            "\ntry:\n while True: f.write(b'0' * 1048576); f.flush()"
            "\nexcept OSError as e: ok=e.errno in {errno.ENOSPC, errno.EDQUOT}"
            "\nfinally: f.close(); p.unlink(missing_ok=True)"
            "\nraise SystemExit(0 if ok else 1)"
        ),
    )


def _prove_logs_exclude_probe_token(docker: Docker, gateway: str) -> None:
    token = docker.run(
        "exec",
        gateway,
        "/opt/ancestryllm/bin/python",
        "-c",
        "from pathlib import Path; print(Path('/run/ancestryllm/probe-token').read_text())",
    ).stdout.strip()
    completed = docker.run("logs", gateway)
    logs = f"{completed.stdout}\n{completed.stderr}"
    if (
        not token
        or token in logs
        or "authorization" in logs.casefold()
        or "bearer" in logs.casefold()
    ):
        _fail("CONTAINER_LOG_SECRET_EXPOSURE", "Runtime logs exposed authentication material.")


def _stop_and_require_clean_exit(docker: Docker, name: str) -> None:
    docker.run("stop", "--timeout", "20", name, timeout=30)
    exit_code = docker.run("wait", name).stdout.strip()
    if exit_code != "0":
        _fail("CONTAINER_SHUTDOWN_FAILED", "The container did not stop cleanly within its budget.")


def _prove_crash_visibility(docker: Docker, gateway: str) -> None:
    docker.run("start", gateway)
    _wait_for_health(docker, gateway)
    docker.run("kill", "--signal", "KILL", gateway)
    exit_code = docker.run("wait", gateway).stdout.strip()
    inspect = _container_inspect(docker, gateway)
    state = inspect.get("State")
    if (
        exit_code != "137"
        or not isinstance(state, dict)
        or state.get("Running") is not False
        or inspect.get("RestartCount") != 0
    ):
        _fail("CONTAINER_CRASH_FAIL_OPEN", "A crashed container was hidden or restarted.")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_lifecycle(
    *,
    gateway_image: str,
    worker_image: str,
    platform: str,
    architecture: str,
    output: Path,
    inventory_output: Path,
) -> None:
    """Run the complete native lifecycle and retain only sanitized deterministic evidence."""

    gateway_image = _validate_image_id(gateway_image)
    worker_image = _validate_image_id(worker_image)
    if platform != f"linux/{architecture}" or architecture not in {"amd64", "arm64"}:
        _fail("CONTAINER_PLATFORM_INVALID", "The CI platform and architecture were not exact.")

    docker = Docker()
    suffix = secrets.token_hex(8)
    gateway = f"ancestryllm-ci-gateway-{suffix}"
    worker = f"ancestryllm-ci-worker-{suffix}"
    network = f"ancestryllm-ci-network-{suffix}"
    volume = f"ancestryllm-ci-data-{suffix}"
    assertions: list[str] = []
    try:
        if _image_architecture(docker, gateway_image) != architecture:
            _fail("CONTAINER_ARCHITECTURE_MISMATCH", "The gateway image was not built natively.")
        if _image_architecture(docker, worker_image) != architecture:
            _fail("CONTAINER_ARCHITECTURE_MISMATCH", "The worker image was not built natively.")
        assertions.append("native-image-architecture")

        docker.run("network", "create", "--internal", "--label", _MANAGED_LABEL, network)
        docker.run("volume", "create", "--label", _MANAGED_LABEL, volume)
        _run_service(
            docker,
            component="gateway",
            name=gateway,
            image=gateway_image,
            platform=platform,
            network=network,
            volume=volume,
        )
        _wait_for_health(docker, gateway)
        _assert_hardening(
            _container_inspect(docker, gateway),
            component="gateway",
            network=network,
            volume=volume,
        )
        assertions.extend(("gateway-health", "runtime-hardening"))

        inventory = _read_inventory(docker, gateway)
        _write_json(inventory_output, inventory)
        assertions.append("package-license-inventory")
        _prove_gateway_fail_closed(docker, gateway)
        assertions.extend(
            (
                "peer-build-skew-rejection",
                "peer-version-skew-rejection",
                "read-only-and-disk-full",
            )
        )
        _prove_logs_exclude_probe_token(docker, gateway)
        assertions.append("log-redaction")
        _stop_and_require_clean_exit(docker, gateway)
        assertions.append("graceful-gateway-shutdown")
        _prove_crash_visibility(docker, gateway)
        assertions.append("crash-visible-without-restart")

        _run_service(
            docker,
            component="worker",
            name=worker,
            image=worker_image,
            platform=platform,
            network=network,
            volume=volume,
        )
        _wait_for_health(docker, worker)
        _assert_hardening(
            _container_inspect(docker, worker),
            component="worker",
            network=network,
            volume=volume,
        )
        _stop_and_require_clean_exit(docker, worker)
        assertions.extend(("optional-worker-health", "graceful-worker-shutdown"))

        inventory_bytes = inventory_output.read_bytes()
        report: dict[str, object] = {
            "schema_version": 1,
            "status": "pass",
            "architecture": architecture,
            "platform": platform,
            "gateway_image_id": gateway_image,
            "worker_image_id": worker_image,
            "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
            "application_routes_enabled": False,
            "schema_migrations_enabled": False,
            "assertions": sorted(assertions),
        }
        _write_json(output, report)
    finally:
        docker.run("container", "rm", "--force", gateway, check=False, timeout=30)
        docker.run("container", "rm", "--force", worker, check=False, timeout=30)
        docker.run("network", "rm", network, check=False, timeout=30)
        docker.run("volume", "rm", volume, check=False, timeout=30)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the container ci smoke command and return its exit status."""
    args = _parser().parse_args(argv)
    try:
        run_lifecycle(
            gateway_image=args.gateway_image,
            worker_image=args.worker_image,
            platform=args.platform,
            architecture=args.architecture,
            output=args.output,
            inventory_output=args.inventory_output,
        )
    except ContainerLifecycleError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ContainerLifecycleError", "main", "run_lifecycle"]
