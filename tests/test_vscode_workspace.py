"""Guard portable editor entry points and repository-owned development commands."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_names_existing_directories_without_machine_paths() -> None:
    workspace = json.loads((ROOT / "AncestryLLM.code-workspace").read_text())
    folders = workspace["folders"]
    assert folders[0] == {"name": "Repository", "path": "."}
    assert len({folder["name"] for folder in folders}) == len(folders)
    assert len({folder["path"] for folder in folders}) == len(folders)
    for folder in folders:
        path = Path(folder["path"])
        assert not path.is_absolute()
        assert (ROOT / path).is_dir()
        assert (ROOT / path).resolve().is_relative_to(ROOT)


def test_python_discovery_has_one_owner_and_does_not_load_dotenv() -> None:
    workspace = json.loads((ROOT / "AncestryLLM.code-workspace").read_text())
    settings = json.loads((ROOT / ".vscode/settings.json").read_text())
    assert workspace["settings"]["python.testing.pytestEnabled"] is False
    assert settings["python.testing.pytestEnabled"] is True
    for configuration in (workspace["settings"], settings):
        assert configuration["python.envFile"] == ""
        assert configuration["python.testing.unittestEnabled"] is False
        assert configuration["python.defaultInterpreterPath"].endswith("/.venv")
    for folder in workspace["folders"][1:]:
        nested = ROOT / folder["path"] / ".vscode/settings.json"
        if nested.exists():
            assert not json.loads(nested.read_text()).get("python.testing.pytestEnabled")


def test_editor_tasks_use_existing_commands_from_repository_root() -> None:
    tasks = json.loads((ROOT / ".vscode/tasks.json").read_text())["tasks"]
    makefile = (ROOT / "Makefile").read_text()
    package = json.loads((ROOT / "desktop/package.json").read_text())
    for task in tasks:
        assert task["type"] == "process"
        assert task["options"]["cwd"] == "${workspaceFolder}"
        assert task.get("runOptions", {}).get("runOn") != "folderOpen"
        if task["command"] == "make":
            assert re.search(rf"^{re.escape(task['args'][0])}:", makefile, re.MULTILINE)
        else:
            assert task["command"] == "pnpm"
            assert task["args"][:2] == ["--dir", "desktop"]
            assert task["args"][2] in package["scripts"]


def test_debug_launches_use_root_environment_on_each_platform() -> None:
    launches = json.loads((ROOT / ".vscode/launch.json").read_text())["configurations"]
    for launch in launches:
        assert launch["type"] == "debugpy"
        assert launch["cwd"] == "${workspaceFolder}"
        assert launch["python"] == "${workspaceFolder}/.venv/bin/python"
        assert launch["windows"]["python"] == "${workspaceFolder}/.venv/Scripts/python.exe"
        assert launch["envFile"] == ""
        assert launch["console"] == "integratedTerminal"
