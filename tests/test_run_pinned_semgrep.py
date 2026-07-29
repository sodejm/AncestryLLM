from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "run_pinned_semgrep.py"


class _Download:
    def __init__(self, content: bytes, url: str) -> None:
        self._content = content
        self._url = url

    def __enter__(self) -> _Download:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, _limit: int) -> bytes:
        return self._content


def _load_runner() -> ModuleType:
    assert _SCRIPT.is_file(), "the pinned Semgrep runner must be checked in"
    spec = importlib.util.spec_from_file_location("run_pinned_semgrep", _SCRIPT)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    return runner


@pytest.mark.parametrize(
    ("response_content", "response_url", "expected_error"),
    (
        (b"changed content here", "https://semgrep.dev/c/p/test", "content hash differs"),
        (b"rules:\n- id: pinned\n", "https://semgrep.dev/c/p/redirected", "redirected"),
    ),
)
def test_rejects_changed_or_redirected_registry_content_before_writing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response_content: bytes,
    response_url: str,
    expected_error: str,
) -> None:
    runner = _load_runner()
    expected = b"rules:\n- id: pinned\n"
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        sha256=hashlib.sha256(expected).hexdigest(),
        size=len(expected),
    )
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(response_content, response_url),
    )
    destination = tmp_path / "test.yml"

    with pytest.raises(RuntimeError, match=expected_error):
        runner.download_rule_bundle(bundle, destination)

    assert not destination.exists()


def test_rejects_changed_registry_size_before_writing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    payload = b"rules:\n- id: pinned\n"
    bundle = runner.RuleBundle(
        name="test",
        url="https://semgrep.dev/c/p/test",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload) + 1,
    )
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(payload, request.full_url),
    )
    destination = tmp_path / "test.yml"

    with pytest.raises(RuntimeError, match="size differs"):
        runner.download_rule_bundle(bundle, destination)

    assert not destination.exists()


def test_resolves_semgrep_beside_the_script_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    interpreter = tmp_path / "python"
    semgrep = tmp_path / "semgrep"
    semgrep.touch()
    monkeypatch.setattr(runner.sys, "executable", str(interpreter))

    assert runner._semgrep_executable() == semgrep


def test_runs_locked_semgrep_with_verified_temporary_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    payloads = {
        "https://semgrep.dev/c/p/python": b"rules:\n- id: python\n",
        "https://semgrep.dev/c/p/secrets": b"rules:\n- id: secrets\n",
    }
    bundles = tuple(
        runner.RuleBundle(
            name=name,
            url=url,
            sha256=hashlib.sha256(payloads[url]).hexdigest(),
            size=len(payloads[url]),
        )
        for name, url in (
            ("python", "https://semgrep.dev/c/p/python"),
            ("secrets", "https://semgrep.dev/c/p/secrets"),
        )
    )
    monkeypatch.setattr(runner, "RULE_BUNDLES", bundles)
    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout: _Download(payloads[request.full_url], request.full_url),
    )
    monkeypatch.setattr(runner, "_semgrep_executable", lambda: Path("/locked/semgrep"))
    calls: list[list[str]] = []
    config_paths: list[Path] = []
    runtime_paths: list[Path] = []

    def run(command: list[str], *, check: bool, env: dict[str, str]) -> SimpleNamespace:
        assert check is False
        runtime_paths.extend((Path(env["SEMGREP_LOG_FILE"]), Path(env["SEMGREP_SETTINGS_FILE"])))
        assert all(path.parent == Path(command[6]).parent for path in runtime_paths)
        for path in runtime_paths:
            path.touch()
        calls.append(command)
        config_paths.extend(
            Path(command[index + 1]) for index, value in enumerate(command) if value == "--config"
        )
        assert [path.read_bytes() for path in config_paths] == list(payloads.values())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", run)

    assert runner.main(["src"]) == 0
    assert calls == [
        [
            "/locked/semgrep",
            "scan",
            "--error",
            "--metrics=off",
            "--disable-version-check",
            "--config",
            str(config_paths[0]),
            "--config",
            str(config_paths[1]),
            "src",
        ]
    ]
    assert all(not path.exists() for path in (*config_paths, *runtime_paths))
