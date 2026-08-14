"""Contracts for deterministic real-PTY terminal documentation captures."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

import pytest
from scripts import docs_terminal_preflight
from scripts.docs_screenshot_manifest import load_manifest
from scripts.docs_terminal_capture import (
    PNG_SIGNATURE,
    DockerCaptureBackend,
    ScenarioCaptureResult,
    TerminalCaptureError,
    capture_terminal_screenshots,
    load_capture_policy,
    normalize_container_platform,
    render_vhs_tape,
    validate_capture_policy,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "docs-screenshot-manifest.json"
POLICY = ROOT / "config" / "docs-terminal-capture-policy.json"
POLICY_SCHEMA = ROOT / "config" / "docs-terminal-capture-policy-v1.schema.json"


def _policy_payload() -> dict[str, object]:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _assert_policy_error(payload: dict[str, object], code: str) -> None:
    with pytest.raises(TerminalCaptureError) as caught:
        validate_capture_policy(payload, schema_path=POLICY_SCHEMA)
    assert caught.value.code == code


def _minimal_png(marker: bytes = b"") -> bytes:
    """Return enough PNG-shaped bytes for orchestration contract tests."""
    return PNG_SIGNATURE + b"contract-fixture" + marker


class FakeCaptureBackend:
    """Test backend that records isolation and deterministically writes PNG fixtures."""

    def __init__(
        self,
        *,
        exit_code: int = 0,
        network_isolated: bool = True,
        omit_ready_signal: bool = False,
        leaked_text: str = "",
        divergent_second_image: bool = False,
        unexpected_output: bool = False,
        prepare_error: TerminalCaptureError | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.network_isolated = network_isolated
        self.omit_ready_signal = omit_ready_signal
        self.leaked_text = leaked_text
        self.divergent_second_image = divergent_second_image
        self.unexpected_output = unexpected_output
        self.prepare_error = prepare_error
        self.prepared = False
        self.calls: list[tuple[str, int]] = []

    def prepare(self, *, repository_root: Path, policy: object) -> None:
        del repository_root, policy
        if self.prepare_error is not None:
            raise self.prepare_error
        self.prepared = True

    def capture(
        self,
        *,
        scenario: dict[str, object],
        policy_scenario: dict[str, object],
        run_number: int,
        working_directory: Path,
        image_path: Path,
        tape: str,
    ) -> ScenarioCaptureResult:
        del policy_scenario, working_directory
        assert self.prepared
        assert 'Screenshot "/capture/output.png"' in tape
        self.calls.append((str(scenario["id"]), run_number))
        marker = b"-different" if self.divergent_second_image and run_number == 2 else b""
        image_path.write_bytes(_minimal_png(marker))
        if self.unexpected_output:
            image_path.with_name("undeclared.png").write_bytes(_minimal_png())
        ready_signal = scenario["ready_signal"]
        assert isinstance(ready_signal, dict)
        transcript = "capture transcript"
        if not self.omit_ready_signal:
            transcript += f"\n{ready_signal['value']}"
        transcript += self.leaked_text
        return ScenarioCaptureResult(
            transcript=transcript,
            exit_code=self.exit_code,
            network_isolated=self.network_isolated,
        )


def _capture(tmp_path: Path, backend: FakeCaptureBackend) -> tuple[Path, ...]:
    output_root = tmp_path / "output"
    temporary_root = tmp_path / "temporary"
    output_root.mkdir()
    temporary_root.mkdir()
    captured = capture_terminal_screenshots(
        manifest_path=MANIFEST,
        policy_path=POLICY,
        repository_root=ROOT,
        output_root=output_root,
        temporary_root=temporary_root,
        backend=backend,
    )
    assert list(temporary_root.iterdir()) == []
    return captured


def test_checked_in_policy_pins_the_complete_native_vhs_toolchain() -> None:
    policy = load_capture_policy(POLICY)

    assert policy.schema_version == 1
    assert policy.container_image == (
        "ghcr.io/charmbracelet/vhs@"
        "sha256:9d5fc3dc0c160b0fb1d2212baff07e6bdf3fa9438c504a3237484567302fcf93"
    )
    assert policy.supported_platforms == {
        "linux/amd64": "sha256:16b21a3bf7bd13e7fcf60d3ece47c92e561ef594f2c1fca5f1683f11190954b8",
        "linux/arm64": "sha256:2c34085dea86c69c94bd64a9bde0f8a839aadb6032c97d4c3268fd0aaeb09262",
    }
    assert policy.tool_versions == {
        "chromium": "Chromium 145.0.7632.159 built on Debian GNU/Linux 13 (trixie)",
        "ffmpeg": "ffmpeg version 7.1.3-0+deb13u1",
        "ttyd": "ttyd version 1.7.7-e2819f2",
        "vhs": "vhs version v0.11.0 (c6af91a)",
    }
    assert policy.font == {
        "family": "JetBrains Mono",
        "path": "/usr/share/fonts/jetbrains-mono/JetBrainsMono-Regular.ttf",
        "sha256": "a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f",
    }
    assert policy.locale == {
        "name": "en_US.UTF-8",
        "path": "/usr/lib/locale/en_US.UTF-8",
        "target": "/usr/lib/locale/C.utf8",
    }
    assert policy.environment == {
        "ANCESTRYLLM_CONFIG_DIR": "/capture/ancestryllm/config",
        "ANCESTRYLLM_DATA_DIR": "/capture/ancestryllm/data",
        "HOME": "/capture/home",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "NO_COLOR": "1",
        "PATH": (
            "/usr/local/lib/ancestryllm-docs-terminal:/usr/local/sbin:/usr/local/bin:"
            "/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TERM": "xterm-256color",
        "TZ": "UTC",
        "XDG_CACHE_HOME": "/capture/xdg/cache",
        "XDG_CONFIG_HOME": "/capture/xdg/config",
        "XDG_DATA_HOME": "/capture/xdg/data",
    }


def test_policy_and_manifest_have_exact_terminal_scenario_parity() -> None:
    policy = load_capture_policy(POLICY)
    manifest = load_manifest(MANIFEST, repository_root=ROOT)
    manifest_ids = {
        str(scenario["id"]) for scenario in manifest.scenarios if scenario["surface"] == "terminal"
    }

    assert manifest_ids == {
        "terminal-cli-help",
        "terminal-interactive-console",
    }
    assert set(policy.scenarios) == manifest_ids
    assert all(
        scenario["launch"][0] == ".venv/bin/ancestry"
        for scenario in manifest.scenarios
        if scenario["surface"] == "terminal"
    )
    assert all(
        scenario["fixture_id"] == "success"
        for scenario in manifest.scenarios
        if scenario["surface"] == "terminal"
    )
    determinism = manifest.payload["determinism"]
    assert policy.locale["name"] == determinism["locale"]
    assert policy.environment["LANG"] == determinism["locale"]
    assert policy.environment["LC_ALL"] == determinism["locale"]
    assert policy.environment["TZ"] == determinism["timezone"]
    assert policy.font["family"] == determinism["fonts"]["terminal"]["family"]
    assert policy.payload["terminal"]["font_size"] == determinism["fonts"]["terminal"]["size_px"]


def test_locale_preflight_requires_the_exact_reviewed_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "C.utf8"
    target.mkdir()
    alias = tmp_path / "en_US.UTF-8"
    alias.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        docs_terminal_preflight.locale,
        "setlocale",
        lambda _category, name: name,
    )

    docs_terminal_preflight._check_locale(
        {
            "name": "en_US.UTF-8",
            "path": str(alias),
            "target": str(target),
        }
    )
    alias.unlink()
    wrong_target = tmp_path / "wrong"
    wrong_target.mkdir()
    alias.symlink_to(wrong_target, target_is_directory=True)

    with pytest.raises(docs_terminal_preflight.PreflightError) as caught:
        docs_terminal_preflight._check_locale(
            {
                "name": "en_US.UTF-8",
                "path": str(alias),
                "target": str(target),
            }
        )
    assert caught.value.code == "DOCSHOT_TERMINAL_LOCALE_MISMATCH"


def test_policy_schema_rejects_unknown_missing_and_unsupported_fields() -> None:
    unknown = _policy_payload()
    unknown["unexpected"] = True
    _assert_policy_error(unknown, "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID")

    missing = _policy_payload()
    del missing["font"]
    _assert_policy_error(missing, "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID")

    unsupported = _policy_payload()
    unsupported["schema_version"] = 2
    _assert_policy_error(unsupported, "DOCSHOT_TERMINAL_POLICY_UNSUPPORTED")

    platform_digest_missing = _policy_payload()
    platforms = platform_digest_missing["supported_platforms"]
    assert isinstance(platforms, dict)
    del platforms["linux/arm64"]
    _assert_policy_error(
        platform_digest_missing,
        "DOCSHOT_TERMINAL_POLICY_SCHEMA_INVALID",
    )


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Linux", "x86_64", "linux/amd64"),
        ("linux", "AMD64", "linux/amd64"),
        ("Linux", "aarch64", "linux/arm64"),
        ("linux", "arm64", "linux/arm64"),
    ],
)
def test_container_platform_normalization(
    system: str,
    machine: str,
    expected: str,
) -> None:
    assert normalize_container_platform(system, machine) == expected


@pytest.mark.parametrize(
    ("system", "machine"),
    [("darwin", "arm64"), ("linux", "riscv64"), ("windows", "amd64")],
)
def test_container_platform_normalization_fails_closed(system: str, machine: str) -> None:
    with pytest.raises(TerminalCaptureError) as caught:
        normalize_container_platform(system, machine)
    assert caught.value.code == "DOCSHOT_TERMINAL_PLATFORM_UNSUPPORTED"


def test_tape_fixes_terminal_geometry_theme_font_prompt_and_timing() -> None:
    manifest = load_manifest(MANIFEST, repository_root=ROOT)
    policy = load_capture_policy(POLICY)
    scenario = next(
        item for item in manifest.scenarios if item["id"] == "terminal-interactive-console"
    )
    tape = render_vhs_tape(
        scenario=scenario,
        policy_scenario=policy.scenarios["terminal-interactive-console"],
        policy=policy,
        screenshot_path="/capture/output.png",
    )

    assert 'Set Shell "bash"' in tape
    assert 'Set FontFamily "JetBrains Mono"' in tape
    assert "Set FontSize 14" in tape
    assert "Set Width 1200" in tape
    assert "Set Height 720" in tape
    assert "Set TypingSpeed 1ms" in tape
    assert (
        'Set Theme {"background":"#f8fafc","cursorColor":"#0f766e","foreground":"#0f172a"}'
    ) in tape
    assert 'Type ".venv/bin/ancestry"' in tape
    assert "Wait+Screen /ancestry >/" in tape
    assert 'Type "modules"' in tape
    assert 'Type "use gedcom"' in tape
    assert r"Wait+Screen /ancestry\(gedcom\) >/" in tape
    assert 'Type "exit"' in tape
    assert 'Screenshot "/capture/output.png"' in tape
    assert 'Screenshot "/capture/output.png"\nSleep 1s\nType "exit"' in tape
    assert "http://" not in tape
    assert "https://" not in tape
    assert "$(" not in tape
    assert "`" not in tape


def test_capture_runs_each_real_terminal_scenario_twice_and_publishes_only_pngs(
    tmp_path: Path,
) -> None:
    backend = FakeCaptureBackend()
    captured = _capture(tmp_path, backend)

    assert backend.calls == [
        ("terminal-cli-help", 1),
        ("terminal-cli-help", 2),
        ("terminal-interactive-console", 1),
        ("terminal-interactive-console", 2),
    ]
    assert tuple(path.relative_to(tmp_path / "output").as_posix() for path in captured) == (
        "docs/assets/screenshots/terminal/cli-help.png",
        "docs/assets/screenshots/terminal/interactive-console.png",
    )
    assert all(path.read_bytes() == _minimal_png() for path in captured)
    assert {path.suffix for path in (tmp_path / "output").rglob("*") if path.is_file()} == {".png"}


@pytest.mark.parametrize(
    ("backend", "expected_code"),
    [
        (
            FakeCaptureBackend(
                prepare_error=TerminalCaptureError(
                    "DOCSHOT_TERMINAL_TOOL_MISSING",
                    "vhs is missing",
                )
            ),
            "DOCSHOT_TERMINAL_TOOL_MISSING",
        ),
        (FakeCaptureBackend(exit_code=7), "DOCSHOT_TERMINAL_COMMAND_FAILED"),
        (FakeCaptureBackend(omit_ready_signal=True), "DOCSHOT_TERMINAL_READY_MISSING"),
        (FakeCaptureBackend(network_isolated=False), "DOCSHOT_TERMINAL_NETWORK_NOT_DENIED"),
        (
            FakeCaptureBackend(leaked_text=" SCREENSHOT-PRIVATE-CANARY-7F4C"),
            "DOCSHOT_PRIVACY_CANARY_LEAKED",
        ),
        (
            FakeCaptureBackend(divergent_second_image=True),
            "DOCSHOT_TERMINAL_REPEATABILITY_FAILED",
        ),
        (FakeCaptureBackend(unexpected_output=True), "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED"),
    ],
)
def test_capture_failures_are_stable_nonzero_and_leave_no_partial_state(
    tmp_path: Path,
    backend: FakeCaptureBackend,
    expected_code: str,
) -> None:
    output_root = tmp_path / "output"
    temporary_root = tmp_path / "temporary"
    output_root.mkdir()
    temporary_root.mkdir()

    with pytest.raises((TerminalCaptureError, ValueError)) as caught:
        capture_terminal_screenshots(
            manifest_path=MANIFEST,
            policy_path=POLICY,
            repository_root=ROOT,
            output_root=output_root,
            temporary_root=temporary_root,
            backend=backend,
        )

    assert getattr(caught.value, "code", None) == expected_code
    assert list(temporary_root.iterdir()) == []
    assert list(output_root.rglob("*")) == []


def test_capture_rejects_a_non_png_before_publication(tmp_path: Path) -> None:
    class InvalidImageBackend(FakeCaptureBackend):
        def capture(self, **kwargs: object) -> ScenarioCaptureResult:
            result = super().capture(**kwargs)
            image_path = kwargs["image_path"]
            assert isinstance(image_path, Path)
            image_path.write_bytes(b"not-a-png")
            return result

    output_root = tmp_path / "output"
    output_root.mkdir()
    with pytest.raises(TerminalCaptureError) as caught:
        capture_terminal_screenshots(
            manifest_path=MANIFEST,
            policy_path=POLICY,
            repository_root=ROOT,
            output_root=output_root,
            temporary_root=tmp_path,
            backend=InvalidImageBackend(),
        )
    assert caught.value.code == "DOCSHOT_TERMINAL_OUTPUT_INVALID"


def test_capture_rejects_a_symlinked_output_parent(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    temporary_root = tmp_path / "temporary"
    escaped_root = tmp_path / "escaped"
    (output_root / "docs").mkdir(parents=True)
    temporary_root.mkdir()
    escaped_root.mkdir()
    (output_root / "docs" / "assets").symlink_to(
        escaped_root,
        target_is_directory=True,
    )

    with pytest.raises(TerminalCaptureError) as caught:
        capture_terminal_screenshots(
            manifest_path=MANIFEST,
            policy_path=POLICY,
            repository_root=ROOT,
            output_root=output_root,
            temporary_root=temporary_root,
            backend=FakeCaptureBackend(),
        )

    assert caught.value.code == "DOCSHOT_TERMINAL_OUTPUT_UNDECLARED"
    assert list(escaped_root.iterdir()) == []
    assert list(temporary_root.iterdir()) == []


def test_native_host_platform_is_supported_by_the_checked_in_policy() -> None:
    """The local runner must select the native Linux container architecture."""
    host_machine = platform.machine()
    expected_machine = "arm64" if host_machine.casefold() in {"arm64", "aarch64"} else "amd64"
    policy = load_capture_policy(POLICY)
    assert f"linux/{expected_machine}" in policy.supported_platforms


class RecordingDockerRunner:
    """Return deterministic Docker responses while recording every argv token."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout_seconds
        self.commands.append(command)
        if command[:3] == ("docker", "version", "--format"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"Arch":"arm64","Os":"linux"}\n',
                stderr="",
            )
        if command[:3] == ("docker", "manifest", "inspect"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "schemaVersion": 2,
                        "manifests": [
                            {
                                "digest": (
                                    "sha256:2c34085dea86c69c94bd64a9bde0f8a839aadb6032c97d4c3268fd0aaeb09262"
                                ),
                                "platform": {"architecture": "arm64", "os": "linux"},
                            },
                            {
                                "digest": (
                                    "sha256:16b21a3bf7bd13e7fcf60d3ece47c92e561ef594f2c1fca5f1683f11190954b8"
                                ),
                                "platform": {"architecture": "amd64", "os": "linux"},
                            },
                        ],
                    }
                ),
                stderr="",
            )
        if command[:2] == ("docker", "build"):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ("docker", "run"):
            if any(token.endswith("/docs_terminal_preflight.py") for token in command):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"network_isolated":true,"status":"ok"}\n',
                    stderr="",
                )
            if any(token.endswith("/docs_terminal_pty.py") for token in command):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="usage: ancestry\nancestry >\nancestry(gedcom) >\n",
                    stderr="",
                )
            mount = next(token for token in command if token.startswith("type=bind,"))
            source = next(part for part in mount.split(",") if part.startswith("src="))[4:]
            (Path(source) / "output.png").write_bytes(_minimal_png())
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command!r}")


def test_docker_backend_pins_native_manifests_and_denies_runtime_network(
    tmp_path: Path,
) -> None:
    runner = RecordingDockerRunner()
    backend = DockerCaptureBackend(runner=runner, uid=501, gid=20)
    policy = load_capture_policy(POLICY)

    backend.prepare(repository_root=ROOT, policy=policy)
    scenario = next(
        item
        for item in load_manifest(MANIFEST, repository_root=ROOT).scenarios
        if item["id"] == "terminal-cli-help"
    )
    image_path = tmp_path / "output.png"
    result = backend.capture(
        scenario=scenario,
        policy_scenario=policy.scenarios["terminal-cli-help"],
        run_number=1,
        working_directory=tmp_path,
        image_path=image_path,
        tape='Screenshot "/capture/output.png"\n',
    )

    assert result.exit_code == 0
    assert result.network_isolated is True
    assert "usage: ancestry" in result.transcript
    assert image_path.read_bytes() == _minimal_png()

    build = next(command for command in runner.commands if command[:2] == ("docker", "build"))
    assert "--pull" in build
    assert "--platform" in build
    assert "linux/arm64" in build
    assert f"VHS_IMAGE={policy.container_image}" in build
    assert f"UV_IMAGE={policy.payload['uv_image']}" in build

    runtime_commands = [command for command in runner.commands if command[:2] == ("docker", "run")]
    assert len(runtime_commands) == 3
    preflight = next(
        command
        for command in runtime_commands
        if any(token.endswith("/docs_terminal_preflight.py") for token in command)
    )
    preflight_mount = next(token for token in preflight if token.startswith("type=bind,"))
    preflight_source = Path(
        next(part for part in preflight_mount.split(",") if part.startswith("src="))[4:]
    )
    assert preflight_source.parent == ROOT.resolve()
    assert preflight_source.name.startswith(".ancestryllm-terminal-preflight-")
    assert not preflight_source.exists()
    for command in runtime_commands:
        assert command[command.index("--network") : command.index("--network") + 2] == (
            "--network",
            "none",
        )
        assert command[command.index("--read-only") : command.index("--read-only") + 1] == (
            "--read-only",
        )
        assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == (
            "--cap-drop",
            "ALL",
        )
        assert "no-new-privileges" in command
        assert command[command.index("--user") : command.index("--user") + 2] == (
            "--user",
            "501:20",
        )
        assert "--env" in command
        assert not any("OPENAI_API_KEY" in token for token in command)


def test_capture_container_and_true_pty_scripts_are_closed_and_pinned() -> None:
    dockerfile = (ROOT / "containers" / "docs-terminal-capture.Dockerfile").read_text(
        encoding="utf-8"
    )
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    shell = (ROOT / "scripts" / "docs_terminal_shell.sh").read_text(encoding="utf-8")

    assert "FROM ${UV_IMAGE} AS uv" in dockerfile
    assert "FROM ${VHS_IMAGE}" in dockerfile
    assert "COPY --from=uv /uv /usr/local/bin/uv" in dockerfile
    assert "uv sync --locked --no-default-groups" in dockerfile
    assert "ln -s /usr/lib/locale/C.utf8 /usr/lib/locale/en_US.UTF-8" in dockerfile
    assert "/usr/local/lib/ancestryllm-docs-terminal/bash" in dockerfile
    assert "latest" not in dockerfile.casefold()
    assert "curl" not in dockerfile.casefold()
    assert "wget" not in dockerfile.casefold()
    assert "ENTRYPOINT []" in dockerfile
    assert "!containers/docs-terminal-capture.Dockerfile" in dockerignore
    assert "!scripts/docs_terminal_preflight.py" in dockerignore
    assert "!scripts/docs_terminal_pty.py" in dockerignore
    assert "!scripts/docs_terminal_shell.sh" in dockerignore
    assert "stty cols 120 rows 36" in shell
    assert 'exec /bin/bash "$@"' in shell


def test_make_exposes_opt_in_terminal_capture_without_changing_plan_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "docs-terminal-screenshots" in makefile
    assert "scripts/docs_terminal_capture.py capture" in makefile
    assert "docs-terminal-screenshots:" not in "\n".join(
        line for line in makefile.splitlines() if line.startswith("docs-screenshots:")
    )


def test_terminal_capture_operations_and_security_disposition_are_documented() -> None:
    authoring = (ROOT / "docs" / "DOCS_AUTHORING.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    threat_model = (ROOT / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
    normalized_authoring = " ".join(authoring.split())

    assert "make docs-terminal-screenshots" in authoring
    assert "For local macOS capture" in authoring
    assert "The reference CI setup" in authoring
    assert "To update the terminal toolchain" in authoring
    assert (
        "documentation embedding, drift comparison, and CI enforcement remain owned by #420"
        in normalized_authoring
    )
    assert "scripts/docs_terminal_capture.py" in architecture
    assert "true PTY" in architecture
    assert "Issue #419 deterministic terminal-capture evidence" in threat_model
    assert "The local Docker daemon" in threat_model
