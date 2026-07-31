"""Repository process contracts for test-driven development."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISSUE_TEMPLATE_ROOT = ROOT / ".github" / "ISSUE_TEMPLATE"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _form_component(form: str, component_id: str) -> str:
    marker = f"  - type: textarea\n    id: {component_id}\n"
    _, separator, remainder = form.partition(marker)
    assert separator, f"missing required issue-form component: {component_id}"
    return marker + remainder.split("\n  - type:", maxsplit=1)[0]


def test_contributor_guidance_defines_the_red_green_refactor_loop() -> None:
    contributing = _read(ROOT / "CONTRIBUTING.md")

    assert "## Test-driven development" in contributing
    for stage in ("1. **Red:**", "2. **Green:**", "3. **Refactor:**"):
        assert stage in contributing
    assert "expected failure" in contributing
    assert "Bug fixes begin with a regression test" in contributing
    assert "non-behavioral" in contributing


def test_agent_guidance_points_contributors_to_the_tdd_policy() -> None:
    for path in (ROOT / "AGENTS.md", ROOT / ".github" / "copilot-instructions.md"):
        guidance = _read(path)
        assert "red-green-refactor" in guidance
        assert "before changing production code" in guidance
        assert "CONTRIBUTING.md#test-driven-development" in guidance


def test_behavior_issue_forms_require_acceptance_tests_and_validation() -> None:
    for filename in ("work_item.yml", "bug_report.yml", "feature_request.yml"):
        form = _read(ISSUE_TEMPLATE_ROOT / filename)
        for component_id in (
            "acceptance-criteria",
            "test-first-plan",
            "validation-evidence",
        ):
            component = _form_component(form, component_id)
            assert "validations:\n      required: true" in component

    assert not (ISSUE_TEMPLATE_ROOT / "bug_report.md").exists()
    assert not (ISSUE_TEMPLATE_ROOT / "feature_request.md").exists()


def test_pull_request_template_requests_auditable_tdd_evidence() -> None:
    pull_request_template = _read(ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md")

    for heading in (
        "## Acceptance criteria",
        "## TDD evidence",
        "### Red",
        "### Green",
        "### Refactor",
        "## Validation evidence",
    ):
        assert heading in pull_request_template
    assert "No behavioral test applies" in pull_request_template
    assert "exact test names" in pull_request_template
