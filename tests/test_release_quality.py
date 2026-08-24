"""Fail-closed tests for the unified v0.7 release quality contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date, timedelta
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
EXPECTED_FUSE_STATES = {
    "RunAsNode": "disabled",
    "EnableCookieEncryption": "enabled",
    "EnableNodeOptionsEnvironmentVariable": "disabled",
    "EnableNodeCliInspectArguments": "disabled",
    "EnableEmbeddedAsarIntegrityValidation": "enabled",
    "OnlyLoadAppFromAsar": "enabled",
    "LoadBrowserProcessSpecificV8Snapshot": "disabled",
    "GrantFileProtocolExtraPrivileges": "disabled",
}


def _digest(*, sha256: str = "d" * 64, size: int = 1) -> dict[str, Any]:
    return {"sha256": sha256, "bytes": size}


def _receipt(
    gates: list[str],
    *,
    artifacts: dict[str, dict[str, Any]] | None = None,
    head: str = HEAD,
) -> dict[str, Any]:
    receipt = {
        "schemaVersion": 2,
        "kind": "verification-receipt",
        "status": "passed",
        "gitHead": head,
        "headBefore": head,
        "headAfter": head,
        "gates": sorted(gates),
        "command": {"executable": "node", "args": ["--test"], "shell": False},
        "result": {
            "exitCode": 0,
            "signal": None,
            "stdout": _digest(size=0),
            "stderr": _digest(size=0),
        },
        "artifacts": copy.deepcopy(artifacts or {}),
        "workspace": {
            "algorithm": "git-workspace-v1",
            "allowedOutputs": [],
            "before": _digest(sha256="e" * 64, size=128),
            "after": _digest(sha256="e" * 64, size=128),
            "status": "unchanged",
        },
    }
    return {**receipt, "receiptFile": _digest(sha256="f" * 64, size=256)}


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


def _file_grant_mediation() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "ancestryllm-packaged-file-grant-evidence",
        "status": "passed",
        "verificationOnlyDialogAdapter": True,
        "observations": {
            "openGrantOpaque": True,
            "openMetadataValidated": True,
            "saveGrantOpaque": True,
            "replacementConfirmed": True,
            "revocationPassed": True,
            "selectedPathsAbsent": True,
        },
    }


def _fault_scenarios() -> dict[str, Any]:
    return {
        "sidecar-withhold-retry": {
            "schemaVersion": 1,
            "kind": "ancestryllm-packaged-fault-evidence",
            "scenario": "sidecar-withhold-retry",
            "status": "passed",
            "packageCopy": True,
            "productionFaultHookUsed": False,
            "observations": {
                "failure": "startup_failed",
                "automaticRestartsRemaining": 2,
                "manualRetriesRemainingBefore": 1,
                "recoveredState": "ready",
                "processExitedAfterWindowClose": True,
            },
        },
        "sidecar-restart-exhaustion-quit": {
            "schemaVersion": 1,
            "kind": "ancestryllm-packaged-fault-evidence",
            "scenario": "sidecar-restart-exhaustion-quit",
            "status": "passed",
            "packageCopy": True,
            "productionFaultHookUsed": False,
            "observations": {
                "automaticRestartCount": 2,
                "exhaustedFailure": "crash_loop",
                "manualRetriesRemainingBefore": 1,
                "manualRetryState": "ready",
                "activeSidecarExitedOnQuit": True,
                "processExitedAfterWindowClose": True,
            },
        },
        "sidecar-integrity-substitution": {
            "schemaVersion": 1,
            "kind": "ancestryllm-packaged-fault-evidence",
            "scenario": "sidecar-integrity-substitution",
            "status": "passed",
            "packageCopy": True,
            "productionFaultHookUsed": False,
            "observations": {
                "failure": "startup_failed",
                "automaticRestartsRemaining": 2,
                "manualRetriesRemainingBefore": 1,
                "manualRetryFailure": "startup_failed",
                "manualRetriesRemainingAfter": 0,
                "verificationProcessTerminated": True,
            },
        },
    }


def _inspection(sidecar_target: str) -> dict[str, Any]:
    platform = sidecar_target.split("-", maxsplit=1)[0]
    if platform == "darwin":
        integrity = {
            "status": "verified",
            "scope": "app.asar",
            "algorithm": "SHA256",
            "hash": "8" * 64,
        }
    else:
        integrity = {
            "status": "not-applicable",
            "scope": "app.asar",
            "reason": "platform does not expose ASAR integrity metadata",
        }
    return {
        "schemaVersion": 1,
        "kind": "ancestryllm-desktop-package-security-inspection",
        "platform": platform,
        "fuses": {
            "status": "verified",
            "count": len(EXPECTED_FUSE_STATES),
            "items": [
                {
                    "name": name,
                    "expected": state,
                    "actual": state,
                    "status": "verified",
                }
                for name, state in EXPECTED_FUSE_STATES.items()
            ],
        },
        "asar": {
            "presence": {"status": "verified"},
            "integrity": integrity,
        },
    }


def _target(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    artifacts = {
        "metrics": _digest(sha256="1" * 64),
        "fuseInspection": _digest(sha256="2" * 64),
        "fileGrantEvidence": _digest(sha256="3" * 64),
        "withholdEvidence": _digest(sha256="4" * 64),
        "restartEvidence": _digest(sha256="5" * 64),
        "integrityEvidence": _digest(sha256="6" * 64),
    }
    runtime_receipt = _receipt(
        ["packageRuntimePassed", "rendererZeroEgressCanaryPassed"],
        artifacts={"metrics": artifacts["metrics"]},
    )
    receipts = {
        "packageRuntimePassed": runtime_receipt,
        "sidecarProcessTreeGuardPassed": _receipt(["sidecarProcessTreeGuardPassed"]),
        "sidecarSmokePassed": _receipt(["sidecarSmokePassed"]),
        "fusesInspectedPassed": _receipt(
            ["fusesInspectedPassed"],
            artifacts={"fuseInspection": artifacts["fuseInspection"]},
        ),
        "rendererZeroEgressCanaryPassed": copy.deepcopy(runtime_receipt),
        "normalLaunchDebugSurfaceAbsentPassed": _receipt(["normalLaunchDebugSurfaceAbsentPassed"]),
        "packagedFileGrantSmokePassed": _receipt(
            ["packagedFileGrantSmokePassed"],
            artifacts={"fileGrantEvidence": artifacts["fileGrantEvidence"]},
        ),
        "packagedSidecarWithholdRetryPassed": _receipt(
            ["packagedSidecarWithholdRetryPassed"],
            artifacts={"faultEvidence": artifacts["withholdEvidence"]},
        ),
        "packagedSidecarRestartExhaustionQuitPassed": _receipt(
            ["packagedSidecarRestartExhaustionQuitPassed"],
            artifacts={"faultEvidence": artifacts["restartEvidence"]},
        ),
        "packagedSidecarIntegritySubstitutionPassed": _receipt(
            ["packagedSidecarIntegritySubstitutionPassed"],
            artifacts={
                "failureDiagnostics": _digest(sha256="8" * 64),
                "faultEvidence": artifacts["integrityEvidence"],
                "substitutedSidecar": _digest(sha256="7" * 64),
            },
        ),
    }
    return {
        "schemaVersion": 2,
        "kind": "target",
        "gitHead": HEAD,
        "runner": row["runner"],
        "sidecarTarget": row["sidecarTarget"],
        "expectedOs": row["expectedOs"],
        "actualOs": row["expectedOs"],
        "arch": row["arch"],
        "hostArch": row["hostArch"],
        "packageBoundary": "unpacked-native",
        "platformValidated": True,
        "artifactKind": "unpublished-unpacked-native",
        "signingVerified": False,
        "packageRuntime": True,
        "sidecarSmoke": True,
        "fusesInspected": True,
        "rendererZeroEgressCanary": True,
        "normalLaunchDebugSurfaceAbsent": True,
        "packagedFileGrantSmoke": True,
        "performancePassed": True,
        "gates": dict.fromkeys(quality.TARGET_RECEIPT_GATES, True),
        "receipts": receipts,
        "artifacts": artifacts,
        "fileGrantMediation": _file_grant_mediation(),
        "faultScenarios": _fault_scenarios(),
        "performance": _performance(row, policy),
        "inspection": _inspection(row["sidecarTarget"]),
    }


def _desktop(policy: dict[str, Any]) -> dict[str, Any]:
    targets = [_target(row, policy) for row in policy["performance"]["targets"]]
    security_gates = policy["security"]["desktopReceiptGates"]
    security_receipt = _receipt(security_gates)
    sbom = _digest(sha256="c" * 64, size=512)
    security_receipt["artifacts"]["sbom"] = copy.deepcopy(sbom)
    return {
        "schemaVersion": 2,
        "kind": "aggregate",
        "gitHead": HEAD,
        "status": "passed",
        "platformValidated": True,
        "toolVersions": copy.deepcopy(policy["qa"]["toolVersions"]),
        "targets": targets,
        "security": {
            "schemaVersion": 2,
            "kind": "security",
            "gitHead": HEAD,
            "gates": dict.fromkeys(security_gates, True),
            "receipts": {gate: copy.deepcopy(security_receipt) for gate in security_gates},
            "sbom": sbom,
        },
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
    assert manifest["inputDigests"] == [
        {
            **policy["evidence"]["inputs"][0],
            "sha256": quality._canonical_sha256(_readiness(policy)),
        },
        {
            **policy["evidence"]["inputs"][1],
            "sha256": quality._canonical_sha256(_desktop(policy)),
        },
    ]
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


def test_policy_cannot_substitute_release_evidence_inputs() -> None:
    policy = _policy()
    policy["evidence"]["inputs"][0] = {
        "artifact": "substituted-evidence",
        "path": "gates.json",
    }

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ001.*required inputs"):
        _build(policy)


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda evidence: evidence.update({"unexpected": True}), "RQ003"),
        (lambda evidence: evidence["gates"][0].update({"unexpected": True}), "RQ004"),
    ),
)
def test_readiness_evidence_uses_closed_schemas(mutate: Any, code: str) -> None:
    policy = _policy()
    readiness = _readiness(policy)
    mutate(readiness)

    with pytest.raises(quality.ReleaseQualityError, match=rf"{code}.*exact schema"):
        _build(policy, readiness=readiness)


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


def test_exception_requires_independent_approval_and_a_bounded_expiry() -> None:
    policy = _policy()
    as_of = date(2026, 8, 24)
    policy["exceptions"] = [
        {
            "id": "RQ-EXAMPLE",
            "family": "qa",
            "gate": "tests-and-coverage",
            "owner": "release-owner",
            "approvedBy": "release-owner",
            "reason": "Synthetic policy-validation exception.",
            "expires": (as_of + timedelta(days=1)).isoformat(),
        }
    ]

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ009.*independent"):
        _build(policy, as_of=as_of)

    policy["exceptions"][0]["approvedBy"] = "security-owner"
    policy["exceptions"][0]["expires"] = (as_of + timedelta(days=91)).isoformat()
    with pytest.raises(quality.ReleaseQualityError, match=r"RQ009.*90 days"):
        _build(policy, as_of=as_of)

    policy["exceptions"][0]["expires"] = (as_of + timedelta(days=90)).isoformat()
    assert _build(policy, as_of=as_of)["exceptions"] == policy["exceptions"]


@pytest.mark.parametrize(
    ("family", "gate"),
    (
        ("qa", "not-a-gate"),
        ("security", "tests-and-coverage"),
        ("performance", "diagnosticsContractPassed"),
        ("diagnostics", "coldLaunchMs"),
    ),
)
def test_exception_gate_must_belong_to_the_selected_family(
    family: str,
    gate: str,
) -> None:
    policy = _policy()
    policy["exceptions"] = [
        {
            "id": "RQ-EXAMPLE",
            "family": family,
            "gate": gate,
            "owner": "release-owner",
            "approvedBy": "security-owner",
            "reason": "Synthetic policy-validation exception.",
            "expires": "2026-08-25",
        }
    ]

    with pytest.raises(
        quality.ReleaseQualityError,
        match=rf"RQ009.*unknown gate for {family}",
    ):
        _build(policy)


@pytest.mark.parametrize("surface", ["security", "target"])
def test_nested_desktop_receipts_must_bind_to_the_exact_head(surface: str) -> None:
    policy = _policy()
    desktop = _desktop(policy)
    if surface == "security":
        receipt = next(iter(desktop["security"]["receipts"].values()))
    else:
        receipt = next(iter(desktop["targets"][0]["receipts"].values()))
    receipt["gitHead"] = "b" * 40

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ00[67].*exact head"):
        _build(policy, desktop=desktop)


def test_security_receipt_inventory_is_required() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["security"].pop("receipts")

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ007.*receipt inventory"):
        _build(policy, desktop=desktop)


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


def test_non_finite_performance_observation_is_rejected() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    performance = desktop["targets"][0]["performance"]
    metric = policy["performance"]["metrics"][0]
    non_finite = float("nan")
    performance["observed"][metric] = non_finite
    performance["checks"][metric]["observed"] = non_finite

    with pytest.raises(quality.ReleaseQualityError, match=rf"RQ006.*{metric}.*invalid"):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize(
    ("surface", "code"),
    (
        ("policy", "RQ001"),
        ("readiness", "RQ003"),
        ("aggregate", "RQ003"),
        ("target", "RQ006"),
        ("target-receipt", "RQ006"),
        ("file-grant", "RQ006"),
        ("fault", "RQ006"),
        ("inspection", "RQ006"),
        ("security", "RQ007"),
    ),
)
def test_boolean_schema_versions_are_rejected(surface: str, code: str) -> None:
    policy = _policy()
    readiness = _readiness(policy)
    desktop = _desktop(policy)
    if surface == "policy":
        policy["schemaVersion"] = True
    elif surface == "readiness":
        readiness["schema_version"] = True
    elif surface == "aggregate":
        desktop["schemaVersion"] = True
    elif surface == "target":
        desktop["targets"][0]["schemaVersion"] = True
    elif surface == "target-receipt":
        desktop["targets"][0]["receipts"]["packageRuntimePassed"]["schemaVersion"] = True
    elif surface == "file-grant":
        desktop["targets"][0]["fileGrantMediation"]["schemaVersion"] = True
    elif surface == "fault":
        desktop["targets"][0]["faultScenarios"]["sidecar-withhold-retry"]["schemaVersion"] = True
    elif surface == "inspection":
        desktop["targets"][0]["inspection"]["schemaVersion"] = True
    else:
        desktop["security"]["schemaVersion"] = True

    with pytest.raises(quality.ReleaseQualityError, match=rf"{code}.*schema"):
        _build(policy, readiness=readiness, desktop=desktop)


def test_target_evidence_uses_the_complete_closed_schema() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0].pop("artifactKind")

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*exact schema"):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize(
    ("gate", "receipt_artifact", "target_artifact"),
    (
        ("packageRuntimePassed", "metrics", "metrics"),
        ("fusesInspectedPassed", "fuseInspection", "fuseInspection"),
        ("packagedFileGrantSmokePassed", "fileGrantEvidence", "fileGrantEvidence"),
        ("packagedSidecarWithholdRetryPassed", "faultEvidence", "withholdEvidence"),
        (
            "packagedSidecarRestartExhaustionQuitPassed",
            "faultEvidence",
            "restartEvidence",
        ),
        (
            "packagedSidecarIntegritySubstitutionPassed",
            "faultEvidence",
            "integrityEvidence",
        ),
    ),
)
def test_target_artifacts_are_bound_to_their_successful_receipts(
    gate: str,
    receipt_artifact: str,
    target_artifact: str,
) -> None:
    policy = _policy()
    desktop = _desktop(policy)
    target = desktop["targets"][0]
    target["receipts"][gate]["artifacts"][receipt_artifact] = _digest(sha256="0" * 64)

    with pytest.raises(
        quality.ReleaseQualityError,
        match=rf"RQ006.*{target_artifact}.*not bound",
    ):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize(
    ("gate", "artifact"),
    [
        (gate, artifact)
        for gate, artifacts in quality.TARGET_RECEIPT_ARTIFACTS.items()
        for artifact in sorted(artifacts)
    ],
)
def test_target_receipts_require_each_gate_artifact(gate: str, artifact: str) -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0]["receipts"][gate]["artifacts"].pop(artifact)

    with pytest.raises(quality.ReleaseQualityError, match=rf"RQ006.*{artifact}"):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize("gate", quality.TARGET_RECEIPT_GATES)
def test_target_receipt_artifact_inventories_reject_unknown_artifacts(gate: str) -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0]["receipts"][gate]["artifacts"]["unknownArtifact"] = _digest()

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*artifact inventory"):
        _build(policy, desktop=desktop)


def test_integrity_receipt_requires_the_substituted_sidecar_digest() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    desktop["targets"][0]["receipts"]["packagedSidecarIntegritySubstitutionPassed"][
        "artifacts"
    ].pop("substitutedSidecar")

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*substitutedSidecar"):
        _build(policy, desktop=desktop)


@pytest.mark.parametrize(
    ("surface", "mutate", "message"),
    (
        (
            "file-grant",
            lambda target: target["fileGrantMediation"]["observations"].pop("selectedPathsAbsent"),
            "file-grant observations",
        ),
        (
            "fault",
            lambda target: target["faultScenarios"]["sidecar-withhold-retry"].update(
                {"unexpected": True}
            ),
            "sidecar-withhold-retry fault evidence",
        ),
        (
            "inspection",
            lambda target: target["inspection"]["fuses"].update({"status": "failed"}),
            "fuses",
        ),
    ),
)
def test_target_supporting_evidence_is_validated(
    surface: str,
    mutate: Any,
    message: str,
) -> None:
    del surface
    policy = _policy()
    desktop = _desktop(policy)
    mutate(desktop["targets"][0])

    with pytest.raises(quality.ReleaseQualityError, match=rf"RQ006.*{message}"):
        _build(policy, desktop=desktop)


def test_fuse_inventory_cannot_omit_a_required_fuse() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    fuses = desktop["targets"][0]["inspection"]["fuses"]
    fuses["items"].pop()
    fuses["count"] -= 1

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*fuses.*inventory"):
        _build(policy, desktop=desktop)


def test_fuse_inventory_cannot_rewrite_the_expected_state() -> None:
    policy = _policy()
    desktop = _desktop(policy)
    item = desktop["targets"][0]["inspection"]["fuses"]["items"][0]
    item["expected"] = "enabled"
    item["actual"] = "enabled"

    with pytest.raises(quality.ReleaseQualityError, match=r"RQ006.*fuses.*inventory"):
        _build(policy, desktop=desktop)


def test_inputs_are_not_mutated() -> None:
    policy = _policy()
    readiness = _readiness(policy)
    desktop = _desktop(policy)
    before = copy.deepcopy((policy, readiness, desktop))

    _build(policy, readiness=readiness, desktop=desktop)

    assert (policy, readiness, desktop) == before
