from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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


def _readiness_gate_inventory() -> set[str]:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "release-readiness.yml"
    ).read_text(encoding="utf-8")
    gate_array = workflow.split("gates: (", maxsplit=1)[1].split("| map({", maxsplit=1)[0]
    return set(re.findall(r'^\s+"([a-z0-9-]+)",?$', gate_array, flags=re.MULTILINE))


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


def _bootstrap_receipt() -> dict[str, Any]:
    policy_path = Path(__file__).parents[1] / "config" / "uv-bootstrap-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    uv_asset = policy["uv"]["assets"]["macos-arm64"]
    gh_asset = policy["github_cli"]["assets"]["macos-arm64"]
    return {
        "schema_version": 1,
        "policy": {
            "schema_version": policy["schema_version"],
            "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        },
        "tool": {
            "name": "uv",
            "version": policy["uv"]["version"],
            "platform": "macos",
            "architecture": "arm64",
            "asset_name": uv_asset["archive_name"],
            "asset_sha256": uv_asset["sha256"],
            "binary_sha256": uv_asset["binary_sha256"],
        },
        "verifier": {
            "name": "GitHub CLI",
            "version": policy["github_cli"]["version"],
            "archive_name": gh_asset["archive_name"],
            "archive_sha256": gh_asset["sha256"],
        },
        "provenance": {
            "source_repository": policy["uv"]["source_repository"],
            "source_commit": policy["uv"]["source_commit"],
            "source_ref": policy["uv"]["source_ref"],
            "signer_workflow_identity": policy["uv"]["signer_workflow_identity"],
            "oidc_issuer": policy["uv"]["oidc_issuer"],
            "predicate_type": policy["uv"]["predicate_type"],
        },
        "verified_at": "2026-08-09T12:34:56Z",
        "status": "success",
        "failure_category": None,
    }


def _security_dependency_report() -> dict[str, Any]:
    policy = json.loads(evidence.SECURITY_DEPENDENCY_POLICY_PATH.read_text(encoding="utf-8"))
    dependencies = sorted(
        policy["required_dependencies"],
        key=lambda item: (item["blocked"], item["blocked_by"]),
    )
    checked_issues = {item["number"] for item in policy["required_issues"]}
    checked_issues.update(
        number
        for dependency in dependencies
        for number in (dependency["blocked"], dependency["blocked_by"])
    )
    checked_issues.add(policy["evidence_consumer"]["issue"])
    return {
        "schema_version": 1,
        "status": "verified",
        "policy_schema_version": 1,
        "policy_sha256": evidence._canonical_json_sha256(policy),
        "repository": policy["repository"],
        "project": policy["project"],
        "checked_issues": sorted(checked_issues),
        "required_dependencies": dependencies,
        "evidence_consumer": policy["evidence_consumer"],
    }


def _write_security_dependency_report(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "security-dependencies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0", "0.4.0", "0.5.0"])
def test_versioned_release_evidence_records_are_valid(version: str) -> None:
    root = Path(__file__).parents[1] / "docs" / "release-evidence" / version
    findings = json.loads((root / "findings.json").read_text(encoding="utf-8"))
    interoperability = json.loads((root / "interoperability.json").read_text(encoding="utf-8"))

    assert evidence._validate_findings(findings, version) == []
    vendors = evidence._validate_interoperability(interoperability, version)
    assert {vendor["status"] for vendor in vendors} == {"unverified"}


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0", "0.4.0", "0.5.0"])
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


def test_readiness_workflow_and_evidence_generator_share_gate_inventory() -> None:
    assert _readiness_gate_inventory() == evidence.REQUIRED_GATES


def test_bootstrap_receipt_requires_exact_schema_policy_asset_and_identity() -> None:
    receipt = _bootstrap_receipt()

    validated = evidence._validate_bootstrap_receipt(receipt)

    assert validated["tool_version"] == "0.12.1"
    assert validated["platform"] == "macos-arm64"
    assert validated["source_commit"] == "329541a503de8a4d9bb021814f9c0875efe033c8"

    receipt["unexpected"] = "field"
    with pytest.raises(ValueError, match="fields do not match schema v1"):
        evidence._validate_bootstrap_receipt(receipt)


@pytest.mark.parametrize("location", ("receipt", "receipt-policy"))
def test_bootstrap_receipt_rejects_boolean_schema_versions(location: str) -> None:
    receipt = _bootstrap_receipt()
    if location == "receipt":
        receipt["schema_version"] = True
    else:
        receipt["policy"]["schema_version"] = True

    with pytest.raises(ValueError, match="schema_version must be 1"):
        evidence._validate_bootstrap_receipt(receipt)


def test_bootstrap_policy_rejects_boolean_schema_version(tmp_path: Path) -> None:
    policy_path = Path(__file__).parents[1] / "config" / "uv-bootstrap-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["schema_version"] = True
    modified_policy_path = tmp_path / "uv-bootstrap-policy.json"
    modified_policy_path.write_text(json.dumps(policy), encoding="utf-8")
    receipt = _bootstrap_receipt()
    receipt["policy"]["schema_version"] = True
    receipt["policy"]["sha256"] = hashlib.sha256(modified_policy_path.read_bytes()).hexdigest()

    with pytest.raises(
        ValueError,
        match=re.escape("bootstrap policy.schema_version must be 1"),
    ):
        evidence._validate_bootstrap_receipt(receipt, modified_policy_path)


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    (
        ("policy", "sha256", "0" * 64, "policy SHA-256"),
        ("tool", "version", "0.12.2", "tool.version"),
        ("tool", "asset_sha256", "0" * 64, "tool.asset_sha256"),
        (
            "provenance",
            "source_repository",
            "https://github.com/example/uv",
            "provenance.source_repository",
        ),
        (
            "provenance",
            "signer_workflow_identity",
            "https://github.com/example/uv/.github/workflows/release.yml@refs/heads/main",
            "provenance.signer_workflow_identity",
        ),
    ),
)
def test_bootstrap_receipt_rejects_unreviewed_values(
    section: str,
    field: str,
    value: str,
    message: str,
) -> None:
    receipt = _bootstrap_receipt()
    receipt[section][field] = value

    with pytest.raises(ValueError, match=re.escape(message)):
        evidence._validate_bootstrap_receipt(receipt)


def test_bootstrap_receipt_must_record_success_and_utc_timestamp() -> None:
    receipt = _bootstrap_receipt()
    receipt["status"] = "failure"
    receipt["failure_category"] = "UV_ARCHIVE_DIGEST_MISMATCH"

    with pytest.raises(ValueError, match="status must be success"):
        evidence._validate_bootstrap_receipt(receipt)

    receipt = _bootstrap_receipt()
    receipt["verified_at"] = "2026-08-09T12:34:56"
    with pytest.raises(ValueError, match="verified_at must be an ISO UTC timestamp"):
        evidence._validate_bootstrap_receipt(receipt)


def test_security_dependency_report_requires_exact_verified_schema(tmp_path: Path) -> None:
    report = _security_dependency_report()

    path = _write_security_dependency_report(tmp_path, report)
    assert evidence._validate_security_dependency_report(path) == report

    report["unexpected"] = "field"
    _write_security_dependency_report(tmp_path, report)
    with pytest.raises(ValueError, match="fields do not match schema v1"):
        evidence._validate_security_dependency_report(path)


def test_security_dependency_report_is_bound_to_reviewed_policy(tmp_path: Path) -> None:
    report = _security_dependency_report()
    report["policy_sha256"] = "0" * 64
    path = _write_security_dependency_report(tmp_path, report)

    with pytest.raises(ValueError, match="does not match the reviewed policy"):
        evidence._validate_security_dependency_report(path)


def test_security_dependency_report_rejects_malformed_reviewed_policy(tmp_path: Path) -> None:
    policy = json.loads(evidence.SECURITY_DEPENDENCY_POLICY_PATH.read_text(encoding="utf-8"))
    policy["required_dependencies"][0]["unexpected"] = True
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    report = _security_dependency_report()
    report["policy_sha256"] = evidence._canonical_json_sha256(policy)
    report_path = _write_security_dependency_report(tmp_path, report)

    with pytest.raises(ValueError, match=r"required_dependencies\[0\] fields"):
        evidence._validate_security_dependency_report(report_path, policy_path)


@pytest.mark.parametrize("field", ("schema_version", "policy_schema_version"))
def test_security_dependency_report_rejects_boolean_schema_versions(
    field: str, tmp_path: Path
) -> None:
    report = _security_dependency_report()
    report[field] = True
    path = _write_security_dependency_report(tmp_path, report)

    with pytest.raises(ValueError, match=f"{field} must be 1"):
        evidence._validate_security_dependency_report(path)


def test_security_dependency_report_rejects_unsorted_or_unchecked_edges(tmp_path: Path) -> None:
    report = _security_dependency_report()
    report["checked_issues"] = [363, 346, 131]
    path = _write_security_dependency_report(tmp_path, report)
    with pytest.raises(ValueError, match="checked_issues must be sorted and unique"):
        evidence._validate_security_dependency_report(path)

    report = _security_dependency_report()
    report["required_dependencies"] = [{"blocked": 999, "blocked_by": 346}]
    _write_security_dependency_report(tmp_path, report)
    with pytest.raises(ValueError, match="must reference checked issues"):
        evidence._validate_security_dependency_report(path)


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
    bootstrap_receipt_path = tmp_path / "uv-bootstrap.json"
    bootstrap_receipt_path.write_text(json.dumps(_bootstrap_receipt()), encoding="utf-8")
    security_dependency_path = tmp_path / "security-dependencies.json"
    security_dependency_path.write_text(json.dumps(_security_dependency_report()), encoding="utf-8")
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
            "--bootstrap-receipt",
            str(bootstrap_receipt_path),
            "--security-dependency-report",
            str(security_dependency_path),
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
    assert "Bootstrap verification" in rendered
    assert "uv` `0.12.1" in rendered
    assert "macos-arm64" in rendered
    assert hashlib.sha256(bootstrap_receipt_path.read_bytes()).hexdigest() in rendered
    assert "329541a503de8a4d9bb021814f9c0875efe033c8" in rendered
    assert "Version 1 security dependency verification" in rendered
    assert hashlib.sha256(security_dependency_path.read_bytes()).hexdigest() in rendered
    assert "#131" in rendered
    assert "#365" in rendered
    assert "version-1-security-dependencies" in rendered
