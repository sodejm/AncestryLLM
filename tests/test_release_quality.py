"""Fail-closed tests for the unified v0.7 release quality contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "config" / "release-quality-policy-v1.json"
SCRIPT_PATH = ROOT / "scripts" / "verify_release_quality.py"

_SPEC = importlib.util.spec_from_file_location("verify_release_quality", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
quality = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = quality
_SPEC.loader.exec_module(quality)

HEAD = "a" * 40
VERSION = "0.7.0"


def _policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _readiness(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release": VERSION,
        "commit": HEAD,
        "run_url": "https://example.test/readiness/1",
        "gates": [
            {
                "name": gate,
                "status": "verified",
                "evidence_url": f"https://example.test/readiness/1#{gate}",
            }
            for gate in policy["qa"]["readinessGates"]
        ],
    }


def _performance(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    ceilings = row["performanceCeilings"]
    observed = dict.fromkeys(policy["performance"]["metrics"], 0)
    return {
        "policyVersion": policy["performance"]["policyVersion"],
        "runner": row["runner"],
        "platform": row["sidecarTarget"],
        "observed": observed,
        "ceilings": ceilings,
        "checks": {
            name: {"observed": 0, "ceiling": ceiling, "passed": True}
            for name, ceiling in ceilings.items()
        },
        "passed": True,
    }


def _desktop(policy: dict[str, Any]) -> dict[str, Any]:
    targets = [
        {
            "runner": row["runner"],
            "sidecarTarget": row["sidecarTarget"],
            "expectedOs": row["expectedOs"],
            "actualOs": row["expectedOs"],
            "arch": row["arch"],
            "hostArch": row["hostArch"],
            "packageBoundary": "unpacked-native",
            "platformValidated": True,
            "performance": _performance(row, policy),
        }
        for row in policy["performance"]["targets"]
    ]
    return {
        "schemaVersion": 2,
        "kind": "aggregate",
        "gitHead": HEAD,
        "status": "passed",
        "platformValidated": True,
        "toolVersions": copy.deepcopy(policy["qa"]["toolVersions"]),
        "targets": targets,
        "security": {"gates": dict.fromkeys(policy["security"]["desktopReceiptGates"], True)},
        "publicationRequirements": {"desktopInstaller": True},
    }


def _build(
    policy: dict[str, Any],
    readiness: dict[str, Any] | None = None,
    desktop: dict[str, Any] | None = None,
    *,
    as_of: date = date(2026, 8, 24),
) -> dict[str, Any]:
    return quality.build_manifest(
        version=VERSION,
        commit=HEAD,
        readiness=readiness or _readiness(policy),
        desktop=desktop or _desktop(policy),
        policy=policy,
        as_of=as_of,
    )


def test_valid_evidence_produces_all_four_quality_families() -> None:
    policy = _policy()

    manifest = _build(policy)

    assert manifest["status"] == "approved"
    assert manifest["release"] == VERSION
    assert manifest["commit"] == HEAD
    assert set(manifest["families"]) == {
        "diagnostics",
        "performance",
        "qa",
        "security",
    }
    assert manifest["evidence"] == policy["evidence"]
    for family in manifest["families"]:
        assert manifest["families"][family]["commands"] == policy["families"][family]["commands"]
    assert manifest["families"]["performance"]["method"] == policy["performance"]["method"]
    assert manifest["exceptions"] == []


def test_cli_writes_an_approved_exact_head_manifest(tmp_path: Path) -> None:
    policy = _policy()
    readiness_path = tmp_path / "gates.json"
    desktop_path = tmp_path / "desktop-evidence.json"
    output_path = tmp_path / "approved" / "release-quality-approval.json"
    readiness_path.write_text(json.dumps(_readiness(policy)), encoding="utf-8")
    desktop_path.write_text(json.dumps(_desktop(policy)), encoding="utf-8")

    status = quality.main(
        [
            "--version",
            VERSION,
            "--commit",
            HEAD,
            "--gates",
            str(readiness_path),
            "--desktop-evidence",
            str(desktop_path),
            "--policy",
            str(POLICY_PATH),
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "approved"
    assert manifest["release"] == VERSION
    assert manifest["commit"] == HEAD


def test_wrong_head_is_rejected() -> None:
    policy = _policy()
    readiness = _readiness(policy)
    readiness["commit"] = "b" * 40

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ002.*exact head"):
        _build(policy, readiness=readiness)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda policy: policy["families"]["qa"].update({"commands": []}),
            "qa commands",
        ),
        (
            lambda policy: policy["evidence"].update({"approvalPath": "approval.json"}),
            "approval path",
        ),
        (
            lambda policy: policy["performance"]["method"].update({"packageBoundary": "asar"}),
            "unpacked-native",
        ),
        (
            lambda policy: policy["diagnostics"].update({"telemetry": True}),
            "telemetry",
        ),
    ),
)
def test_malformed_policy_contract_is_rejected(
    mutate: Any,
    message: str,
) -> None:
    policy = _policy()
    mutate(policy)

    with pytest.raises(quality.ReleaseQualityError, match=rf"RQ001.*{message}"):
        _build(policy)


def test_missing_readiness_gate_is_rejected() -> None:
    policy = _policy()
    readiness = _readiness(policy)
    readiness["gates"].pop()

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ004.*readiness gate"):
        _build(policy, readiness=readiness)


def test_unknown_runner_or_tool_version_is_rejected() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0]["runner"] = "macos-latest"

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*target matrix"):
        _build(policy, desktop=desktop)

    desktop = _desktop(policy)
    desktop["toolVersions"]["node"] = "latest"
    with pytest.raises(quality.ReleaseQualityError, match=r"RQ005.*tool versions"):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize("gate", ["desktopCoveragePassed", "jsStaticAnalysisPassed"])
def test_missing_desktop_coverage_or_javascript_static_analysis_is_rejected(
    gate: str,
) -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["security"]["gates"].pop(gate)

    with pytest.raises(quality.ReleaseQualityError, match=rf"RQ007.*{gate}"):
        _build(policy, desktop=desktop)


def test_expired_exception_is_rejected_and_current_exception_is_retained() -> None:
    policy = _policy()
    policy["exceptions"] = [
        {
            "id": "RQ-EXAMPLE",
            "family": "qa",
            "gate": "tests-and-coverage",
            "owner": "release-owner",
            "approvedBy": "security-owner",
            "reason": "Synthetic policy-validation exception.",
            "expires": "2026-08-23",
        }
    ]

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ009.*expired"):
        _build(policy)

    policy["exceptions"][0]["expires"] = "2026-08-25"
    manifest = _build(policy)
    assert manifest["exceptions"] == policy["exceptions"]


def test_missing_diagnostics_canary_is_rejected() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["security"]["gates"].pop(policy["diagnostics"]["syntheticCanaryGate"])

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ008.*diagnostics"):
        _build(policy, desktop=desktop)


def test_diagnostics_schema_digest_mismatch_is_rejected() -> None:
    policy = _policy()
    policy["diagnostics"]["schemaSha256"] = "0" * 64

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ008.*schema digest"):
        _build(policy)


def test_incomplete_performance_evidence_is_rejected() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0]["performance"]["checks"].pop("rssBytes")

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*performance"):
        _build(policy, desktop=desktop)


def test_inputs_are_not_mutated() -> None:
    policy = _policy()
    readiness = _readiness(policy)
    desktop = _desktop(policy)
    before = copy.deepcopy((policy, readiness, desktop))

    _build(policy, readiness=readiness, desktop=desktop)

    assert (policy, readiness, desktop) == before
