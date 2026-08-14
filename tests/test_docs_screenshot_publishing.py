"""Contracts for publishing and checking deterministic documentation screenshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import docs_screenshots
from scripts.docs_screenshot_manifest import (
    ScreenshotManifestError,
    ValidatedManifest,
    validate_published_assets,
)
from scripts.docs_screenshots import (
    DocsScreenshotError,
    _copy_repository_snapshot,
    _default_capture_runner,
    _pnpm_command,
    _publish_atomically,
    check_screenshots,
    regenerate_screenshots,
    select_scenarios,
    validate_drift_report,
)


def _png(red: int, green: int, blue: int, *, filter_type: int = 0) -> bytes:
    def chunk(kind: bytes, content: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(content, checksum)
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(bytes((filter_type, red, green, blue)))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


PNG = _png(25, 91, 143)
DIFFERENT_PNG = _png(143, 91, 25)


def _manifest(
    *,
    documentation_path: str = "docs/guide.md",
    privacy_canaries: tuple[str, ...] = (),
) -> ValidatedManifest:
    return ValidatedManifest(
        payload={
            "schema_version": 1,
            "output_allowlist": ["docs/assets/screenshots/terminal/example.png"],
            "fixtures": [],
            "scenarios": [
                {
                    "id": "terminal-example",
                    "surface": "terminal",
                    "output_path": "docs/assets/screenshots/terminal/example.png",
                    "documentation": [
                        {"path": documentation_path, "anchor": "example"},
                    ],
                },
            ],
        },
        privacy_canaries=privacy_canaries,
    )


def _write_published_contract(
    repository_root: Path,
    *,
    alt_text: str = "Ancestry terminal showing fictional example output",
    image: bytes = PNG,
) -> None:
    documentation = repository_root / "docs/guide.md"
    documentation.parent.mkdir(parents=True)
    documentation.write_text(
        f"# Guide\n\n## Example\n\n![{alt_text}](assets/screenshots/terminal/example.png)\n",
        encoding="utf-8",
    )
    output = repository_root / "docs/assets/screenshots/terminal/example.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(image)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_published_assets_require_every_declared_output(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path)
    (tmp_path / manifest.scenarios[0]["output_path"]).unlink()

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_ASSET_MISSING"


def test_published_assets_reject_orphaned_pngs(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path)
    orphan = tmp_path / "docs/assets/screenshots/terminal/orphan.png"
    orphan.write_bytes(PNG)

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_ASSET_ORPHANED"


def test_published_assets_require_meaningful_alt_text(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path, alt_text="Screenshot")

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_DOC_ALT_INVALID"


def test_published_assets_reject_multiword_generic_alt_text(tmp_path: Path) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path, alt_text="Screenshot image")

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_DOC_ALT_INVALID"


def test_published_assets_parse_rendered_reference_images_and_ignore_code(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path)
    documentation = tmp_path / "docs/guide.md"
    documentation.write_text(
        """# Guide

## Example

```markdown
![Fake screenshot](assets/screenshots/terminal/unknown.png)
```

`![Another fake screenshot](assets/screenshots/terminal/also-unknown.png)`

![Ancestry terminal showing fictional example output][terminal-example]

[terminal-example]: assets/screenshots/terminal/example.png
""",
        encoding="utf-8",
    )

    validate_published_assets(manifest, repository_root=tmp_path)


@pytest.mark.parametrize(
    "malformed",
    (
        b"\x89PNG\r\n\x1a\ntruncated",
        PNG[:-1],
        PNG + b"trailing-data",
        PNG[:29] + bytes((PNG[29] ^ 1,)) + PNG[30:],
        _png(25, 91, 143, filter_type=5),
    ),
)
def test_published_assets_reject_structurally_invalid_pngs(
    tmp_path: Path,
    malformed: bytes,
) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path, image=malformed)

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_ASSET_INVALID"


def test_published_assets_reject_broken_or_undeclared_screenshot_references(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    _write_published_contract(tmp_path)
    extra_documentation = tmp_path / "docs/extra.md"
    extra_documentation.write_text(
        "# Extra\n\n![Unexpected asset](assets/screenshots/terminal/unknown.png)\n",
        encoding="utf-8",
    )

    with pytest.raises(ScreenshotManifestError) as exc_info:
        validate_published_assets(manifest, repository_root=tmp_path)

    assert exc_info.value.code == "DOCSHOT_DOC_IMAGE_UNDECLARED"


def test_scenario_selection_is_closed_and_surface_scoped() -> None:
    manifest = _manifest()

    assert [scenario["id"] for scenario in select_scenarios(manifest)] == ["terminal-example"]
    assert [scenario["id"] for scenario in select_scenarios(manifest, surfaces=("terminal",))] == [
        "terminal-example"
    ]

    with pytest.raises(DocsScreenshotError) as exc_info:
        select_scenarios(manifest, scenario_ids=("missing",))
    assert exc_info.value.code == "DOCSHOT_SCENARIO_UNKNOWN"

    with pytest.raises(DocsScreenshotError) as exc_info:
        select_scenarios(
            manifest,
            surfaces=("electron",),
            scenario_ids=("terminal-example",),
        )
    assert exc_info.value.code == "DOCSHOT_SCENARIO_SURFACE_MISMATCH"


@pytest.mark.parametrize(
    ("explicit", "runner_temp", "host_platform", "expected"),
    (
        (Path("explicit"), "/runner", "darwin", Path("explicit")),
        (None, "/runner", "darwin", Path("repository")),
        (None, "/runner", "linux", Path("/runner")),
        (None, None, "linux", Path("system-temporary")),
    ),
)
def test_temporary_root_selection_keeps_docker_binds_visible(
    explicit: Path | None,
    runner_temp: str | None,
    host_platform: str,
    expected: Path,
) -> None:
    assert (
        docs_screenshots.resolve_temporary_root(
            repository_root=Path("repository"),
            explicit=explicit,
            runner_temp=runner_temp,
            host_platform=host_platform,
            system_temp=Path("system-temporary"),
        )
        == expected
    )


def test_check_is_non_mutating_when_capture_matches(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)
    before = _snapshot(repository_root)

    def capture_runner(
        *,
        surface: str,
        scenario_ids: tuple[str, ...],
        output_root: Path,
        temporary_root: Path,
        repository_root: Path,
        manifest_path: Path,
    ) -> None:
        del temporary_root
        assert surface == "terminal"
        assert scenario_ids == ("terminal-example",)
        assert manifest_path == repository_root / "config/docs-screenshot-manifest.json"
        destination = output_root / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(PNG)

    report = check_screenshots(
        manifest,
        repository_root=repository_root,
        temporary_root=temporary_root,
        capture_runner=capture_runner,
    )

    assert report["status"] == "success"
    assert report["scenarios"][0]["status"] == "match"
    assert _snapshot(repository_root) == before
    assert list(temporary_root.iterdir()) == []


def test_custom_manifest_path_reaches_the_capture_adapter(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    custom_manifest = repository_root / "config/custom-docshots.json"
    repository_root.mkdir()
    temporary_root.mkdir()
    custom_manifest.parent.mkdir()
    custom_manifest.write_text("{}\n", encoding="utf-8")
    manifest = _manifest()
    _write_published_contract(repository_root)
    received: list[Path] = []

    def capture_runner(**arguments: Any) -> None:
        received.append(arguments["manifest_path"])
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(PNG)

    check_screenshots(
        manifest,
        manifest_path=custom_manifest,
        repository_root=repository_root,
        temporary_root=temporary_root,
        capture_runner=capture_runner,
    )

    assert received == [custom_manifest]


def test_check_writes_sanitized_hash_evidence_before_failing_drift(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    report_path = tmp_path / "report.json"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(DIFFERENT_PNG)

    with pytest.raises(DocsScreenshotError) as exc_info:
        check_screenshots(
            manifest,
            repository_root=repository_root,
            temporary_root=temporary_root,
            report_path=report_path,
            capture_runner=capture_runner,
        )

    assert exc_info.value.code == "DOCSHOT_DRIFT_DETECTED"
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["status"] == "failure"
    assert report["scenarios"] == [
        {
            "captured_sha256": hashlib.sha256(DIFFERENT_PNG).hexdigest(),
            "expected_sha256": hashlib.sha256(PNG).hexdigest(),
            "id": "terminal-example",
            "status": "hash-mismatch",
            "surface": "terminal",
        }
    ]
    assert str(repository_root) not in report_text
    assert str(temporary_root) not in report_text
    assert "docs/assets" not in report_text
    assert list(temporary_root.iterdir()) == []


def test_check_writes_drift_evidence_when_expected_asset_is_missing(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    report_path = tmp_path / "report.json"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)
    (repository_root / manifest.scenarios[0]["output_path"]).unlink()

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(PNG)

    with pytest.raises(DocsScreenshotError) as exc_info:
        check_screenshots(
            manifest,
            repository_root=repository_root,
            temporary_root=temporary_root,
            report_path=report_path,
            capture_runner=capture_runner,
        )

    assert exc_info.value.code == "DOCSHOT_DRIFT_DETECTED"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scenarios"][0]["status"] == "expected-missing"
    assert report["scenarios"][0]["expected_sha256"] == ""


def test_check_writes_drift_evidence_when_expected_asset_is_invalid(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    report_path = tmp_path / "report.json"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    malformed = b"\x89PNG\r\n\x1a\ntruncated"
    _write_published_contract(repository_root, image=malformed)

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(PNG)

    with pytest.raises(DocsScreenshotError) as exc_info:
        check_screenshots(
            manifest,
            repository_root=repository_root,
            temporary_root=temporary_root,
            report_path=report_path,
            capture_runner=capture_runner,
        )

    assert exc_info.value.code == "DOCSHOT_DRIFT_DETECTED"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scenarios"][0]["status"] == "hash-mismatch"
    assert report["scenarios"][0]["expected_sha256"] == hashlib.sha256(malformed).hexdigest()


def test_check_rejects_structurally_invalid_staged_png(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")

    with pytest.raises(DocsScreenshotError) as exc_info:
        check_screenshots(
            manifest,
            repository_root=repository_root,
            temporary_root=temporary_root,
            capture_runner=capture_runner,
        )

    assert exc_info.value.code == "DOCSHOT_CAPTURE_INVALID"


def test_check_rejects_privacy_canaries_in_captured_bytes(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest(privacy_canaries=("PRIVATE-CANARY",))
    _write_published_contract(repository_root)

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(PNG + b"PRIVATE-CANARY")

    with pytest.raises(DocsScreenshotError) as exc_info:
        check_screenshots(
            manifest,
            repository_root=repository_root,
            temporary_root=temporary_root,
            capture_runner=capture_runner,
        )

    assert exc_info.value.code == "DOCSHOT_PRIVACY_CANARY_LEAKED"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda report: report.pop("status"),
        lambda report: report.update({"unknown": True}),
        lambda report: report["scenarios"][0].update({"path": "/private/path"}),
    ),
)
def test_drift_report_schema_rejects_missing_or_unknown_fields(
    mutation: Any,
) -> None:
    report = {
        "schema_version": 1,
        "status": "success",
        "scenarios": [
            {
                "captured_sha256": "a" * 64,
                "expected_sha256": "a" * 64,
                "id": "terminal-example",
                "status": "match",
                "surface": "terminal",
            }
        ],
    }
    mutation(report)

    with pytest.raises(DocsScreenshotError) as exc_info:
        validate_drift_report(report)

    assert exc_info.value.code == "DOCSHOT_REPORT_SCHEMA_INVALID"


def test_drift_report_schema_rejects_semantically_false_success() -> None:
    report = {
        "schema_version": 1,
        "status": "success",
        "scenarios": [
            {
                "captured_sha256": "b" * 64,
                "expected_sha256": "a" * 64,
                "id": "terminal-example",
                "status": "hash-mismatch",
                "surface": "terminal",
            }
        ],
    }

    with pytest.raises(DocsScreenshotError) as exc_info:
        validate_drift_report(report)

    assert exc_info.value.code == "DOCSHOT_REPORT_SCHEMA_INVALID"


def test_electron_repository_snapshot_copies_files_outside_the_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    workspace = tmp_path / "isolated-workspace"
    source = repository_root / "desktop/package.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"private": true}\n', encoding="utf-8")

    def list_files(*args: Any, **kwargs: Any) -> SimpleNamespace:
        assert args == (
            (
                "/usr/bin/git",
                "ls-files",
                "--cached",
                "-z",
            ),
        )
        assert kwargs["cwd"] == repository_root
        return SimpleNamespace(returncode=0, stdout=b"desktop/package.json\0")

    monkeypatch.setattr("scripts.docs_screenshots.shutil.which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr("scripts.docs_screenshots.subprocess.run", list_files)

    _copy_repository_snapshot(repository_root, workspace)

    copied = workspace / "desktop/package.json"
    assert copied.read_bytes() == source.read_bytes()
    copied.write_text('{"private": false}\n', encoding="utf-8")
    assert source.read_text(encoding="utf-8") == '{"private": true}\n'


def test_electron_capture_uses_locked_installer_and_selected_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    output_root = tmp_path / "output"
    custom_manifest = repository_root / "config/custom-docshots.json"
    repository_root.mkdir()
    temporary_root.mkdir()
    output_root.mkdir()
    custom_manifest.parent.mkdir()
    custom_manifest.write_text('{"selected": true}\n', encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    def copy_snapshot(_repository_root: Path, workspace: Path) -> None:
        workspace.mkdir()

    def run_command(command: tuple[str, ...], **_arguments: Any) -> None:
        commands.append(command)

    monkeypatch.setattr(docs_screenshots, "_copy_repository_snapshot", copy_snapshot)
    monkeypatch.setattr(docs_screenshots, "_run_electron_command", run_command)
    monkeypatch.setattr(docs_screenshots, "_electron_build_environment", lambda _root: {})

    _default_capture_runner(
        surface="electron",
        scenario_ids=("electron-example",),
        output_root=output_root,
        temporary_root=temporary_root,
        repository_root=repository_root,
        manifest_path=custom_manifest,
    )

    staged_manifest = temporary_root / "electron-workspace/config/docs-screenshot-manifest.json"
    assert staged_manifest.read_bytes() == custom_manifest.read_bytes()
    assert commands == [
        ("node", "desktop/scripts/install-locked.mjs"),
        ("pnpm", "--dir", "desktop", "capture:docs"),
    ]


def test_electron_commands_cannot_wait_for_interactive_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def run(command: tuple[str, ...], **arguments: Any) -> SimpleNamespace:
        observed["command"] = command
        observed.update(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(docs_screenshots.subprocess, "run", run)

    docs_screenshots._run_electron_command(
        ("pnpm", "--dir", "desktop", "capture:docs"),
        repository_root=tmp_path,
        environment={"PATH": os.environ["PATH"]},
    )

    assert observed["stdin"] is subprocess.DEVNULL


def test_windows_pnpm_capture_uses_command_prompt() -> None:
    assert _pnpm_command(
        ("--dir", "desktop", "capture:docs"),
        host_platform="win32",
        environment={},
    ) == (
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        "pnpm.cmd",
        "--dir",
        "desktop",
        "capture:docs",
    )


def test_electron_environment_preserves_uppercase_windows_command_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        docs_screenshots.os,
        "environ",
        {"COMSPEC": r"C:\Windows\System32\cmd.exe", "UNRELATED_SECRET": "excluded"},
    )

    environment = docs_screenshots._electron_build_environment(tmp_path)

    assert environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert "UNRELATED_SECRET" not in environment


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_regeneration_publishes_world_readable_assets(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    repository_root.mkdir()
    temporary_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)
    output = repository_root / manifest.scenarios[0]["output_path"]
    output.chmod(0o600)

    def capture_runner(**arguments: Any) -> None:
        destination = arguments["output_root"] / manifest.scenarios[0]["output_path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(DIFFERENT_PNG)

    regenerate_screenshots(
        manifest,
        repository_root=repository_root,
        temporary_root=temporary_root,
        capture_runner=capture_runner,
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_publish_rollback_restores_original_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    captured_root = tmp_path / "captured"
    repository_root.mkdir()
    captured_root.mkdir()
    manifest = _manifest()
    _write_published_contract(repository_root)
    output = repository_root / manifest.scenarios[0]["output_path"]
    output.chmod(0o640)
    original = output.read_bytes()
    staged = captured_root / manifest.scenarios[0]["output_path"]
    staged.parent.mkdir(parents=True)
    staged.write_bytes(DIFFERENT_PNG)

    def fail_validation(*_args: Any, **_kwargs: Any) -> None:
        raise ScreenshotManifestError("DOCSHOT_ASSET_INVALID", "forced rollback")

    monkeypatch.setattr(docs_screenshots, "validate_published_assets", fail_validation)

    with pytest.raises(ScreenshotManifestError):
        _publish_atomically(
            manifest,
            manifest.scenarios,
            repository_root=repository_root,
            captured_root=captured_root,
        )

    assert output.read_bytes() == original
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
