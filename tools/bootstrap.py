import os
import platform
import shutil
import subprocess
import sys
import time

# Bound the Ollama context window so local model calls stay within a 24GB VRAM
# budget instead of overflowing into host RAM.
OLLAMA_NUM_CTX = os.getenv("OLLAMA_NUM_CTX", "8192")
OLLAMA_BOOTSTRAP_MODELS = os.getenv(
    "OLLAMA_BOOTSTRAP_MODELS",
    "gemma4,llama3.1,qwen3,mistral",
)
DOCKER_READY_TIMEOUT_SECONDS = int(os.getenv("DOCKER_READY_TIMEOUT_SECONDS", "120"))
SERVICE_READY_TIMEOUT_SECONDS = int(os.getenv("SERVICE_READY_TIMEOUT_SECONDS", "180"))
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "localhost").strip().lower()
# Readiness probes target the local service endpoints via maintained SDKs
# (the `ollama` client and `httpx`) instead of shelling out to `curl`.
OLLAMA_HEALTHCHECK_URL = os.getenv("OLLAMA_HEALTHCHECK_URL", "http://127.0.0.1:11434")
OPEN_WEBUI_HEALTHCHECK_URL = os.getenv(
    "OPEN_WEBUI_HEALTHCHECK_URL", "http://127.0.0.1:3000/"
)
HEALTHCHECK_POLL_SECONDS = int(os.getenv("HEALTHCHECK_POLL_SECONDS", "2"))


def configure_ollama_runtime() -> None:
    """Set the ``OLLAMA_NUM_CTX`` environment variable if not already present.

    Bounding the context window keeps local model calls within the available
    VRAM budget and prevents silent overflow into host RAM.
    """
    os.environ.setdefault("OLLAMA_NUM_CTX", OLLAMA_NUM_CTX)
    print(
        "[bootstrap] Ollama context window bounded to "
        f"num_ctx={os.environ['OLLAMA_NUM_CTX']} for VRAM safety"
    )


def run_command(command: list[str], description: str) -> None:
    """Execute *command* as a subprocess and raise :exc:`RuntimeError` on failure.

    Prints *description* alongside the command before running it so the caller
    can follow bootstrap progress in the terminal.
    """
    print(f"[bootstrap] {description}: {' '.join(command)}")
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        missing_command = command[0]
        raise RuntimeError(f"Required command not found: {missing_command}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed with exit code {exc.returncode}") from exc


def command_succeeds(command: list[str]) -> bool:
    """Return True when a command exits successfully, otherwise False."""
    try:
        return (
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def ensure_ollama_on_macos() -> None:
    """Install and start Ollama via Homebrew on macOS.

    Raises :exc:`RuntimeError` when Homebrew is not found, because it is the
    only supported installation path on macOS.
    """
    brew_path = shutil.which("brew")
    if not brew_path:
        raise RuntimeError(
            "Homebrew is required on macOS to install Ollama. Install Homebrew and run again."
        )

    ollama_installed = subprocess.run(
        [brew_path, "list", "--formula", "ollama"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0

    if not ollama_installed:
        run_command([brew_path, "install", "ollama"], "Installing Ollama with Homebrew")
    else:
        print("[bootstrap] Ollama is already installed via Homebrew")

    run_command([brew_path, "services", "start", "ollama"], "Starting Ollama background service")


def restart_ollama_on_macos() -> None:
    """Restart the Ollama Homebrew service on macOS."""
    brew_path = shutil.which("brew")
    if not brew_path:
        raise RuntimeError("Homebrew is required on macOS to restart Ollama.")
    run_command(
        [brew_path, "services", "restart", "ollama"],
        "Restarting Ollama background service",
    )


def ensure_ollama_models() -> None:
    """Pre-pull configured Ollama models so local chat is ready after bootstrap."""
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        raise RuntimeError("Required command not found: ollama")

    models = [model.strip() for model in OLLAMA_BOOTSTRAP_MODELS.split(",") if model.strip()]
    if not models:
        return

    successful_pulls = 0
    for model in models:
        try:
            run_command(
                [ollama_path, "pull", model],
                f"Ensuring Ollama model is available ({model})",
            )
            successful_pulls += 1
        except RuntimeError as exc:
            print(f"[bootstrap] Model pull failed for '{model}': {exc}", file=sys.stderr)

    if successful_pulls == 0:
        raise RuntimeError(
            "Failed to pull all configured Ollama models. Check OLLAMA_BOOTSTRAP_MODELS."
        )


def resolve_compose_command(system_name: str) -> list[str]:
    """Resolve an available Docker Compose command, installing fallback on macOS."""
    docker_path = shutil.which("docker")
    if docker_path and command_succeeds([docker_path, "compose", "version"]):
        return [docker_path, "compose"]

    docker_compose_path = shutil.which("docker-compose")
    if docker_compose_path:
        return [docker_compose_path]

    if system_name == "Darwin":
        brew_path = shutil.which("brew")
        if brew_path:
            run_command(
                [brew_path, "install", "docker-compose"],
                "Installing legacy Docker Compose with Homebrew",
            )
            docker_compose_path = shutil.which("docker-compose")
            if docker_compose_path:
                return [docker_compose_path]

    raise RuntimeError(
        "Docker Compose is required. Install Docker Compose v2 (`docker compose`) "
        "or the legacy `docker-compose` binary and run again."
    )


def ensure_docker_daemon(system_name: str) -> None:
    """Ensure the Docker daemon is reachable before starting containers."""
    docker_path = shutil.which("docker")
    if not docker_path:
        raise RuntimeError("Required command not found: docker")

    if command_succeeds([docker_path, "info"]):
        return

    if system_name == "Darwin":
        if command_succeeds(["open", "-a", "Docker"]):
            print("[bootstrap] Starting Docker Desktop")
        else:
            colima_path = shutil.which("colima")
            if colima_path:
                run_command([colima_path, "start"], "Starting Colima runtime")
        deadline = time.time() + DOCKER_READY_TIMEOUT_SECONDS
        while time.time() < deadline:
            if command_succeeds([docker_path, "info"]):
                print("[bootstrap] Docker daemon is ready")
                return
            time.sleep(2)

    raise RuntimeError(
        "Docker daemon is not ready. Start Docker and re-run quickstart."
    )


def wait_for_ollama_ready(timeout_seconds: int) -> None:
    """Poll the local Ollama API via the official SDK until ready or timeout."""
    import httpx
    import ollama

    client = ollama.Client(host=OLLAMA_HEALTHCHECK_URL)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            client.list()
            return
        except (ollama.ResponseError, httpx.HTTPError, OSError):
            time.sleep(HEALTHCHECK_POLL_SECONDS)

    raise RuntimeError(f"Ollama did not become ready at {OLLAMA_HEALTHCHECK_URL}")


def wait_for_open_webui_ready(timeout_seconds: int) -> None:
    """Poll Open WebUI via httpx until the endpoint responds or timeout is reached."""
    import httpx

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(OPEN_WEBUI_HEALTHCHECK_URL, timeout=5)
            if response.is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(HEALTHCHECK_POLL_SECONDS)

    raise RuntimeError(
        f"Open WebUI did not become ready at {OPEN_WEBUI_HEALTHCHECK_URL}"
    )


def resolve_compose_files() -> list[str]:
    """Choose compose file set based on deployment mode (localhost or hosted)."""
    base_file = "docker-compose.yml"
    if DEPLOYMENT_MODE == "localhost":
        return ["-f", base_file]
    if DEPLOYMENT_MODE == "hosted":
        return ["-f", base_file, "-f", "docker-compose.hosted.yml"]
    raise RuntimeError("DEPLOYMENT_MODE must be either 'localhost' or 'hosted'.")


def validate_runtime(system_name: str) -> None:
    """Verify Ollama and Open WebUI are healthy after compose startup."""
    try:
        wait_for_ollama_ready(SERVICE_READY_TIMEOUT_SECONDS)
    except RuntimeError:
        if system_name != "Darwin":
            raise
        restart_ollama_on_macos()
        wait_for_ollama_ready(SERVICE_READY_TIMEOUT_SECONDS)

    wait_for_open_webui_ready(SERVICE_READY_TIMEOUT_SECONDS)
    print("[bootstrap] Runtime health checks passed")


def main() -> int:
    """Run the bootstrap sequence and return an exit code.

    Returns ``0`` on success or ``1`` when any step raises :exc:`RuntimeError`.
    """
    try:
        system_name = platform.system()
        print(f"[bootstrap] Detected platform: {system_name}")

        if system_name == "Darwin":
            ensure_ollama_on_macos()
            ensure_ollama_models()
        elif system_name in {"Linux", "Windows"}:
            print("[bootstrap] Ollama setup will be handled by Docker Compose on this platform")
        else:
            print("[bootstrap] Unknown platform; proceeding with Docker Compose startup")

        configure_ollama_runtime()
        compose_command = resolve_compose_command(system_name)
        compose_files = resolve_compose_files()
        print(f"[bootstrap] Deployment mode: {DEPLOYMENT_MODE}")
        ensure_docker_daemon(system_name)
        run_command(
            [*compose_command, *compose_files, "up", "-d"],
            "Starting services with Docker Compose",
        )
        validate_runtime(system_name)
        print("[bootstrap] Bootstrap complete")
        return 0
    except RuntimeError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
