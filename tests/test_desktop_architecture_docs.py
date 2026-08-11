"""Contract tests for the desktop architecture and secure-development gate."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_ADR = _ROOT / "docs" / "ADR-0025-electron-fastapi-desktop.md"
_DEPLOYMENT_ADR = _ROOT / "docs" / "ADR-0026-local-first-container-remote-deployment.md"
_THREAT_MODEL = _ROOT / "docs" / "THREAT_MODEL.md"
_ARCHITECTURE = _ROOT / "ARCHITECTURE.md"
_PRIVACY = _ROOT / "docs" / "PRIVACY_AND_CONSENT.md"
_RELEASING = _ROOT / "docs" / "RELEASING.md"
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_SIDEBAR = _ROOT / "docs" / "_Sidebar.md"
_FILE_INGRESS = _ROOT / "docs" / "FILE_INGRESS.md"
_DESKTOP_README = _ROOT / "desktop" / "README.md"
_DESKTOP_SHELL = _ROOT / "docs" / "DESKTOP_SHELL.md"


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
        "TM-M01",
        "TM-H01",
        "TM-K01",
        "TM-N01",
        "TM-V01",
        "TM-G01",
        "TM-X01",
        "TM-B01",
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

    assert set(rows) == {f"AB-{number:02}" for number in range(1, 23)}
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
            r"^\|\s*`(?P<id>STR-[RAFLUMHKNVGXB]-[STRIDE])`\s*(?P<row>\|.*)$",
            text,
            flags=re.MULTILINE,
        )
    }
    expected = {
        f"STR-{boundary}-{category}"
        for boundary in ("R", "A", "F", "L", "U")
        for category in "STRIDE"
    }
    expected.update(
        {
            "STR-M-T",
            "STR-M-R",
            "STR-M-E",
            "STR-H-S",
            "STR-H-T",
            "STR-H-I",
            "STR-H-D",
            "STR-H-E",
            "STR-K-E",
            "STR-K-D",
            "STR-N-S",
            "STR-N-T",
            "STR-N-I",
            "STR-V-T",
            "STR-V-R",
            "STR-V-I",
            "STR-V-D",
            "STR-G-S",
            "STR-G-T",
            "STR-G-D",
            "STR-G-E",
            "STR-X-S",
            "STR-X-I",
            "STR-B-S",
            "STR-B-T",
            "STR-B-R",
            "STR-B-D",
        }
    )

    assert set(rows) == expected
    for threat, row in rows.items():
        assert "TM-" in row, threat
        assert "#" in row, threat
        assert "G" in row, threat
        assert "Negative:" in row, threat


def test_deployment_adr_records_fail_closed_profile_contracts() -> None:
    adr = _read(_DEPLOYMENT_ADR)
    architecture = _read(_ARCHITECTURE)
    privacy = _read(_PRIVACY)
    threat_model = _read(_THREAT_MODEL)
    normalized_adr = " ".join(adr.split())

    for text in (adr, architecture, privacy):
        normalized = " ".join(text.split())
        assert "provider=none" in normalized
        assert "incompatible with Connect Remote and Host Remote" in normalized
        assert "separate hosts, secrets, volumes, and identity realms" in normalized

    for phrase in (
        "one authorized household principal",
        "every other OIDC subject",
        "no-swap memory",
        "allowlisted read-only `family_trees` mount",
    ):
        assert phrase in normalized_adr

    for text in (adr, architecture, threat_model):
        normalized = " ".join(text.split())
        assert "socket-free native application-service path" in normalized
        assert "does not start the container backend" in normalized

    normalized_threat_model = " ".join(threat_model.split())
    assert "allowlisted read-only `family_trees` mount" in normalized_threat_model
    assert 'RemoteRenderer -->|"fixed typed bridge"| RemoteMain' in threat_model
    assert 'RemoteMain -->|"HTTPS after enrollment"| Internet' in threat_model
    assert "RemoteRenderer --> Internet" not in threat_model
    assert "Gateway <--> Volume" not in threat_model

    for budget in (
        "CPU quota",
        "PID ceiling",
        "Writable storage",
        "Inode ceiling",
        "Log retention",
        "Concurrent connections",
        "API workers",
        "Concurrent jobs",
        "Non-file request body",
    ):
        assert f"| {budget} |" in adr


def test_deployment_release_gates_are_profile_specific() -> None:
    releasing = _read(_RELEASING)
    architecture = _read(_ARCHITECTURE)

    for profile in ("Local Desktop containers", "Connect Remote", "Host Remote"):
        assert f"| {profile} |" in releasing

    for exclusion in (
        "Remote edge, identity, and host-operations evidence from `G6` is not applicable.",
        "Local Docker/runtime evidence from `G5` and Host Remote operations are not applicable.",
        "Local Desktop supervisor evidence is not applicable.",
    ):
        assert exclusion in releasing

    for mapping in (
        "Local Desktop containers require `G0`, `G5`, and their applicable `G7` evidence",
        "Connect Remote requires `G0`, its applicable client-side `G6`, and `G7` evidence",
        "Host Remote requires `G0`, `G6`, and its applicable `G7` evidence",
    ):
        assert mapping in architecture


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


def test_issue_103_opaque_file_grant_boundary_is_documented() -> None:
    architecture = _read(_ARCHITECTURE)
    desktop_readme = _read(_DESKTOP_README)
    desktop_shell = _read(_DESKTOP_SHELL)
    file_ingress = _read(_FILE_INGRESS)
    threat_model = _read(_THREAT_MODEL)
    contributing = _read(_CONTRIBUTING)
    documents = (
        architecture,
        desktop_readme,
        desktop_shell,
        file_ingress,
        threat_model,
        contributing,
    )
    combined = "\n".join(documents)

    for interface in (
        "requestOpenFileGrant",
        "requestSaveFileGrant",
        "revokeFileGrant",
        "resolveReadGrant",
        "resolveWriteGrant",
    ):
        assert f"`{interface}`" in combined

    for text in documents:
        assert "Issue #103" in text

    for phrase in ("path-free", "single-use", "native", "revocation"):
        assert phrase in combined

    normalized_file_ingress = " ".join(file_ingress.casefold().split())
    assert "renderer cannot invoke either resolver" in normalized_file_ingress
    assert "raw host path" in normalized_file_ingress
    assert "#114/#118/#131" in threat_model
