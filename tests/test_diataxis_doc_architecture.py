"""Contract tests for the Diátaxis documentation architecture.

These tests verify that the documentation inventory and authoring rules exist
and satisfy the structural requirements defined in sodejm/AncestryLLM#259.
They do not validate prose quality; that is a human editorial concern.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_doc_authoring_exists() -> None:
    """DOC_AUTHORING.md must exist in docs/."""
    assert (DOCS / "DOC_AUTHORING.md").is_file()


def test_doc_inventory_exists() -> None:
    """DOC_INVENTORY.md must exist in docs/."""
    assert (DOCS / "DOC_INVENTORY.md").is_file()


def test_doc_authoring_references_github_docs_style_guide() -> None:
    """DOC_AUTHORING.md must identify the GitHub Docs style guide as the prose baseline."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "GitHub Docs style guide" in content
    assert "docs.github.com" in content


def test_doc_authoring_separates_machine_and_human_checks() -> None:
    """DOC_AUTHORING.md must distinguish machine-checkable and human-review requirements."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "Machine-checkable requirements" in content
    assert "Human editorial review" in content


def test_doc_authoring_covers_implemented_versus_planned() -> None:
    """DOC_AUTHORING.md must define how to mark implemented vs. planned behavior."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "Implemented versus planned behavior" in content


def test_doc_authoring_defines_path_and_basename_rules() -> None:
    """DOC_AUTHORING.md must document path and basename rules."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "Path and basename rules" in content


def test_doc_authoring_defines_history_policy() -> None:
    """DOC_AUTHORING.md must document the history and link preservation policy."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "History and link preservation policy" in content


def test_doc_authoring_defines_search_discoverability_rules() -> None:
    """DOC_AUTHORING.md must document search and discoverability rules."""
    content = (DOCS / "DOC_AUTHORING.md").read_text(encoding="utf-8")
    assert "Search and discoverability rules" in content


def test_doc_inventory_covers_all_tracked_markdown_files() -> None:
    """DOC_INVENTORY.md must reference every tracked Markdown file under docs/.

    Files that are Wiki control files or release artifacts are excluded from the
    per-page search-metadata requirement but must still appear in the inventory.
    """
    inventory = (DOCS / "DOC_INVENTORY.md").read_text(encoding="utf-8")
    excluded_basenames = {
        # Wiki/Pages control files that do not have per-page metadata entries
        "_Sidebar.md",
        "_config.yml",
    }
    markdown_files = [
        path
        for path in DOCS.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    ]
    for md_file in markdown_files:
        basename = md_file.name
        if basename in excluded_basenames:
            continue
        relative = md_file.relative_to(DOCS).as_posix()
        assert relative in inventory or basename in inventory, (
            f"Markdown file not found in DOC_INVENTORY.md: {relative}"
        )


def test_sidebar_has_four_mode_sections() -> None:
    """_Sidebar.md must contain sections for each of the four Diátaxis modes."""
    sidebar = (DOCS / "_Sidebar.md").read_text(encoding="utf-8")
    for section in (
        "How-to guides",
        "Reference",
        "Explanation",
    ):
        assert section in sidebar, f"Sidebar is missing section: {section!r}"


def test_sidebar_has_supporting_navigation_sections() -> None:
    """_Sidebar.md must have sections for infrastructure/authoring and ADRs."""
    sidebar = (DOCS / "_Sidebar.md").read_text(encoding="utf-8")
    assert "Infrastructure" in sidebar or "Authoring" in sidebar, (
        "Sidebar must have an Infrastructure or Authoring section"
    )
    assert "Architecture Decision Records" in sidebar or "ADR" in sidebar, (
        "Sidebar must have an ADR section"
    )


def test_sidebar_links_only_to_existing_pages() -> None:
    """Every relative Markdown link in _Sidebar.md must resolve to an existing file."""
    import re

    sidebar_text = (DOCS / "_Sidebar.md").read_text(encoding="utf-8")
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    missing: list[str] = []
    for match in pattern.finditer(sidebar_text):
        target = match.group(2)
        if "://" in target or target.startswith("#") or target.startswith("mailto:"):
            continue
        # Strip anchor
        target_path = target.split("#", 1)[0]
        if not target_path:
            continue
        resolved = DOCS / target_path
        if not resolved.exists():
            missing.append(target)

    assert not missing, f"Sidebar links to non-existent files: {missing}"


def test_home_has_four_mode_sections() -> None:
    """Home.md must contain sections for each of the four Diátaxis modes."""
    home = (DOCS / "Home.md").read_text(encoding="utf-8")
    for section in (
        "How-to guides",
        "Reference",
        "Explanation",
    ):
        assert section in home, f"Home.md is missing section: {section!r}"


def test_home_does_not_link_to_nonexistent_pages() -> None:
    """Home.md must not link to pages that do not exist."""
    import re

    home_text = (DOCS / "Home.md").read_text(encoding="utf-8")
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    missing: list[str] = []
    for match in pattern.finditer(home_text):
        target = match.group(2)
        if "://" in target or target.startswith("#") or target.startswith("mailto:"):
            continue
        target_path = target.split("#", 1)[0]
        if not target_path:
            continue
        resolved = DOCS / target_path
        if not resolved.exists():
            missing.append(target)

    assert not missing, f"Home.md links to non-existent files: {missing}"
