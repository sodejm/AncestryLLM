"""Contract checks for shared repository agent guidance."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
COPILOT = ROOT / ".github" / "copilot-instructions.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
WORK_ITEM_TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "work_item.yml"
PULL_REQUEST_TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_copilot_guidance_defers_to_shared_authorities() -> None:
    text = _normalized(COPILOT)

    assert "[AGENTS.md](../AGENTS.md)" in text
    assert "[ARCHITECTURE.md](../ARCHITECTURE.md)" in text
    assert "must not weaken the shared requirements" in text


def test_agent_guidance_agrees_on_implemented_and_future_boundaries() -> None:
    agents = _normalized(AGENTS)
    copilot = _normalized(COPILOT)

    shared_contracts = (
        "one-shot CLI",
        "prompt-toolkit/Rich REPL",
        "FastAPI",
        "Electron",
        "bounded Electron 0.6 desktop control shell",
        "Home, Diagnostics, Settings, and onboarding",
        "source-level gates",
        "target-matched evidence",
        "future adapters",
        "same application-service contracts",
        "`CommandSpec`",
        "`ModuleDescriptor`",
        "`CommandInvocation`",
        "`CommandExecutor`",
        "application ports, DTOs, artifact references, and error mapping",
        "transport-neutral",
        "serializable",
    )
    for contract in shared_contracts:
        assert contract in agents
        assert contract in copilot

    stale_claims = (
        "executor/DTO boundary remains planned",
        "transport-neutral 0.3 executor as implemented until",
        "isolated 0.5.0 work",
    )
    for claim in stale_claims:
        assert claim not in agents
        assert claim not in copilot


def test_agent_guidance_agrees_on_validation_safety_and_workflow() -> None:
    agents = _normalized(AGENTS)
    copilot = _normalized(COPILOT)

    shared_contracts = (
        "`make test`",
        "`make lint`",
        "`make typecheck`",
        "`make security`",
        "exit code 130",
        "stable coded errors",
        "fictional data",
        "RootsMagic",
        "read-only",
        "loss-minimal",
        "Provider `none`",
        "network-free",
        "explicit provider selection and user consent",
        "OS keyring",
        "Do not auto-load `.env`",
        "appropriate `feature/*`, `bugfix/*`, or `hotfix/*`",
        "push unless explicitly requested",
        "tests, documentation, dead code",
        "acceptance criteria",
    )
    for contract in shared_contracts:
        assert contract in agents
        assert contract in copilot


def test_contributing_defines_the_github_flow_branch_contract() -> None:
    contributing = _normalized(CONTRIBUTING)

    for contract in (
        "GitHub Flow branch strategy",
        "`feature/*`",
        "`bugfix/*`",
        "`hotfix/*`",
        "current `origin/main`",
        "pull request",
        "protected",
        "short-lived",
        "Dependabot",
        "AWS Prescriptive Guidance",
    ):
        assert contract in contributing


def test_agent_and_issue_guidance_require_a_classified_branch_prefix() -> None:
    agents = _normalized(AGENTS)
    copilot = _normalized(COPILOT)
    work_item = _normalized(WORK_ITEM_TEMPLATE)
    pull_request = _normalized(PULL_REQUEST_TEMPLATE)

    for guidance in (agents, copilot, work_item, pull_request):
        assert "`feature/*`" in guidance
        assert "`bugfix/*`" in guidance
        assert "`hotfix/*`" in guidance
