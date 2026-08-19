"""Contract coverage for the privileged Codex pull-request review workflow."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _normalized(path: str) -> str:
    return " ".join(_read(path).split())


def test_review_workflow_loads_its_guidance_from_the_recorded_base_sha() -> None:
    skill = _read(".agents/skills/code-review/SKILL.md")
    agents = _read("AGENTS.md")
    contributing = _read("CONTRIBUTING.md")

    assert 'git show "$BASE_SHA:AGENTS.md"' in skill
    assert 'git show "$BASE_SHA:.agents/skills/code-review/SKILL.md"' in skill
    for document in (skill, agents, contributing):
        assert "recorded base SHA" in document
        assert "pull-request head" in document


def test_review_workflow_reuses_only_a_trusted_successful_codex_result() -> None:
    skill = _normalized(".agents/skills/code-review/SKILL.md")
    agents = _normalized("AGENTS.md")

    for document in (skill, agents):
        assert "expected Codex integration identity" in document
        assert "exact trusted review-request comment" in document
        assert "explicitly unsuccessful Codex result blocks delivery" in document


def test_privileged_review_boundary_is_recorded_in_architecture_and_threat_model() -> None:
    architecture = _read("ARCHITECTURE.md")
    threat_model = _read("docs/THREAT_MODEL.md")

    assert "Repository delivery review authority" in architecture
    assert "Repository delivery review boundary" in threat_model
    assert "recorded base SHA" in architecture
    assert "recorded base SHA" in threat_model
