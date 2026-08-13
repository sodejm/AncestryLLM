"""Contract checks for the canonical documentation architecture."""

import json
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIRECTORY = REPOSITORY_ROOT / "docs"
AUTHORING_GUIDE = DOCS_DIRECTORY / "DOCS_AUTHORING.md"
PAGE_METADATA = DOCS_DIRECTORY / "_data" / "page_metadata.json"

MIGRATED_READER_DOCS = {
    "APPLICATION_CONTRACTS.md": "reference/APPLICATION_CONTRACTS.md",
    "ARCHITECTURE_CONTRACTS.md": "reference/ARCHITECTURE_CONTRACTS.md",
    "CI.md": "reference/CI.md",
    "CLI.md": "reference/CLI.md",
    "COMMAND_EXECUTOR.md": "reference/COMMAND_EXECUTOR.md",
    "DEPENDENCY_MAINTENANCE.md": "reference/DEPENDENCY_MAINTENANCE.md",
    "DESKTOP_SHELL.md": "explanation/DESKTOP_SHELL.md",
    "DESKTOP_SIDECAR.md": "reference/DESKTOP_SIDECAR.md",
    "FILE_INGRESS.md": "reference/FILE_INGRESS.md",
    "GEDCOM_COMPATIBILITY.md": "reference/GEDCOM_COMPATIBILITY.md",
    "LOCAL_LLM_BENCHMARKS.md": "reference/LOCAL_LLM_BENCHMARKS.md",
    "LOCAL_RETRIEVAL_EVALUATION.md": "reference/LOCAL_RETRIEVAL_EVALUATION.md",
    "MODULE_AUTHORING.md": "reference/MODULE_AUTHORING.md",
    "PRIVACY_AND_CONSENT.md": "explanation/PRIVACY_AND_CONSENT.md",
    "PROVIDERS.md": "reference/PROVIDERS.md",
    "REPL_ARCHITECTURE.md": "explanation/REPL_ARCHITECTURE.md",
    "RUFF_EXPANSION_EVALUATION.md": "reference/RUFF_EXPANSION_EVALUATION.md",
    "TY_ADVISORY_EVALUATION.md": "reference/TY_ADVISORY_EVALUATION.md",
    "UV_BUILD_EVALUATION.md": "reference/UV_BUILD_EVALUATION.md",
    "VERSIONING.md": "reference/VERSIONING.md",
    "api/API_REFERENCE.md": "reference/api/API_REFERENCE.md",
}


def _inventory_rows() -> list[list[str]]:
    """Return the complete rows in the canonical migration inventory."""
    inventory_section = (
        AUTHORING_GUIDE.read_text(encoding="utf-8")
        .split("## Complete migration inventory", maxsplit=1)[1]
        .split("## ", maxsplit=1)[0]
    )
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in inventory_section.splitlines()
        if line.startswith("| `")
    ]


def _tracked_markdown_pages() -> set[str]:
    """Return tracked Markdown source pages relative to ``docs/``."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs"],
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
    )
    return {
        path.decode("utf-8").removeprefix("docs/")
        for path in result.stdout.split(b"\0")
        if path.endswith(b".md")
    }


def test_inventory_covers_every_canonical_markdown_page() -> None:
    """Every currently published source page has a migration decision."""
    authoring_guide = AUTHORING_GUIDE.read_text(encoding="utf-8")

    assert "| Current path | Primary Diataxis type | Intended path | Action |" in authoring_guide
    assert "| Audience | Implementation status | Related owner |" in authoring_guide
    assert (
        "| Primary search intent | Likely queries | Search-facing title | Description |"
        in authoring_guide
    )
    assert "| Discoverable-URL disposition |" in authoring_guide

    inventory_rows = _inventory_rows()
    assert inventory_rows
    assert all(len(row) == 12 for row in inventory_rows)
    assert all(all(cell for cell in row) for row in inventory_rows)

    tracked_pages = _tracked_markdown_pages()
    inventory_pages = {row[0].strip("`") for row in inventory_rows}

    assert inventory_pages == tracked_pages, (
        f"Inventory omissions: {sorted(tracked_pages - inventory_pages)}; "
        f"unexpected entries: {sorted(inventory_pages - tracked_pages)}"
    )


def test_reference_and_explanation_inventory_is_fully_migrated() -> None:
    """Issue #261 moves every assigned reader page and finalizes its disposition."""
    inventory = {row[0].strip("`"): row for row in _inventory_rows()}
    migrated_paths = set(MIGRATED_READER_DOCS.values())
    reader_rows = {
        path: row for path, row in inventory.items() if row[1] in {"Reference", "Explanation"}
    }

    assert set(reader_rows) == migrated_paths
    assert (DOCS_DIRECTORY / "api" / "openapi-v1.json").is_file()

    for legacy_path, migrated_path in MIGRATED_READER_DOCS.items():
        assert not (DOCS_DIRECTORY / legacy_path).exists()
        assert (DOCS_DIRECTORY / migrated_path).is_file()
        row = inventory[migrated_path]
        assert row[2] == f"`docs/{migrated_path}`"
        assert row[3] == "Moved in #261 with git mv"
        assert "Wiki basename retained" in row[11]
        assert "Pages route moved" in row[11]


def test_navigation_separates_reader_modes_from_supporting_material() -> None:
    """Landing navigation exposes reader modes and the bounded desktop scope."""
    for page in ("Home.md", "_Sidebar.md"):
        navigation = (DOCS_DIRECTORY / page).read_text(encoding="utf-8")
        for heading in (
            "Tutorial",
            "How-to",
            "Reference",
            "Explanation",
            "Supporting",
        ):
            assert heading in navigation

    home = (DOCS_DIRECTORY / "Home.md").read_text(encoding="utf-8")
    home_text = " ".join(home.split())
    assert "released bounded Electron desktop control shell" in home
    assert "Home, Diagnostics, Settings, and capability onboarding" in home_text
    assert "The CLI and REPL use the same command specification" in home
    assert "All surfaces use the same command specification" not in home
    assert "Desktop-domain capabilities" in home_text
    assert "presentation-only **Tasks** adapters" in home_text
    assert (
        "genealogy/domain task admission or execution, direct artifact access, provider "
        "execution, cloud accounts, and updater flows" in home_text
    )
    for page in ("Home.md", "_Sidebar.md"):
        navigation = (DOCS_DIRECTORY / page).read_text(encoding="utf-8")
        assert (
            "[Desktop shell (released bounded v0.5.0 plus marked Unreleased source)]"
            "(explanation/DESKTOP_SHELL.md)" in navigation
        )
        assert (
            "[Desktop verification (released bounded shell and later changes)](DESKTOP_VERIFICATION.md)"
            in navigation
        )
        assert (
            "[Desktop deployment (released bounded shell publication)](DEPLOYMENT.md)" in navigation
        )
        how_to_start = navigation.index("How-to")
        reference_start = navigation.index("Reference")
        module_authoring_link = navigation.index("(reference/MODULE_AUTHORING.md)")
        application_contracts_link = navigation.index("(reference/APPLICATION_CONTRACTS.md)")
        explanation_start = navigation.index("Explanation")
        provider_link = navigation.index("(reference/PROVIDERS.md)")
        assert not how_to_start < provider_link < reference_start
        assert reference_start < module_authoring_link < explanation_start
        assert reference_start < application_contracts_link < explanation_start


def test_architecture_authority_and_migration_controls_are_explicit() -> None:
    """Keep root authority out of staged docs and make migrations explicit."""
    authoring_guide = AUTHORING_GUIDE.read_text(encoding="utf-8")

    assert "repository-root `ARCHITECTURE.md` and the ADRs remain authoritative" in authoring_guide
    assert "`ARCHITECTURE.md` is not staged" in authoring_guide
    assert "published from `docs/`" in authoring_guide
    assert "../ARCHITECTURE.md" in authoring_guide
    assert "#257 source-aware rewrite" in authoring_guide
    assert "A unique basename alone is not sufficient" in authoring_guide
    assert "`reference/PROVIDERS.md` | Reference | `docs/reference/PROVIDERS.md`" in authoring_guide
    assert (
        "`reference/APPLICATION_CONTRACTS.md` | Reference | "
        "`docs/reference/APPLICATION_CONTRACTS.md`" in authoring_guide
    )
    assert (
        "`reference/MODULE_AUTHORING.md` | Reference | `docs/reference/MODULE_AUTHORING.md`"
        in authoring_guide
    )
    assert (
        "Released bounded 0.5.0 shell; clearly marked Unreleased Tasks presentation"
        in authoring_guide
    )
    assert (
        "Implemented verification gate for released bounded shell; later domain work planned"
        in authoring_guide
    )
    assert (
        "`explanation/DESKTOP_SHELL.md` | Explanation | "
        "`docs/explanation/DESKTOP_SHELL.md`" in authoring_guide
    )
    assert "Historical release | Release notes | Find version 0.5.0 changes" in authoring_guide


def test_pages_metadata_describes_the_landing_and_authoring_pages() -> None:
    """Pages-only metadata keeps the two descriptive navigation entries in sync."""
    metadata = json.loads(PAGE_METADATA.read_text(encoding="utf-8"))

    public_entries = {path: entry for path, entry in metadata.items() if not path.startswith("_")}
    assert set(public_entries) == _tracked_markdown_pages() - {"_Sidebar.md"}

    for field in ("title", "description"):
        values = [entry[field].strip() for entry in public_entries.values()]
        assert all(values)
        assert len(values) == len({value.casefold() for value in values})

    home_description = metadata["Home.md"]["description"].lower()
    assert "cli and repl" in home_description
    assert "released bounded desktop control shell" in home_description
    assert "planned" in home_description
    assert "Diátaxis" in metadata["DOCS_AUTHORING.md"]["description"]
    authoring_h1 = AUTHORING_GUIDE.read_text(encoding="utf-8").splitlines()[0].removeprefix("# ")
    assert metadata["DOCS_AUTHORING.md"]["title"] == f"{authoring_h1} — AncestryLLM"
    shell_description = metadata["explanation/DESKTOP_SHELL.md"]["description"].lower()
    assert "released" in shell_description
    assert "home, diagnostics, settings, and capability onboarding" in shell_description
    assert "excluded" in shell_description
    sidecar_description = metadata["reference/DESKTOP_SIDECAR.md"]["description"].lower()
    assert "released" in sidecar_description
    assert "excludes" in sidecar_description
    deployment_description = metadata["DEPLOYMENT.md"]["description"].lower()
    assert "released bounded" in deployment_description
    assert "not a hosted application" in deployment_description
    verification_description = metadata["DESKTOP_VERIFICATION.md"]["description"].lower()
    assert "implemented" in verification_description
    assert "released bounded desktop shell" in verification_description
    assert "unreleased" not in verification_description
    assert "release approval" in verification_description
    release_notes_description = metadata["release-notes/0.5.0.md"]["description"].lower()
    assert "released" in release_notes_description
    assert "unreleased" not in release_notes_description
    assert "_Sidebar.md" not in metadata
