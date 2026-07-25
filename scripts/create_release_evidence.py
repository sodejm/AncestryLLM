#!/usr/bin/env python3
"""Create a payload-free Markdown evidence manifest for a release run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_STATUSES = {"verified", "failed", "unavailable", "unverified"}
DISPOSITIONS = {"fixed", "false-positive", "accepted-risk"}
SEVERITIES = {"critical", "high", "medium", "low", "informational"}
REQUIRED_GATES = {
    "codeql",
    "cross-platform-install",
    "cyclonedx-sbom",
    "dependency-audit",
    "lifecycle-audit",
    "milestone-closure",
    "mypy",
    "privacy-regressions",
    "repository-safety",
    "reproducible-distributions",
    "ruff-format",
    "secret-scan",
    "semgrep",
    "tests-and-coverage",
    "workflow-security-analysis",
}
FINDING_SOURCES = {
    "dependency-audit",
    "semgrep",
    "codeql",
    "secret-scan",
    "repository-safety",
}
REQUIRED_INTEROPERABILITY_VENDORS = {
    "Ancestry",
    "Geni",
    "MyHeritage",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise ValueError(f"{field} must be a non-empty single-line string")
    return value.strip()


def _date(value: object, field: str) -> str:
    raw = _text(value, field)
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    return raw


def _url(value: object, field: str) -> str:
    raw = _text(value, field)
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username:
        raise ValueError(f"{field} must be an HTTPS URL without user information")
    return raw


def _validate_header(payload: dict[str, Any], version: str, label: str) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError(f"{label}.schema_version must be 1")
    if payload.get("release") != version:
        raise ValueError(f"{label}.release must match the requested version")
    _date(payload.get("reviewed_at"), f"{label}.reviewed_at")
    _text(payload.get("reviewed_by"), f"{label}.reviewed_by")


def _validate_findings(
    payload: dict[str, Any],
    version: str,
    *,
    as_of: date | None = None,
) -> list[dict[str, str]]:
    _validate_header(payload, version, "findings")
    evidence_date = as_of or datetime.now(UTC).date()
    if payload.get("inventory_complete") is not True:
        raise ValueError("findings.inventory_complete must be true")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("findings.sources must be a list")
    source_names: set[str] = set()
    for index, source in enumerate(sources):
        name = _text(source, f"findings.sources[{index}]")
        if name in source_names:
            raise ValueError(f"duplicate findings source: {name}")
        source_names.add(name)
    missing_sources = FINDING_SOURCES - source_names
    unexpected_sources = source_names - FINDING_SOURCES
    if missing_sources or unexpected_sources:
        raise ValueError(
            "findings.sources differs from the required inventory: "
            f"missing={sorted(missing_sources)}, unexpected={sorted(unexpected_sources)}"
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings.findings must be a list")
    validated: list[dict[str, str]] = []
    identifiers: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"findings.findings[{index}] must be an object")
        prefix = f"findings.findings[{index}]"
        identifier = _text(finding.get("id"), f"{prefix}.id")
        if identifier in identifiers:
            raise ValueError(f"duplicate finding id: {identifier}")
        identifiers.add(identifier)
        source = _text(finding.get("source"), f"{prefix}.source")
        if source not in source_names:
            raise ValueError(f"{prefix}.source is not in findings.sources")
        severity = _text(finding.get("severity"), f"{prefix}.severity")
        if severity not in SEVERITIES:
            raise ValueError(f"{prefix}.severity is unsupported")
        disposition = _text(finding.get("disposition"), f"{prefix}.disposition")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"{prefix}.disposition is unsupported")
        expires = _date(finding.get("expires"), f"{prefix}.expires")
        if date.fromisoformat(expires) < evidence_date:
            raise ValueError(
                f"{prefix}.expires predates the release evidence date {evidence_date.isoformat()}"
            )
        validated.append(
            {
                "id": identifier,
                "source": source,
                "severity": severity,
                "disposition": disposition,
                "owner": _text(finding.get("owner"), f"{prefix}.owner"),
                "expires": expires,
                "evidence_url": _url(finding.get("evidence_url"), f"{prefix}.evidence_url"),
                "rationale": _text(finding.get("rationale"), f"{prefix}.rationale"),
            }
        )
    return validated


def _validate_gates(payload: dict[str, Any], version: str, commit: str) -> list[dict[str, str]]:
    if payload.get("schema_version") != 1:
        raise ValueError("gates.schema_version must be 1")
    if payload.get("release") != version:
        raise ValueError("gates.release must match the requested version")
    if payload.get("commit") != commit:
        raise ValueError("gates.commit must match the requested commit")
    run_url = _url(payload.get("run_url"), "gates.run_url")
    gates = payload.get("gates")
    if not isinstance(gates, list):
        raise ValueError("gates.gates must be a list")
    validated: list[dict[str, str]] = []
    names: set[str] = set()
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise ValueError(f"gates.gates[{index}] must be an object")
        prefix = f"gates.gates[{index}]"
        name = _text(gate.get("name"), f"{prefix}.name")
        if name in names:
            raise ValueError(f"duplicate release gate: {name}")
        names.add(name)
        status = _text(gate.get("status"), f"{prefix}.status")
        if status not in EVIDENCE_STATUSES:
            raise ValueError(f"{prefix}.status is unsupported")
        if status != "verified":
            raise ValueError(f"{prefix}.status must be verified")
        evidence_url = _url(gate.get("evidence_url", run_url), f"{prefix}.evidence_url")
        validated.append(
            {
                "name": name,
                "status": status,
                "evidence_url": evidence_url,
            }
        )
    missing = REQUIRED_GATES - names
    unexpected = names - REQUIRED_GATES
    if missing or unexpected:
        raise ValueError(
            "gates.gates differs from the required inventory: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    return sorted(validated, key=lambda item: item["name"])


def _validate_interoperability(payload: dict[str, Any], version: str) -> list[dict[str, Any]]:
    _validate_header(payload, version, "interoperability")
    vendors = payload.get("vendors")
    if not isinstance(vendors, list) or not vendors:
        raise ValueError("interoperability.vendors must be a non-empty list")
    validated: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, vendor in enumerate(vendors):
        if not isinstance(vendor, dict):
            raise ValueError(f"interoperability.vendors[{index}] must be an object")
        prefix = f"interoperability.vendors[{index}]"
        name = _text(vendor.get("vendor"), f"{prefix}.vendor")
        if name in names:
            raise ValueError(f"duplicate interoperability vendor: {name}")
        names.add(name)
        status = _text(vendor.get("status"), f"{prefix}.status")
        if status not in EVIDENCE_STATUSES:
            raise ValueError(f"{prefix}.status is unsupported")
        evidence_urls = vendor.get("evidence_urls")
        if not isinstance(evidence_urls, list) or not evidence_urls:
            raise ValueError(f"{prefix}.evidence_urls must be a non-empty list")
        urls = [_url(item, f"{prefix}.evidence_urls") for item in evidence_urls]
        fixture_ids = vendor.get("fixture_ids", [])
        if not isinstance(fixture_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in fixture_ids
        ):
            raise ValueError(f"{prefix}.fixture_ids must be a list of strings")
        application_version = vendor.get("application_version")
        if status in {"verified", "failed"}:
            if vendor.get("fictional_data") is not True:
                raise ValueError(f"{prefix}.fictional_data must be true")
            if not fixture_ids:
                raise ValueError(f"{prefix}.fixture_ids must identify fictional inputs")
            application_version = _text(application_version, f"{prefix}.application_version")
        elif application_version is not None:
            application_version = _text(application_version, f"{prefix}.application_version")
        validated.append(
            {
                "vendor": name,
                "status": status,
                "assessed_at": _date(vendor.get("assessed_at"), f"{prefix}.assessed_at"),
                "evidence_urls": urls,
                "fixture_ids": [item.strip() for item in fixture_ids],
                "application_version": application_version,
                "notes": _text(vendor.get("notes"), f"{prefix}.notes"),
            }
        )
    missing_vendors = REQUIRED_INTEROPERABILITY_VENDORS - names
    if missing_vendors:
        raise ValueError(
            f"interoperability.vendors is missing supported importers: {sorted(missing_vendors)}"
        )
    return validated


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "'")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gates", required=True, type=Path)
    parser.add_argument("--findings", required=True, type=Path)
    parser.add_argument("--interoperability", required=True, type=Path)
    args = parser.parse_args()

    if not SEMVER.fullmatch(args.version):
        parser.error("version must be a stable SemVer value")
    if not COMMIT.fullmatch(args.commit):
        parser.error("commit must be a full lowercase Git SHA")
    try:
        workflow_url = _url(args.run_url, "run-url")
    except ValueError as exc:
        parser.error(str(exc))
    generated_date = datetime.now(UTC).date()
    files = sorted(
        path
        for path in args.artifacts.iterdir()
        if path.is_file() and path.name != "SHA256SUMS" and path.resolve() != args.output.resolve()
    )
    if not files:
        parser.error("artifact directory is empty")
    try:
        gates_payload = _load_object(args.gates, "gates")
        gates = _validate_gates(gates_payload, args.version, args.commit)
        findings_payload = _load_object(args.findings, "findings")
        findings = _validate_findings(
            findings_payload,
            args.version,
            as_of=generated_date,
        )
        interoperability_payload = _load_object(args.interoperability, "interoperability")
        vendors = _validate_interoperability(interoperability_payload, args.version)
    except ValueError as exc:
        parser.error(str(exc))

    hashes = "\n".join(f"- `{path.name}`: `{_sha256(path)}`" for path in files)
    gate_rows = "\n".join(
        "| {name} | {status} | [workflow evidence]({evidence_url}) |".format(
            **{key: _escape(value) for key, value in gate.items()}
        )
        for gate in gates
    )
    if findings:
        finding_rows = "\n".join(
            "| {id} | {source} | {severity} | {disposition} | {owner} | {expires} | [evidence]({evidence_url}) | {rationale} |".format(
                **{key: _escape(value) for key, value in finding.items()}
            )
            for finding in findings
        )
    else:
        finding_rows = "| None | - | - | - | - | - | - | No open or dispositioned findings. |"
    vendor_rows = "\n".join(
        "| {vendor} | {status} | {assessed_at} | {version} | {evidence} | {notes} |".format(
            vendor=_escape(vendor["vendor"]),
            status=vendor["status"],
            assessed_at=vendor["assessed_at"],
            version=_escape(vendor["application_version"] or "not recorded"),
            evidence=", ".join(
                f"[{index}]({url})" for index, url in enumerate(vendor["evidence_urls"], start=1)
            ),
            notes=_escape(vendor["notes"]),
        )
        for vendor in vendors
    )
    generated = generated_date.isoformat()
    args.output.write_text(
        f"""# AncestryLLM {args.version} release evidence

- Version: `{args.version}`
- Tag: `v{args.version}`
- Commit: `{args.commit}`
- Generated: `{generated}`
- Manifest workflow: {workflow_url}
- Approved readiness workflow: {gates_payload["run_url"]}

## Automated gates

| Gate | Status | Evidence |
|---|---|---|
{gate_rows}

## Security finding dispositions

- Inventory reviewed by: `{_escape(str(findings_payload["reviewed_by"]))}`
- Inventory reviewed at: `{findings_payload["reviewed_at"]}`

| ID | Source | Severity | Disposition | Owner | Expires | Evidence | Rationale |
|---|---|---|---|---|---|---|---|
{finding_rows}

## Interoperability

Only a `verified` row supports a positive compatibility claim. `failed`,
`unavailable`, and `unverified` are release limitations.

| Vendor/importer | Status | Assessed | Version | Evidence | Notes |
|---|---|---|---|---|---|
{vendor_rows}

## Artifact SHA-256

{hashes}
""",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
