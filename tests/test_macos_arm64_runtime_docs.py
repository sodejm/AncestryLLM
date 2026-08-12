"""Documentation contracts for the macOS arm64 local-runtime bootstrap."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[1]
_ARCHITECTURE = _ROOT / "ARCHITECTURE.md"
_README = _ROOT / "README.md"
_DESKTOP_README = _ROOT / "desktop" / "README.md"
_DESKTOP_SHELL = _ROOT / "docs" / "explanation" / "DESKTOP_SHELL.md"
_THREAT_MODEL = _ROOT / "docs" / "THREAT_MODEL.md"
_RELEASE_NOTES = _ROOT / "docs" / "release-notes" / "0.6.0.md"
_ADR_0026 = _ROOT / "docs" / "ADR-0026-local-first-container-remote-deployment.md"
_DEPLOYMENT = _ROOT / "docs" / "DEPLOYMENT.md"
_RELEASING = _ROOT / "docs" / "RELEASING.md"
_SETUP_DIAGNOSTICS = _ROOT / "docs" / "SETUP_DIAGNOSTICS.md"
_DOCS_AUTHORING = _ROOT / "docs" / "DOCS_AUTHORING.md"
_HOME = _ROOT / "docs" / "Home.md"
_PAGE_METADATA = _ROOT / "docs" / "_data" / "page_metadata.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def test_issue_348_boundary_is_consistent_across_affected_documentation() -> None:
    paths = (
        _ARCHITECTURE,
        _README,
        _DESKTOP_README,
        _DESKTOP_SHELL,
        _THREAT_MODEL,
        _RELEASE_NOTES,
        _ADR_0026,
        _DEPLOYMENT,
        _RELEASING,
        _SETUP_DIAGNOSTICS,
        _DOCS_AUTHORING,
        _HOME,
        _PAGE_METADATA,
    )

    for path in paths:
        assert "macOS arm64" in _read(path), path

    for path in (
        _ARCHITECTURE,
        _README,
        _DESKTOP_README,
        _DESKTOP_SHELL,
        _THREAT_MODEL,
        _RELEASE_NOTES,
    ):
        assert "Issue #348" in _read(path), path

    combined = "\n".join(_read(path) for path in paths)
    assert "unwired #363" not in combined
    assert "until #348 runtime integration passes" not in combined
    assert "runtime acquisition and selection" not in combined

    architecture = _normalized(_ARCHITECTURE)
    assert "macos-arm64-runtime-policy-v1.json" in architecture
    assert "`ancestryllm-local-arm64`" in architecture
    assert "`colima-ancestryllm-local-arm64`" in architecture
    assert "Docker Desktop remains optional" in architecture
    assert "does not start an application container" in architecture
    assert "renderer receives no Docker socket" in architecture

    threat_model = _normalized(_THREAT_MODEL)
    for control in ("`TM-H01`", "`TM-K01`", "`TM-N01`", "`TM-V01`"):
        assert control in threat_model
    assert (
        "downloaded bytes cannot execute before both size and digest verification" in threat_model
    )
    assert "administrator privileges" in threat_model
    assert "ambient Docker context" in threat_model
    assert "sanitized local-runtime diagnostics" in threat_model


def test_operator_guide_documents_review_apply_recovery_and_removal() -> None:
    text = _normalized(_DESKTOP_SHELL)

    for command in (
        "AncestryLLM --local-runtime status",
        "AncestryLLM --local-runtime preview setup --offline",
        "AncestryLLM --local-runtime apply setup --offline --plan-revision",
    ):
        assert command in text

    for operation in (
        "`setup`",
        "`start`",
        "`stop`",
        "`repair`",
        "`uninstall-preserve`",
        "`uninstall-delete`",
    ):
        assert operation in text

    for phrase in (
        "`SET UP LOCAL RUNTIME`",
        "`START LOCAL RUNTIME`",
        "`STOP LOCAL RUNTIME`",
        "`REPAIR LOCAL RUNTIME`",
        "`REMOVE LOCAL RUNTIME`",
        "`DELETE LOCAL RUNTIME DATA`",
    ):
        assert phrase in text

    for contract in (
        "Apple silicon",
        "macOS 13",
        "24 GiB",
        "hardware virtualization",
        "does not request administrator privileges",
        "Docker Desktop is optional",
        "SHA-256",
        "license",
        "`.part`",
        "one JSON line",
        "exit code 0",
        "exit code 1",
        "exit code 2",
        "preserve app data",
        "delete app data",
    ):
        assert contract in text


def test_runtime_policy_update_procedure_is_fail_closed() -> None:
    text = _normalized(_DESKTOP_SHELL)

    assert "macos-arm64-runtime-policy-v1.json" in text
    assert "no implicit latest version" in text
    assert "no mirror fallback" in text
    for reviewed_field in (
        "version",
        "repository",
        "asset name",
        "source URL",
        "byte size",
        "SHA-256",
        "license identity",
        "license digest",
    ):
        assert reviewed_field in text
