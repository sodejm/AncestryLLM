from unittest.mock import Mock, patch

import tools.bootstrap as bootstrap


def test_main_on_darwin_checks_brew_and_runs_docker():
    commands = []

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        if command[:3] == ["brew", "list", "--formula"]:
            return Mock(returncode=1)
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Darwin"), patch(
        "tools.bootstrap.shutil.which", return_value="brew"
    ), patch("tools.bootstrap.subprocess.run", side_effect=fake_run):
        result = bootstrap.main()

    assert result == 0
    assert commands == [
        ["brew", "list", "--formula", "ollama"],
        ["brew", "install", "ollama"],
        ["brew", "services", "start", "ollama"],
        ["docker", "compose", "up", "-d"],
    ]


def test_main_on_linux_skips_brew_and_runs_docker_only():
    commands = []

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(list(command))
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Linux"), patch(
        "tools.bootstrap.shutil.which"
    ) as mock_which, patch("tools.bootstrap.subprocess.run", side_effect=fake_run):
        result = bootstrap.main()

    assert result == 0
    mock_which.assert_not_called()
    assert commands == [["docker", "compose", "up", "-d"]]


def test_main_returns_error_when_docker_is_missing():
    def fake_run(command, check=True, stdout=None, stderr=None):
        if command == ["docker", "compose", "up", "-d"]:
            raise FileNotFoundError("docker")
        return Mock(returncode=0)

    with patch("tools.bootstrap.platform.system", return_value="Linux"), patch(
        "tools.bootstrap.subprocess.run", side_effect=fake_run
    ):
        result = bootstrap.main()

    assert result == 1


def test_main_returns_error_when_brew_is_missing():
    with patch("tools.bootstrap.platform.system", return_value="Darwin"), patch(
        "tools.bootstrap.shutil.which", return_value=None
    ):
        result = bootstrap.main()

    assert result == 1
