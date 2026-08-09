"""Contract tests for the desktop architecture and secure-development gate."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_ADR = _ROOT / "docs" / "ADR-0025-electron-fastapi-desktop.md"
_THREAT_MODEL = _ROOT / "docs" / "THREAT_MODEL.md"
_ARCHITECTURE = _ROOT / "ARCHITECTURE.md"
_PRIVACY = _ROOT / "docs" / "PRIVACY_AND_CONSENT.md"
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_SIDEBAR = _ROOT / "docs" / "_Sidebar.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_adr_ratifies_processes_contracts_and_scope() -> None:
    text = _read(_ADR)
    normalized = " ".join(text.split())

    assert "- **Status:** Accepted" in text
    assert "Issue #98" in text
    for boundary in (
        "Sandboxed renderer",
        "Preload bridge",
        "Electron main",
        "FastAPI sidecar",
        "Application services",
        "Bounded workers",
    ):
        assert boundary in text

    for contract in (
        "ApiVersion",
        "CapabilityManifest",
        "ErrorEnvelope",
        "FileGrant",
        "EventEnvelope",
        "SecretMutation",
        "PluginPackageManifest",
        "UpdateManifest",
    ):
        assert f"`{contract}`" in text

    assert "/api/v1" in text
    assert "MVP navigation" in text
    assert "Post-MVP" in text
    assert "no in-app authentication" in text
    assert "accessible degraded diagnostics" in normalized


def test_desktop_adr_records_backlog_disposition_and_exclusive_ownership() -> None:
    text = _read(_ADR)
    normalized = " ".join(text.casefold().split())

    for issue_number in (11, 16, 18, 19, 39, 41, 43, 45, 52, 60):
        assert f"#{issue_number}" in text

    for owner in (
        "`desktop/`",
        "`src/ancestryllm/api/`",
        "`pyproject.toml` and `uv.lock`",
        "GitHub workflows",
        "architecture and security documents",
    ):
        assert owner.casefold() in normalized

    assert "Do not create" in text
    assert "public API" in text
    assert "RootsMagic" in text
    assert "immutable" in text


def test_desktop_threat_model_maps_controls_to_owasp_nist_and_evidence() -> None:
    text = _read(_THREAT_MODEL)

    assert "OWASP Top 10:2025" in text
    for category in range(1, 11):
        assert f"A{category:02}:2025" in text

    assert "NIST SP 800-218" in text
    for practice_group in ("PO", "PS", "PW", "RV"):
        assert re.search(rf"\|\s*`{practice_group}`\s*\|", text)

    for control in (
        "TM-R01",
        "TM-R02",
        "TM-I01",
        "TM-A01",
        "TM-A02",
        "TM-A03",
        "TM-S01",
        "TM-F01",
        "TM-F02",
        "TM-L01",
        "TM-L02",
        "TM-D01",
        "TM-P01",
        "TM-P02",
        "TM-U01",
        "TM-U02",
        "TM-E01",
        "TM-C01",
        "TM-O01",
        "TM-O02",
    ):
        assert f"`{control}`" in text

    assert "inherent risk" in text
    assert "evidence-backed residual risk" in text
    assert "90 days" in text
    assert "Expired exceptions fail" in text


def test_every_desktop_abuse_case_has_owner_gate_and_negative_test() -> None:
    text = _read(_THREAT_MODEL)
    rows = {
        match.group("id"): match.group("row")
        for match in re.finditer(
            r"^\|\s*`(?P<id>AB-\d{2})`\s*(?P<row>\|.*)$",
            text,
            flags=re.MULTILINE,
        )
    }

    assert set(rows) == {f"AB-{number:02}" for number in range(1, 22)}
    for abuse_case, row in rows.items():
        assert "TM-" in row, abuse_case
        assert "#" in row, abuse_case
        assert "G" in row, abuse_case
        assert "Negative:" in row, abuse_case


def test_every_stride_boundary_threat_has_control_owner_gate_and_test() -> None:
    text = _read(_THREAT_MODEL)
    rows = {
        match.group("id"): match.group("row")
        for match in re.finditer(
            r"^\|\s*`(?P<id>STR-[RAFLU]-[STRIDE])`\s*(?P<row>\|.*)$",
            text,
            flags=re.MULTILINE,
        )
    }
    expected = {
        f"STR-{boundary}-{category}"
        for boundary in ("R", "A", "F", "L", "U")
        for category in "STRIDE"
    }

    assert set(rows) == expected
    for threat, row in rows.items():
        assert "TM-" in row, threat
        assert "#" in row, threat
        assert "G" in row, threat
        assert "Negative:" in row, threat


def test_desktop_policy_is_aligned_across_normative_guidance() -> None:
    architecture = _read(_ARCHITECTURE)
    privacy = _read(_PRIVACY)
    contributing = _read(_CONTRIBUTING)
    sidebar = _read(_SIDEBAR)

    for text in (architecture, privacy, contributing):
        assert "ADR-0025-electron-fastapi-desktop.md" in text
        assert "OWASP Top 10:2025" in text
        assert "NIST SP 800-218" in text

    for text in (architecture, privacy):
        assert "renderer" in text
        assert "untrusted" in text
        assert "provider=none" in text

    assert "ADR-0025-electron-fastapi-desktop.md" in sidebar
