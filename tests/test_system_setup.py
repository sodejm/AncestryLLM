import pytest
from unittest.mock import patch

import tools.system_setup as system_setup


def test_auto_install_dependencies_on_macos_uses_brew_and_starts_docker():
    with patch("tools.system_setup.platform.system", return_value="Darwin"), patch(
        "tools.system_setup.auto_install_macos"
    ) as auto_install_macos, patch("tools.system_setup.auto_install_linux") as auto_install_linux:
        system_setup.auto_install_dependencies()

    auto_install_macos.assert_called_once()
    auto_install_linux.assert_not_called()


def test_auto_install_dependencies_on_linux_uses_apt_flow():
    with patch("tools.system_setup.platform.system", return_value="Linux"), patch(
        "tools.system_setup.auto_install_macos"
    ) as auto_install_macos, patch("tools.system_setup.auto_install_linux") as auto_install_linux:
        system_setup.auto_install_dependencies()

    auto_install_linux.assert_called_once()
    auto_install_macos.assert_not_called()


def test_auto_install_dependencies_on_windows_returns_error():
    with patch("tools.system_setup.platform.system", return_value="Windows"):
        with pytest.raises(RuntimeError, match="not supported on Windows"):
            system_setup.auto_install_dependencies()
