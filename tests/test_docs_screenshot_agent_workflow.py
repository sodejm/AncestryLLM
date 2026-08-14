"""Contracts for the repository-local screenshot regeneration agent workflow."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.docs_screenshot_manifest import load_manifest
from scripts.docs_screenshots import check_screenshots, select_scenarios

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "docs-screenshot-manifest.json"
SKILL = ROOT / ".agents" / "skills" / "docs-screenshot-regeneration" / "SKILL.md"


def _target_body(makefile: str, target_name: str) -> str:
    declaration = re.search(rf"(?m)^{re.escape(target_name)}:[^\n]*\n", makefile)
    assert declaration is not None
    remaining = makefile[declaration.end() :]
    next_target = re.search(r"(?m)^[A-Za-z0-9_.$()/%-]+:[^\n]*\n", remaining)
    return remaining[: next_target.start()] if next_target else remaining


def test_skill_declares_a_canonical_screenshot_orchestration_contract() -> None:
    assert SKILL.is_file()
    text = SKILL.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    frontmatter = text.split("---\n", maxsplit=2)[1]
    assert re.search(r"(?m)^name: docs-screenshot-regeneration$", frontmatter)
    assert re.search(r"(?m)^license: MIT$", frontmatter)
    assert "documentation screenshot" in frontmatter.lower()

    assert "config/docs-screenshot-manifest.json" in text
    assert "single source of truth" in text.lower()
    assert "make docs-screenshots DOCS_SCREENSHOT_SCENARIO=<scenario-id>" in text
    assert "make docs-screenshots DOCS_SCREENSHOT_SURFACE=<surface>" in text
    assert "make docs-screenshots-check" in text
    assert "python scripts/docs_screenshots.py capture" not in text


def test_skill_fails_closed_and_requires_a_complete_final_report() -> None:
    text = SKILL.read_text(encoding="utf-8").lower()

    for required_contract in (
        "git status --short --untracked-files=all",
        "stop before capture",
        "do not clean, reset, stash, or revert",
        "output_allowlist",
        "provider `none`",
        "network `disabled`",
        "fictional",
        "privacy canary",
        "pinned rendering tool versions",
        "no staging, commits, pushes, or pull requests",
        "changed/regenerated",
        "unchanged",
        "owning documentation",
        "privacy and drift",
    ):
        assert required_contract in text


def test_contributor_docs_cross_link_the_agent_workflow_and_record_impact() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    authoring = (ROOT / "docs" / "DOCS_AUTHORING.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    threat_model = (ROOT / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")

    assert ".agents/skills/docs-screenshot-regeneration/SKILL.md" in contributing
    assert (
        "https://github.com/sodejm/AncestryLLM/blob/main/"
        ".agents/skills/docs-screenshot-regeneration/SKILL.md"
    ) in authoring
    assert ".agents/skills/docs-screenshot-regeneration/SKILL.md" in architecture
    assert "repository tooling only" in architecture
    assert "Issue #421 screenshot-agent-workflow evidence" in threat_model


def test_make_passes_focused_selection_without_shell_reinterpretation() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    capture_target = _target_body(makefile, "docs-screenshots")
    check_target = _target_body(makefile, "docs-screenshots-check")

    assert "export DOCS_SCREENSHOT_SURFACE" in makefile
    assert "export DOCS_SCREENSHOT_SCENARIO" in makefile
    assert "override DOCS_SCREENSHOT_SURFACE := $(value DOCS_SCREENSHOT_SURFACE)" in makefile
    assert "override DOCS_SCREENSHOT_SCENARIO := $(value DOCS_SCREENSHOT_SCENARIO)" in makefile
    assert "$$DOCS_SCREENSHOT_SURFACE" in capture_target
    assert "$$DOCS_SCREENSHOT_SCENARIO" in capture_target
    assert '--surface "$$DOCS_SCREENSHOT_SURFACE"' in capture_target
    assert '--scenario "$$DOCS_SCREENSHOT_SCENARIO"' in capture_target
    assert "$(DOCS_SCREENSHOT_SURFACE)" not in capture_target
    assert "$(DOCS_SCREENSHOT_SCENARIO)" not in capture_target
    assert "DOCS_SCREENSHOT_SURFACE" not in check_target
    assert "DOCS_SCREENSHOT_SCENARIO" not in check_target


def test_make_never_evaluates_selector_values_as_make_syntax(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "selector-was-evaluated"
    completed = subprocess.run(
        [
            "make",
            "--dry-run",
            "docs-screenshots",
            f"DOCS_SCREENSHOT_SCENARIO=$(shell touch {marker})",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    ("scenario_id", "surface"),
    (
        ("electron-ready-home", "electron"),
        ("terminal-cli-help", "terminal"),
    ),
)
def test_fixture_dry_run_reports_one_scenario_without_repository_writes(
    scenario_id: str,
    surface: str,
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST, repository_root=ROOT)
    selected = select_scenarios(
        manifest,
        surfaces=(surface,),
        scenario_ids=(scenario_id,),
    )
    fixtures = {fixture["id"]: fixture for fixture in manifest.fixtures}

    assert len(selected) == 1
    scenario = selected[0]
    fixture = fixtures[scenario["fixture_id"]]
    assert scenario["id"] == scenario_id
    assert scenario["surface"] == surface
    assert scenario["output_path"] in manifest.payload["output_allowlist"]
    assert fixture["provider"] == "none"
    assert fixture["network"] == "disabled"
    assert fixture["fictional"] is True
    assert all((ROOT / reference["path"]).is_file() for reference in scenario["documentation"])

    repository_root = tmp_path / "repository"
    temporary_root = tmp_path / "temporary"
    owned_paths = {Path(str(candidate["output_path"])) for candidate in manifest.scenarios} | {
        Path(str(reference["path"]))
        for candidate in manifest.scenarios
        for reference in candidate["documentation"]
    }
    owned_paths.add(MANIFEST.relative_to(ROOT))
    for relative_path in sorted(owned_paths):
        destination = repository_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, destination)
    temporary_root.mkdir()
    before = {
        path.relative_to(repository_root).as_posix(): path.read_bytes()
        for path in repository_root.rglob("*")
        if path.is_file()
    }

    def capture_runner(
        *,
        surface: str,
        scenario_ids: tuple[str, ...],
        output_root: Path,
        temporary_root: Path,
        repository_root: Path,
        manifest_path: Path,
    ) -> None:
        assert surface == scenario["surface"]
        assert scenario_ids == (scenario_id,)
        assert temporary_root.parent == tmp_path / "temporary"
        assert repository_root == tmp_path / "repository"
        assert manifest_path == repository_root / MANIFEST.relative_to(ROOT)
        output = output_root / str(scenario["output_path"])
        output.parent.mkdir(parents=True)
        shutil.copy2(repository_root / str(scenario["output_path"]), output)

    report = check_screenshots(
        manifest,
        repository_root=repository_root,
        temporary_root=temporary_root,
        manifest_path=repository_root / MANIFEST.relative_to(ROOT),
        surfaces=(surface,),
        scenario_ids=(scenario_id,),
        capture_runner=capture_runner,
    )

    after = {
        path.relative_to(repository_root).as_posix(): path.read_bytes()
        for path in repository_root.rglob("*")
        if path.is_file()
    }
    assert report["status"] == "success"
    assert report["scenarios"] == [
        {
            "captured_sha256": report["scenarios"][0]["expected_sha256"],
            "expected_sha256": report["scenarios"][0]["expected_sha256"],
            "id": scenario_id,
            "status": "match",
            "surface": surface,
        },
    ]
    assert after == before
    assert list(temporary_root.iterdir()) == []
