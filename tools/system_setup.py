import argparse
import platform
import shutil
import subprocess
import time


def run_command(command: list[str], description: str) -> None:
    """Run an installation/setup command with normalized error handling."""
    print(f"[auto-install] {description}: {' '.join(command)}")
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed with exit code {exc.returncode}") from exc


def find_brew() -> str | None:
    """Locate Homebrew in common install locations."""
    brew_path = shutil.which("brew")
    if brew_path:
        return brew_path
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if shutil.which(candidate):
            return candidate
    return None


def ensure_homebrew() -> str:
    """Ensure Homebrew exists and return the executable path."""
    brew_path = find_brew()
    if brew_path:
        return brew_path

    run_command(
        [
            "/bin/bash",
            "-c",
            "NONINTERACTIVE=1 /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"",
        ],
        "Installing Homebrew",
    )
    brew_path = find_brew()
    if not brew_path:
        raise RuntimeError("Homebrew installation finished but `brew` was not found on PATH.")
    return brew_path


def ensure_brew_package(brew_path: str, package: str, *, cask: bool = False) -> None:
    """Install a Homebrew formula/cask when it is not already installed."""
    package_kind = "--cask" if cask else "--formula"
    installed = (
        subprocess.run(
            [brew_path, "list", package_kind, package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    if installed:
        print(f"[auto-install] {package} already installed via Homebrew")
        return

    command = [brew_path, "install"]
    if cask:
        command.append("--cask")
    command.append(package)
    run_command(command, f"Installing {package} with Homebrew")


def docker_ready() -> bool:
    """Return True when Docker CLI and daemon are both available."""
    docker_path = shutil.which("docker")
    if not docker_path:
        return False
    return (
        subprocess.run(
            [docker_path, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def wait_for_docker(timeout_seconds: int = 180) -> None:
    """Wait for Docker daemon readiness within a bounded timeout."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        if docker_ready():
            print("[auto-install] Docker daemon is ready")
            return
        time.sleep(3)
    raise RuntimeError(
        "Docker is installed but not ready. Start Docker Desktop and re-run quickstart."
    )


def ensure_macos_docker_runtime() -> None:
    """Start Docker Desktop or Colima and wait until Docker is ready on macOS."""
    if docker_ready():
        print("[auto-install] Docker daemon is already ready")
        return

    started = False
    docker_app_exists = (
        subprocess.run(
            ["open", "-Ra", "Docker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    if docker_app_exists:
        run_command(["open", "-a", "Docker"], "Starting Docker Desktop")
        started = True

    colima_path = shutil.which("colima")
    if not started and colima_path:
        run_command([colima_path, "start"], "Starting Colima runtime")
        started = True

    if not started:
        raise RuntimeError(
            "No Docker runtime found. Install Docker Desktop or Colima and re-run quickstart."
        )

    wait_for_docker()


def auto_install_macos() -> None:
    """Install required macOS system dependencies for local development/runtime."""
    brew_path = ensure_homebrew()
    ensure_brew_package(brew_path, "git")
    ensure_brew_package(brew_path, "python")
    ensure_brew_package(brew_path, "docker")
    ensure_brew_package(brew_path, "docker-compose")
    ensure_brew_package(brew_path, "colima")
    ensure_macos_docker_runtime()


def auto_install_linux() -> None:
    """Install required Linux system dependencies on apt-based distributions."""
    if not shutil.which("apt-get"):
        raise RuntimeError(
            "Auto-install on Linux currently supports apt-based distributions only."
        )
    run_command(["sudo", "apt-get", "update"], "Updating apt package index")
    run_command(
        [
            "sudo",
            "apt-get",
            "install",
            "-y",
            "git",
            "python3",
            "python3-venv",
            "docker.io",
            "docker-compose-plugin",
        ],
        "Installing required system dependencies",
    )
    run_command(["sudo", "systemctl", "enable", "--now", "docker"], "Starting Docker service")
    wait_for_docker()


def auto_install_dependencies() -> None:
    """Install platform-specific system dependencies for quickstart."""
    system_name = platform.system()
    print(f"[auto-install] Detected platform: {system_name}")
    if system_name == "Darwin":
        auto_install_macos()
        return
    if system_name == "Linux":
        auto_install_linux()
        return
    if system_name == "Windows":
        raise RuntimeError(
            "Auto-install is not supported on Windows yet. Install Docker Desktop, Python 3, and Git manually."
        )
    raise RuntimeError(
        f"Auto-install is not supported on platform: {system_name}."
    )


def main() -> int:
    """Entrypoint for optional auto-install mode used by quickstart."""
    parser = argparse.ArgumentParser(description="Install missing system dependencies for quickstart.")
    parser.add_argument(
        "--auto-install",
        action="store_true",
        help="Install required system dependencies and start Docker if needed.",
    )
    args = parser.parse_args()

    if not args.auto_install:
        return 0

    try:
        auto_install_dependencies()
        return 0
    except RuntimeError as exc:
        print(f"[auto-install] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
