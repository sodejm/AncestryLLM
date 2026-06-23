import os
import platform
import shutil
import subprocess
import sys

# Bound the Ollama context window so local model calls stay within a 24GB VRAM
# budget instead of overflowing into host RAM.
OLLAMA_NUM_CTX = os.getenv("OLLAMA_NUM_CTX", "8192")


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


def main() -> int:
    """Run the bootstrap sequence and return an exit code.

    Returns ``0`` on success or ``1`` when any step raises :exc:`RuntimeError`.
    """
    try:
        system_name = platform.system()
        print(f"[bootstrap] Detected platform: {system_name}")

        if system_name == "Darwin":
            ensure_ollama_on_macos()
        elif system_name in {"Linux", "Windows"}:
            print("[bootstrap] Ollama setup will be handled by Docker Compose on this platform")
        else:
            print("[bootstrap] Unknown platform; proceeding with Docker Compose startup")

        configure_ollama_runtime()
        run_command(["docker", "compose", "up", "-d"], "Starting services with Docker Compose")
        print("[bootstrap] Bootstrap complete")
        return 0
    except RuntimeError as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
