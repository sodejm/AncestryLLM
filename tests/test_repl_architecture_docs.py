"""Documentation contracts for the implemented REPL compatibility boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPL_ARCHITECTURE = ROOT / "docs" / "explanation" / "REPL_ARCHITECTURE.md"


def _text() -> str:
    return REPL_ARCHITECTURE.read_text(encoding="utf-8")


def test_repl_architecture_records_the_shared_implemented_execution_path() -> None:
    text = _text()

    assert "**Status:** Implemented" in text
    assert "`input -> routing -> execution ->\nservices`" in text
    assert "same transport-neutral `CommandInvocation`" in text
    assert "same\nimmutable `CommandExecutor` registry" in text
    assert "The REPL does not import the CLI" in text
    assert "The one-shot CLI and REPL are sibling adapters" in text


def test_repl_architecture_records_cli_compatibility_and_exit_semantics() -> None:
    text = _text()

    for contract in (
        "One-shot CLI",
        "Interactive REPL",
        "AncestryError.exit_code",
        "exit code `0`",
        "exit code `2`",
        "stable error contract",
        "same plain service result",
        "no per-command process exit status",
    ):
        assert contract in text


def test_repl_architecture_records_security_and_future_scope_boundaries() -> None:
    text = _text()

    for contract in (
        "Shell/Python execution",
        "keyring contents",
        "owner-only permissions",
        "Provider selection and consent stay explicit",
        "`provider=none` remains\n  network-free",
        "RootsMagic",
        "rooted, loss-minimal",
        "GEDCOM atomic publication",
        "No API, WebUI, multi-user server, autonomous agent",
    ):
        assert contract in text
