"""Contract tests for the v0.6 desktop learning path."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
DOCS = ROOT / "docs"

LEARNING_PATH = {
    "tutorials/desktop-first-run.md": "Desktop first run",
    "how-to/desktop-diagnostics.md": "Recover with desktop diagnostics",
    "how-to/desktop-file-access.md": "Grant desktop file access",
    "how-to/desktop-provider-consent.md": "Configure a desktop provider and consent",
    "how-to/desktop-tasks.md": "Monitor and cancel desktop tasks",
    "how-to/desktop-chat.md": "Use transient desktop chat",
    "reference/DESKTOP.md": "Desktop reference",
}

PRODUCT_DEPENDENCY_DOCS = {
    106: ("../tutorials/desktop-first-run.md", "#accessibility-contract"),
    107: ("../tutorials/desktop-first-run.md", "../how-to/desktop-diagnostics.md"),
    108: ("../how-to/desktop-provider-consent.md", "#settings-catalog"),
    109: ("../how-to/desktop-tasks.md", "#stable-error-codes"),
    112: ("../how-to/desktop-chat.md", "#chat-limits-and-states"),
}


def _read(relative_path: str) -> str:
    return (DOCS / relative_path).read_text(encoding="utf-8")


def _normalized(relative_path: str) -> str:
    return " ".join(_read(relative_path).split())


def test_desktop_learning_path_pages_are_published_and_discoverable() -> None:
    """Every focused page is canonical, navigable, inventoried, and searchable."""
    home = _read("Home.md")
    sidebar = _read("_Sidebar.md")
    inventory = _read("DOCS_AUTHORING.md")
    metadata = json.loads(_read("_data/page_metadata.json"))

    for relative_path, title in LEARNING_PATH.items():
        assert (DOCS / relative_path).is_file(), relative_path
        link = f"[{title}]({relative_path})"
        assert link in home, relative_path
        assert link in sidebar, relative_path
        assert f"| `{relative_path}` |" in inventory, relative_path
        assert relative_path in metadata, relative_path


def test_first_run_tutorial_reaches_a_safe_meaningful_result() -> None:
    """The tutorial uses fictional local state and offers supported next steps."""
    tutorial = _normalized("tutorials/desktop-first-run.md")

    for contract in (
        "provider=none",
        "Continue to Home",
        "Open read-only diagnostics",
        "Review welcome",
        "Local control channel",
        "fictional",
        "network-free",
        "CLI reference",
        "interactive console",
    ):
        assert contract in tutorial


def test_first_run_screenshot_is_accessible_and_manifest_owned() -> None:
    """The reused fictional screenshot remains declared and reproducible."""
    tutorial = _read("tutorials/desktop-first-run.md")
    manifest = json.loads(
        (ROOT / "config/docs-screenshot-manifest.json").read_text(encoding="utf-8")
    )
    ready_home = next(
        scenario for scenario in manifest["scenarios"] if scenario["id"] == "electron-ready-home"
    )

    assert (
        "![AncestryLLM desktop Home showing the fictional provider-none ready state]"
        "(../assets/screenshots/electron/ready-home.png)"
    ) in tutorial
    assert {
        "path": "docs/tutorials/desktop-first-run.md",
        "anchor": "3-confirm-the-local-home-state",
    } in ready_home["documentation"]


def test_desktop_how_tos_match_shipped_labels_and_safety_boundaries() -> None:
    """Task guides use exact labels without inventing renderer authority."""
    diagnostics = _normalized("how-to/desktop-diagnostics.md")
    file_access = _normalized("how-to/desktop-file-access.md")
    providers = _normalized("how-to/desktop-provider-consent.md")
    tasks = _normalized("how-to/desktop-tasks.md")
    chat = _normalized("how-to/desktop-chat.md")

    for label in (
        "Retry desktop service",
        "Configuration",
        "Encrypted database support",
        "Credential storage",
        "Local workspace",
    ):
        assert label in diagnostics
    assert "stable code" in diagnostics
    assert "sanitized" in diagnostics

    for boundary in (
        "opaque",
        "single-use",
        "app-session",
        "requesting-window",
        "RootsMagic",
        "immutable",
        "loss-minimal",
    ):
        assert boundary in file_access
    assert "does not expose a standalone file-grant workspace" in file_access
    assert "path" in file_access

    for label in (
        "Provider configuration",
        "Test endpoint",
        "Review consent",
        "Save consent",
        "Revoke",
    ):
        assert label in providers
    assert "provider=none" in providers
    assert "explicit" in providers

    for label in (
        "Refresh tasks",
        "Cancel task",
        "Waiting for a safe point",
        "Progress total unknown.",
    ):
        assert label in tasks
    assert "grant-mediated" in tasks

    for label in (
        "Transient conversation",
        "Not saved",
        "Provider and privacy scope",
        "Ctrl+Enter",
        "Stop response",
        "Regenerate response",
        "Copy response",
    ):
        assert label in chat
    assert "advisory" in chat
    assert "not evidence" in chat


def test_desktop_reference_covers_navigation_states_codes_and_platforms() -> None:
    """The lookup page records tested labels, states, recovery, and accessibility."""
    reference = _normalized("reference/DESKTOP.md")

    for route in ("Home", "Chat", "Tasks", "Diagnostics", "Settings"):
        assert route in reference
    for state in (
        "Starting",
        "Ready",
        "Degraded",
        "Stopped",
        "Queued",
        "Running",
        "Cancelling",
        "Waiting for a safe point",
        "Completed",
        "Failed",
        "Cancelled",
    ):
        assert state in reference
    for code in (
        "STARTUP_MUTATION_BLOCKED",
        "FILE_GRANT_STALE",
        "JOB_NOT_FOUND",
        "CHAT_STREAM_STALLED",
        "INTERNAL_ERROR",
    ):
        assert f"`{code}`" in reference
    for accessibility_contract in (
        "Skip to workspace",
        "Ctrl+K",
        "Command+K",
        "Escape",
        "screen reader",
    ):
        assert accessibility_contract in reference
    for platform in ("macOS", "Windows", "Linux"):
        assert platform in reference


def test_product_dependencies_link_to_their_verified_documentation() -> None:
    """Each delivery-gating issue points readers to its shipped learning path."""
    reference = _read("reference/DESKTOP.md")

    for issue, documentation_links in PRODUCT_DEPENDENCY_DOCS.items():
        assert f"https://github.com/sodejm/AncestryLLM/issues/{issue}" in reference
        for documentation_link in documentation_links:
            assert f"]({documentation_link})" in reference


def test_desktop_explanations_link_architecture_and_security_authorities() -> None:
    """Explanations connect user choices to reviewed architecture and controls."""
    shell = _normalized("explanation/DESKTOP_SHELL.md")
    privacy = _normalized("explanation/PRIVACY_AND_CONSENT.md")

    for text in (shell, privacy):
        assert "ADR-0025" in text
        assert "THREAT_MODEL.md" in text
    for concept in (
        "loopback",
        "not a public or LAN API",
        "OS keyring",
        "opaque grants",
        "safe point",
    ):
        assert concept in f"{shell} {privacy}"
