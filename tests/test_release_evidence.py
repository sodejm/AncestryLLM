from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "create_release_evidence.py"
_SPEC = importlib.util.spec_from_file_location("create_release_evidence", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
evidence = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = evidence
_SPEC.loader.exec_module(evidence)


def _findings() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release": "0.2.0",
        "reviewed_at": "2026-07-24",
        "reviewed_by": "release-owner",
        "inventory_complete": True,
        "sources": sorted(evidence.FINDING_SOURCES),
        "findings": [],
    }


def _gates() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release": "0.2.0",
        "commit": "a" * 40,
        "run_url": "https://example.test/readiness-run",
        "gates": [
            {
                "name": name,
                "status": "verified",
                "evidence_url": f"https://example.test/readiness-run#{name}",
            }
            for name in sorted(evidence.REQUIRED_GATES)
        ],
    }


def _interoperability() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release": "0.2.0",
        "reviewed_at": "2026-07-24",
        "reviewed_by": "release-owner",
        "vendors": [
            {
                "vendor": "Ancestry",
                "status": "verified",
                "assessed_at": "2026-07-24",
                "application_version": "1.2.3",
                "fictional_data": True,
                "fixture_ids": ["fictional-minimal-555"],
                "evidence_urls": ["https://example.test/import-evidence"],
                "notes": "The fictional fixture imported successfully.",
            },
            *[
                {
                    "vendor": vendor,
                    "status": "unverified",
                    "assessed_at": "2026-07-24",
                    "fictional_data": False,
                    "fixture_ids": [],
                    "evidence_urls": ["https://example.test/import-evidence"],
                    "notes": "No dated fictional-data manual import evidence is recorded.",
                }
                for vendor in sorted(evidence.REQUIRED_INTEROPERABILITY_VENDORS - {"Ancestry"})
            ],
        ],
    }


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0", "0.4.0"])
def test_versioned_release_evidence_records_are_valid(version: str) -> None:
    root = Path(__file__).parents[1] / "docs" / "release-evidence" / version
    findings = json.loads((root / "findings.json").read_text(encoding="utf-8"))
    interoperability = json.loads((root / "interoperability.json").read_text(encoding="utf-8"))

    assert evidence._validate_findings(findings, version) == []
    vendors = evidence._validate_interoperability(interoperability, version)
    assert {vendor["status"] for vendor in vendors} == {"unverified"}


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0", "0.4.0"])
def test_curated_release_notes_distinguish_unverified_interoperability(
    version: str,
) -> None:
    notes = (Path(__file__).parents[1] / "docs" / "release-notes" / f"{version}.md").read_text(
        encoding="utf-8"
    )
    normalized_notes = " ".join(notes.split())

    assert "Interoperability limitations" in notes
    assert "`unverified`" in notes
    assert "does not make a positive compatibility claim" in normalized_notes
    assert "SHA256SUMS" in notes


def test_finding_dispositions_require_owner_expiry_and_evidence() -> None:
    payload = _findings()
    payload["findings"] = [
        {
            "id": "SEC-1",
            "source": "semgrep",
            "severity": "high",
            "disposition": "accepted-risk",
            "expires": "2026-08-01",
            "evidence_url": "https://example.test/SEC-1",
            "rationale": "A fictional exception for validation.",
        }
    ]

    with pytest.raises(ValueError, match="owner"):
        evidence._validate_findings(
            payload,
            "0.2.0",
            as_of=date(2026, 8, 1),
        )


def test_finding_dispositions_must_not_be_expired_at_readiness() -> None:
    payload = _findings()
    payload["findings"] = [
        {
            "id": "SEC-EXPIRED",
            "source": "semgrep",
            "severity": "medium",
            "disposition": "accepted-risk",
            "owner": "release-owner",
            "expires": "2026-07-24",
            "evidence_url": "https://example.test/SEC-EXPIRED",
            "rationale": "A fictional expired exception.",
        }
    ]

    with pytest.raises(ValueError, match=r"expires predates.*2026-07-25"):
        evidence._validate_findings(
            payload,
            "0.2.0",
            as_of=date(2026, 7, 25),
        )

    payload["findings"][0]["expires"] = "2026-07-25"
    assert (
        evidence._validate_findings(
            payload,
            "0.2.0",
            as_of=date(2026, 7, 25),
        )[0]["expires"]
        == "2026-07-25"
    )


def test_verified_interoperability_requires_fictional_dated_evidence() -> None:
    payload = _interoperability()
    payload["vendors"][0]["fictional_data"] = False

    with pytest.raises(ValueError, match="fictional_data"):
        evidence._validate_interoperability(payload, "0.2.0")


def test_interoperability_requires_every_supported_importer() -> None:
    payload = _interoperability()
    payload["vendors"] = [
        vendor for vendor in payload["vendors"] if vendor["vendor"] != "MyHeritage"
    ]

    with pytest.raises(ValueError, match=r"missing supported importers.*MyHeritage"):
        evidence._validate_interoperability(payload, "0.2.0")


def test_gate_inventory_requires_exact_commit_every_gate_and_verified_status() -> None:
    payload = _gates()
    payload["gates"].pop()

    with pytest.raises(ValueError, match="required inventory"):
        evidence._validate_gates(payload, "0.2.0", "a" * 40)

    with pytest.raises(ValueError, match="commit"):
        evidence._validate_gates(_gates(), "0.2.0", "b" * 40)

    payload = _gates()
    payload["gates"][0]["status"] = "unverified"
    with pytest.raises(ValueError, match="status must be verified"):
        evidence._validate_gates(payload, "0.2.0", "a" * 40)


def test_generator_embeds_dispositions_interoperability_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "ancestryllm-0.2.0.whl").write_bytes(b"fictional wheel")
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps(_findings()), encoding="utf-8")
    gates_path = tmp_path / "gates.json"
    gates_path.write_text(json.dumps(_gates()), encoding="utf-8")
    interoperability_path = tmp_path / "interoperability.json"
    interoperability_path.write_text(json.dumps(_interoperability()), encoding="utf-8")
    output = tmp_path / "release-evidence.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(_SCRIPT),
            "--version",
            "0.2.0",
            "--commit",
            "a" * 40,
            "--run-url",
            "https://example.test/run",
            "--artifacts",
            str(artifacts),
            "--gates",
            str(gates_path),
            "--findings",
            str(findings_path),
            "--interoperability",
            str(interoperability_path),
            "--output",
            str(output),
        ],
    )

    assert evidence.main() == 0
    rendered = output.read_text(encoding="utf-8")
    assert "Security finding dispositions" in rendered
    assert "Ancestry" in rendered
    assert "ancestryllm-0.2.0.whl" in rendered
    assert "tests-and-coverage" in rendered
    assert "https://example.test/readiness-run#tests-and-coverage" in rendered
