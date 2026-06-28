from unittest.mock import Mock, call, patch

import pytest

import tools.bootstrap as bootstrap


def test_main_on_darwin_checks_brew_and_runs_docker():
    commands = []

    def fake_which(command):
        if command == "brew":
            return "brew"
        if command == "docker":
            return "docker"
        return None

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        if command[:3] == ["brew", "list", "--formula"]:
            return Mock(returncode=1)
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Darwin"), patch(
        "tools.bootstrap.shutil.which", side_effect=fake_which
    ), patch("tools.bootstrap.subprocess.run", side_effect=fake_run), patch(
        "tools.bootstrap.ensure_ollama_models"
    ), patch(
        "tools.bootstrap.validate_runtime"
    ):
        result = bootstrap.main()

    assert result == 0
    assert commands == [
        ["brew", "list", "--formula", "ollama"],
        ["brew", "install", "ollama"],
        ["brew", "services", "start", "ollama"],
        ["docker", "compose", "version"],
        ["docker", "info"],
        ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"],
    ]


def test_main_on_linux_skips_brew_and_runs_docker_only():
    commands = []

    def fake_which(command):
        if command == "docker":
            return "docker"
        return None

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Linux"), patch(
        "tools.bootstrap.shutil.which", side_effect=fake_which
    ), patch("tools.bootstrap.subprocess.run", side_effect=fake_run), patch(
        "tools.bootstrap.validate_runtime"
    ):
        result = bootstrap.main()

    assert result == 0
    assert commands == [
        ["docker", "compose", "version"],
        ["docker", "info"],
        ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"],
    ]


def test_main_uses_legacy_docker_compose_when_plugin_is_unavailable():
    commands = []

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        if command == ["docker", "compose", "version"]:
            return Mock(returncode=1)
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Linux"), patch(
        "tools.bootstrap.shutil.which", side_effect=["docker", "docker-compose", "docker"]
    ), patch("tools.bootstrap.subprocess.run", side_effect=fake_run), patch(
        "tools.bootstrap.validate_runtime"
    ):
        result = bootstrap.main()

    assert result == 0
    assert commands == [
        ["docker", "compose", "version"],
        ["docker", "info"],
        ["docker-compose", "-f", "docker-compose.yml", "up", "-d"],
    ]


def test_main_installs_legacy_docker_compose_on_darwin_if_missing():
    commands = []
    which_values = iter(["brew", "docker", None, "brew", "docker-compose", "docker"])

    def fake_which(_):
        return next(which_values)

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        if command[:3] == ["brew", "list", "--formula"]:
            return Mock(returncode=0)
        if command == ["docker", "compose", "version"]:
            return Mock(returncode=1)
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Darwin"), patch(
        "tools.bootstrap.shutil.which", side_effect=fake_which
    ), patch("tools.bootstrap.subprocess.run", side_effect=fake_run), patch(
        "tools.bootstrap.ensure_ollama_models"
    ), patch(
        "tools.bootstrap.validate_runtime"
    ):
        result = bootstrap.main()

    assert result == 0
    assert ["brew", "install", "docker-compose"] in commands
    assert ["docker-compose", "-f", "docker-compose.yml", "up", "-d"] in commands


def test_main_in_hosted_mode_uses_hosted_compose_overlay():
    commands = []

    def fake_which(command):
        if command == "docker":
            return "docker"
        return None

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        return Mock(returncode=0)

    with patch("tools.bootstrap.DEPLOYMENT_MODE", "hosted"), patch(
        "tools.bootstrap.platform.system", return_value="Linux"
    ), patch("tools.bootstrap.shutil.which", side_effect=fake_which), patch(
        "tools.bootstrap.subprocess.run", side_effect=fake_run
    ), patch("tools.bootstrap.validate_runtime"):
        result = bootstrap.main()

    assert result == 0
    assert commands == [
        ["docker", "compose", "version"],
        ["docker", "info"],
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.hosted.yml",
            "up",
            "-d",
        ],
    ]


def test_main_returns_error_when_docker_compose_is_missing():
    with patch("tools.bootstrap.platform.system", return_value="Linux"), patch(
        "tools.bootstrap.shutil.which", side_effect=["docker", None]
    ):
        result = bootstrap.main()

    assert result == 1


def test_main_returns_error_when_brew_is_missing():
    with patch("tools.bootstrap.platform.system", return_value="Darwin"), patch(
        "tools.bootstrap.shutil.which", return_value=None
    ):
        result = bootstrap.main()

    assert result == 1


def test_ensure_docker_daemon_on_darwin_starts_docker_when_not_ready():
    commands = []
    run_results = iter([Mock(returncode=1), Mock(returncode=0)])

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        if command == ["open", "-a", "Docker"]:
            return Mock(returncode=0)
        return next(run_results)

    with patch("tools.bootstrap.shutil.which", return_value="docker"), patch(
        "tools.bootstrap.subprocess.run", side_effect=fake_run
    ), patch("tools.bootstrap.time.sleep"):
        bootstrap.ensure_docker_daemon("Darwin")

    assert commands == [
        ["docker", "info"],
        ["open", "-a", "Docker"],
        ["docker", "info"],
    ]


def test_validate_runtime_restarts_ollama_on_darwin_when_unhealthy():
    with patch(
        "tools.bootstrap.wait_for_ollama_ready",
        side_effect=[RuntimeError("down"), None],
    ) as wait_for_ollama_ready, patch(
        "tools.bootstrap.wait_for_open_webui_ready"
    ) as wait_for_open_webui_ready, patch(
        "tools.bootstrap.restart_ollama_on_macos"
    ) as restart:
        bootstrap.validate_runtime("Darwin")

    restart.assert_called_once()
    assert wait_for_ollama_ready.call_count == 2
    wait_for_open_webui_ready.assert_called_once()


def test_validate_runtime_raises_on_non_darwin_ollama_failure():
    with patch("tools.bootstrap.wait_for_ollama_ready", side_effect=RuntimeError("down")):
        with pytest.raises(RuntimeError):
            bootstrap.validate_runtime("Linux")


def test_ensure_ollama_models_pulls_all_configured_models():
    with patch("tools.bootstrap.OLLAMA_BOOTSTRAP_MODELS", "gemma4,llama3.1"), patch(
        "tools.bootstrap.shutil.which", return_value="ollama"
    ), patch("tools.bootstrap.run_command") as run_command:
        bootstrap.ensure_ollama_models()

    assert run_command.call_args_list == [
        call(
            ["ollama", "pull", "gemma4"],
            "Ensuring Ollama model is available (gemma4)",
        ),
        call(
            ["ollama", "pull", "llama3.1"],
            "Ensuring Ollama model is available (llama3.1)",
        ),
    ]


def test_ensure_ollama_models_errors_when_every_pull_fails():
    with patch("tools.bootstrap.OLLAMA_BOOTSTRAP_MODELS", "gemma4,llama3.1"), patch(
        "tools.bootstrap.shutil.which", return_value="ollama"
    ), patch("tools.bootstrap.run_command", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            bootstrap.ensure_ollama_models()

def test_wait_for_ollama_ready_returns_when_client_lists_models():
    fake_client = Mock()
    fake_client.list.return_value = {"models": []}
    with patch("ollama.Client", return_value=fake_client) as client_ctor, patch(
        "tools.bootstrap.time.sleep"
    ):
        bootstrap.wait_for_ollama_ready(10)

    client_ctor.assert_called_once_with(host=bootstrap.OLLAMA_HEALTHCHECK_URL)
    fake_client.list.assert_called_once()


def test_wait_for_ollama_ready_raises_after_timeout():
    fake_client = Mock()
    fake_client.list.side_effect = ConnectionError("ollama is down")
    times = iter([1000.0, 1001.0, 2000.0])
    with patch("ollama.Client", return_value=fake_client), patch(
        "tools.bootstrap.time.sleep"
    ), patch("tools.bootstrap.time.time", side_effect=lambda: next(times)):
        with pytest.raises(RuntimeError):
            bootstrap.wait_for_ollama_ready(10)


def test_wait_for_open_webui_ready_returns_on_success():
    response = Mock()
    response.is_success = True
    with patch("httpx.get", return_value=response) as httpx_get, patch(
        "tools.bootstrap.time.sleep"
    ):
        bootstrap.wait_for_open_webui_ready(10)

    httpx_get.assert_called_once_with(bootstrap.OPEN_WEBUI_HEALTHCHECK_URL, timeout=5)


def test_wait_for_open_webui_ready_raises_after_timeout():
    import httpx

    times = iter([1000.0, 1001.0, 2000.0])
    with patch("httpx.get", side_effect=httpx.ConnectError("webui is down")), patch(
        "tools.bootstrap.time.sleep"
    ), patch("tools.bootstrap.time.time", side_effect=lambda: next(times)):
        with pytest.raises(RuntimeError):
            bootstrap.wait_for_open_webui_ready(10)
