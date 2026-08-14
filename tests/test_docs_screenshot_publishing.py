"""Contracts for publishing and checking deterministic documentation screenshots."""

from __future__ import annotations

import json
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
    check_screenshots,
    select_scenarios,
    validate_drift_report,
)

PNG = b"\x89PNG\r\n\x1a\npublished"
DIFFERENT_PNG = b"\x89PNG\r\n\x1a\ndifferent"


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
    ) -> None:
        del temporary_root, repository_root
        assert surface == "terminal"
        assert scenario_ids == ("terminal-example",)
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
            "captured_sha256": "0bdb0e54c90c4e84e72592e8e230d8930a3626a53cbda0e2c7f508d01b3136ea",
            "expected_sha256": "6a24561e65446f892a5c1577336d1dd9ff7f903955713413bde2cc6c4015be6b",
            "id": "terminal-example",
            "status": "hash-mismatch",
            "surface": "terminal",
        }
    ]
    assert str(repository_root) not in report_text
    assert str(temporary_root) not in report_text
    assert "docs/assets" not in report_text
    assert list(temporary_root.iterdir()) == []


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
