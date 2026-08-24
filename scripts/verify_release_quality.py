#!/usr/bin/env python3
"""Validate exact-head release quality evidence and emit its approval manifest."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Never

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "release-quality-policy-v1.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAX_SAFE_INTEGER = (2**53) - 1
FAMILIES = ("diagnostics", "performance", "qa", "security")
REQUIRED_EVIDENCE_INPUTS = (
    ("release-evidence", "gates.json"),
    ("desktop-evidence-aggregate", "desktop-evidence.json"),
)
TARGET_RECEIPT_GATES = (
    "packageRuntimePassed",
    "sidecarProcessTreeGuardPassed",
    "sidecarSmokePassed",
    "fusesInspectedPassed",
    "rendererZeroEgressCanaryPassed",
    "normalLaunchDebugSurfaceAbsentPassed",
    "packagedFileGrantSmokePassed",
    "packagedSidecarWithholdRetryPassed",
    "packagedSidecarRestartExhaustionQuitPassed",
    "packagedSidecarIntegritySubstitutionPassed",
)
TARGET_EVIDENCE_KEYS = {
    "schemaVersion",
    "kind",
    "gitHead",
    "runner",
    "sidecarTarget",
    "expectedOs",
    "actualOs",
    "arch",
    "hostArch",
    "packageBoundary",
    "platformValidated",
    "artifactKind",
    "signingVerified",
    "packageRuntime",
    "sidecarSmoke",
    "fusesInspected",
    "rendererZeroEgressCanary",
    "normalLaunchDebugSurfaceAbsent",
    "packagedFileGrantSmoke",
    "performancePassed",
    "gates",
    "receipts",
    "artifacts",
    "fileGrantMediation",
    "faultScenarios",
    "performance",
    "inspection",
}
TARGET_ARTIFACT_BINDINGS = (
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
)
TARGET_RECEIPT_ARTIFACTS: dict[str, frozenset[str]] = {
    "packageRuntimePassed": frozenset({"metrics"}),
    "sidecarProcessTreeGuardPassed": frozenset(),
    "sidecarSmokePassed": frozenset(),
    "fusesInspectedPassed": frozenset({"fuseInspection"}),
    "rendererZeroEgressCanaryPassed": frozenset({"metrics"}),
    "normalLaunchDebugSurfaceAbsentPassed": frozenset(),
    "packagedFileGrantSmokePassed": frozenset({"fileGrantEvidence"}),
    "packagedSidecarWithholdRetryPassed": frozenset({"faultEvidence"}),
    "packagedSidecarRestartExhaustionQuitPassed": frozenset({"faultEvidence"}),
    "packagedSidecarIntegritySubstitutionPassed": frozenset(
        {"failureDiagnostics", "faultEvidence", "substitutedSidecar"}
    ),
}
TARGET_GATE_FLAGS = {
    "packageRuntime": "packageRuntimePassed",
    "sidecarSmoke": "sidecarSmokePassed",
    "fusesInspected": "fusesInspectedPassed",
    "rendererZeroEgressCanary": "rendererZeroEgressCanaryPassed",
    "normalLaunchDebugSurfaceAbsent": "normalLaunchDebugSurfaceAbsentPassed",
    "packagedFileGrantSmoke": "packagedFileGrantSmokePassed",
}
FILE_GRANT_OBSERVATIONS = {
    "openGrantOpaque": True,
    "openMetadataValidated": True,
    "saveGrantOpaque": True,
    "replacementConfirmed": True,
    "revocationPassed": True,
    "selectedPathsAbsent": True,
}
FAULT_SCENARIOS: dict[str, dict[str, Any]] = {
    "packagedSidecarWithholdRetryPassed": {
        "name": "sidecar-withhold-retry",
        "artifact": "withholdEvidence",
        "observations": {
            "failure": "startup_failed",
            "automaticRestartsRemaining": 2,
            "manualRetriesRemainingBefore": 1,
            "recoveredState": "ready",
            "processExitedAfterWindowClose": True,
        },
    },
    "packagedSidecarRestartExhaustionQuitPassed": {
        "name": "sidecar-restart-exhaustion-quit",
        "artifact": "restartEvidence",
        "observations": {
            "automaticRestartCount": 2,
            "exhaustedFailure": "crash_loop",
            "manualRetriesRemainingBefore": 1,
            "manualRetryState": "ready",
            "activeSidecarExitedOnQuit": True,
            "processExitedAfterWindowClose": True,
        },
    },
    "packagedSidecarIntegritySubstitutionPassed": {
        "name": "sidecar-integrity-substitution",
        "artifact": "integrityEvidence",
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


class ReleaseQualityError(ValueError):
    """A stable, operator-actionable release-quality validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _reject(code: str, message: str) -> Never:
    raise ReleaseQualityError(code, message)


def _object(value: Any, code: str, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject(code, f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], code: str, label: str) -> None:
    if set(value) != expected:
        _reject(code, f"{label} must use the exact schema")


def _schema_version(value: Any, expected: int, code: str, label: str) -> None:
    if type(value) is not int or value != expected:
        _reject(code, f"{label} has an unsupported schema version")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _string_list(value: Any, code: str, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _reject(code, f"{label} must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _reject(code, f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        _reject(code, f"{label} must contain unique values")
    return value


def _validate_policy(policy: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    _exact_keys(
        policy,
        {
            "schemaVersion",
            "policyId",
            "families",
            "evidence",
            "qa",
            "security",
            "performance",
            "diagnostics",
            "exceptions",
        },
        "RQ001",
        "quality policy",
    )
    _schema_version(policy["schemaVersion"], 1, "RQ001", "quality policy")
    if policy["policyId"] != "ancestryllm-release-quality-v1":
        _reject("RQ001", "unsupported quality policy identity")
    families = _object(policy["families"], "RQ001", "policy families")
    if set(families) != set(FAMILIES):
        _reject("RQ001", "policy must assign all four quality families")
    for family in FAMILIES:
        record = _object(families[family], "RQ001", f"{family} family")
        _exact_keys(record, {"owner", "commands"}, "RQ001", f"{family} family")
        if not isinstance(record["owner"], str) or not record["owner"].strip():
            _reject("RQ001", f"{family} owner is missing")
        _string_list(record["commands"], "RQ001", f"{family} commands")

    evidence = _object(policy["evidence"], "RQ001", "evidence policy")
    _exact_keys(
        evidence,
        {"inputs", "approvalPath", "verifier"},
        "RQ001",
        "evidence policy",
    )
    inputs = evidence["inputs"]
    if not isinstance(inputs, list) or len(inputs) != 2:
        _reject("RQ001", "evidence policy must define the two required inputs")
    checked_inputs: list[tuple[str, str]] = []
    for item in inputs:
        record = _object(item, "RQ001", "evidence input")
        _exact_keys(record, {"artifact", "path"}, "RQ001", "evidence input")
        if any(
            not isinstance(record[field], str) or not record[field].strip()
            for field in ("artifact", "path")
        ):
            _reject("RQ001", "evidence input artifact and path are required")
        checked_inputs.append((record["artifact"], record["path"]))
    if tuple(checked_inputs) != REQUIRED_EVIDENCE_INPUTS:
        _reject("RQ001", "evidence policy must use the two required inputs")
    if evidence["approvalPath"] != "release-quality-approval.json":
        _reject("RQ001", "unsupported release-quality approval path")
    if evidence["verifier"] != "scripts/verify_release_quality.py":
        _reject("RQ001", "unsupported release-quality verifier")

    qa = _object(policy["qa"], "RQ001", "QA policy")
    _exact_keys(
        qa,
        {"readinessGates", "toolVersions", "desktopCoverage"},
        "RQ001",
        "QA policy",
    )
    _string_list(qa["readinessGates"], "RQ001", "readiness gates")
    tools = _object(qa["toolVersions"], "RQ001", "tool versions")
    _exact_keys(
        tools,
        {"python", "node", "pnpm", "vitest", "webdriverio"},
        "RQ001",
        "tool versions",
    )
    if any(not isinstance(version, str) or not version.strip() for version in tools.values()):
        _reject("RQ001", "tool versions must be non-empty strings")
    coverage = _object(qa["desktopCoverage"], "RQ001", "desktop coverage")
    _exact_keys(
        coverage,
        {"provider", "thresholds", "reviewedExclusions"},
        "RQ001",
        "desktop coverage",
    )
    if coverage["provider"] != "v8":
        _reject("RQ001", "desktop coverage provider must be v8")
    thresholds = _object(coverage["thresholds"], "RQ001", "coverage thresholds")
    _exact_keys(
        thresholds,
        {"branches", "functions", "lines", "statements"},
        "RQ001",
        "coverage thresholds",
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100
        for value in thresholds.values()
    ):
        _reject("RQ001", "coverage thresholds must be integer percentages")
    _string_list(
        coverage["reviewedExclusions"],
        "RQ001",
        "coverage exclusions",
    )

    security = _object(policy["security"], "RQ001", "security policy")
    _exact_keys(
        security,
        {"desktopReceiptGates"},
        "RQ001",
        "security policy",
    )
    receipt_gates = _string_list(
        security["desktopReceiptGates"],
        "RQ001",
        "desktop receipt gates",
    )

    performance = _object(policy["performance"], "RQ001", "performance policy")
    _exact_keys(
        performance,
        {"policyVersion", "method", "metrics", "targets"},
        "RQ001",
        "performance policy",
    )
    if (
        not isinstance(performance["policyVersion"], str)
        or not performance["policyVersion"].strip()
    ):
        _reject("RQ001", "performance policy version is missing")
    method = _object(performance["method"], "RQ001", "performance method")
    _exact_keys(
        method,
        {"command", "measurementSource", "validator", "packageBoundary"},
        "RQ001",
        "performance method",
    )
    if any(not isinstance(value, str) or not value.strip() for value in method.values()):
        _reject("RQ001", "performance method fields are required")
    if method["packageBoundary"] != "unpacked-native":
        _reject("RQ001", "performance method must validate the unpacked-native boundary")
    metrics = _string_list(performance["metrics"], "RQ001", "performance metrics")
    targets = performance["targets"]
    if not isinstance(targets, list) or not targets:
        _reject("RQ001", "performance targets are missing")
    runners: set[str] = set()
    for item in targets:
        record = _object(item, "RQ001", "performance target")
        _exact_keys(
            record,
            {
                "runner",
                "sidecarTarget",
                "expectedOs",
                "arch",
                "hostArch",
                "performanceCeilings",
            },
            "RQ001",
            "performance target",
        )
        for field in ("runner", "sidecarTarget", "expectedOs", "arch", "hostArch"):
            if not isinstance(record[field], str) or not record[field].strip():
                _reject("RQ001", f"performance target {field} is missing")
        if record["runner"] in runners:
            _reject("RQ001", f"duplicate performance runner {record['runner']}")
        runners.add(record["runner"])
        ceilings = _object(
            record["performanceCeilings"],
            "RQ001",
            f"{record['runner']} performance ceilings",
        )
        if set(ceilings) != set(metrics):
            _reject("RQ001", f"{record['runner']} performance ceilings are incomplete")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in ceilings.values()
        ):
            _reject("RQ001", f"{record['runner']} performance ceilings are invalid")

    diagnostics = _object(policy["diagnostics"], "RQ001", "diagnostics policy")
    _exact_keys(
        diagnostics,
        {
            "schemaVersion",
            "schemaPath",
            "schemaSha256",
            "safeCodesSource",
            "syntheticCanaryGate",
            "retention",
            "telemetry",
        },
        "RQ001",
        "diagnostics policy",
    )
    for field in (
        "schemaVersion",
        "schemaPath",
        "safeCodesSource",
        "syntheticCanaryGate",
    ):
        if not isinstance(diagnostics[field], str) or not diagnostics[field].strip():
            _reject("RQ001", f"diagnostics {field} is missing")
    if not isinstance(diagnostics["schemaSha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", diagnostics["schemaSha256"]
    ):
        _reject("RQ001", "diagnostics schema digest is invalid")
    repository_root = ROOT.resolve()
    schema_path = (repository_root / diagnostics["schemaPath"]).resolve()
    try:
        schema_path.relative_to(repository_root)
    except ValueError:
        _reject("RQ008", "diagnostics schema path must stay inside the repository")
    try:
        schema_digest = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    except OSError as error:
        _reject("RQ008", f"cannot read diagnostics schema: {error}")
    if schema_digest != diagnostics["schemaSha256"]:
        _reject("RQ008", "diagnostics schema digest does not match the checked-out schema")
    if diagnostics["syntheticCanaryGate"] not in receipt_gates:
        _reject("RQ001", "diagnostics canary is not a required desktop receipt gate")
    retention = _object(diagnostics["retention"], "RQ001", "diagnostics retention")
    _exact_keys(
        retention,
        {"maxBytesPerFile", "maxFilesPerComponent"},
        "RQ001",
        "diagnostics retention",
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in retention.values()
    ):
        _reject("RQ001", "diagnostics retention limits must be positive integers")
    if diagnostics["telemetry"] is not False:
        _reject("RQ001", "diagnostics telemetry must remain disabled")

    exceptions = policy["exceptions"]
    if not isinstance(exceptions, list):
        _reject("RQ009", "exceptions must be an array")
    seen: set[str] = set()
    for item in exceptions:
        record = _object(item, "RQ009", "exception")
        _exact_keys(
            record,
            {"id", "family", "gate", "owner", "approvedBy", "reason", "expires"},
            "RQ009",
            "exception",
        )
        for field in ("id", "gate", "owner", "approvedBy", "reason", "expires"):
            if not isinstance(record[field], str) or not record[field].strip():
                _reject("RQ009", f"exception {field} is missing")
        if record["id"] in seen:
            _reject("RQ009", f"duplicate exception {record['id']}")
        seen.add(record["id"])
        if record["family"] not in FAMILIES:
            _reject("RQ009", f"exception {record['id']} has an unknown family")
        if record["approvedBy"] == record["owner"]:
            _reject("RQ009", f"exception {record['id']} needs an independent approver")
        try:
            expiry = date.fromisoformat(record["expires"])
        except ValueError:
            _reject("RQ009", f"exception {record['id']} has an invalid expiry")
        if expiry < as_of:
            _reject("RQ009", f"exception {record['id']} expired before {as_of.isoformat()}")
        if expiry > as_of + timedelta(days=90):
            _reject("RQ009", f"exception {record['id']} expires more than 90 days from review")
    return copy.deepcopy(exceptions)


def _validate_readiness(
    readiness: dict[str, Any], version: str, commit: str, required: list[str]
) -> dict[str, str]:
    _exact_keys(
        readiness,
        {"schema_version", "release", "commit", "run_url", "gates"},
        "RQ003",
        "readiness evidence",
    )
    _schema_version(readiness["schema_version"], 1, "RQ003", "readiness evidence")
    if readiness["release"] != version:
        _reject("RQ003", "readiness evidence has the wrong release")
    if readiness["commit"] != commit:
        _reject("RQ002", "readiness evidence is not from the exact head")
    run_url = readiness["run_url"]
    if not isinstance(run_url, str) or not run_url.startswith(("https://", "http://")):
        _reject("RQ003", "readiness evidence has no run URL")
    gates = readiness["gates"]
    if not isinstance(gates, list):
        _reject("RQ004", "readiness gate inventory is missing")
    by_name: dict[str, dict[str, Any]] = {}
    for raw in gates:
        gate = _object(raw, "RQ004", "readiness gate")
        _exact_keys(
            gate,
            {"name", "status", "evidence_url"},
            "RQ004",
            "readiness gate",
        )
        name = gate["name"]
        if not isinstance(name, str) or name in by_name:
            _reject("RQ004", "readiness gate names must be unique strings")
        by_name[name] = gate
    if set(by_name) != set(required):
        missing = sorted(set(required) - set(by_name))
        unknown = sorted(set(by_name) - set(required))
        _reject("RQ004", f"readiness gate inventory mismatch; missing={missing}, unknown={unknown}")
    for name in required:
        gate = by_name[name]
        if gate["status"] != "verified":
            _reject("RQ004", f"readiness gate {name} is not verified")
        url = gate["evidence_url"]
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            _reject("RQ004", f"readiness gate {name} has no evidence URL")
    return {name: str(by_name[name]["evidence_url"]) for name in sorted(by_name)}


def _validate_digest(
    value: Any,
    code: str,
    label: str,
    *,
    nonempty: bool = False,
) -> dict[str, Any]:
    digest = _object(value, code, label)
    _exact_keys(digest, {"bytes", "sha256"}, code, label)
    if not isinstance(digest["sha256"], str) or not SHA256.fullmatch(digest["sha256"]):
        _reject(code, f"{label}.sha256 must be a lowercase SHA-256 digest")
    size = digest["bytes"]
    minimum = 1 if nonempty else 0
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not minimum <= size <= MAX_SAFE_INTEGER
    ):
        qualifier = "positive" if nonempty else "non-negative"
        _reject(code, f"{label}.bytes must be a {qualifier} safe integer")
    return digest


def _validate_receipt_summary(
    value: Any,
    required_gate: str,
    commit: str,
    allowed_gates: tuple[str, ...] | list[str],
    code: str,
    label: str,
) -> dict[str, Any]:
    summary = _object(value, code, label)
    receipt_keys = {
        "artifacts",
        "command",
        "gates",
        "gitHead",
        "headAfter",
        "headBefore",
        "kind",
        "result",
        "schemaVersion",
        "status",
        "workspace",
    }
    _exact_keys(summary, receipt_keys | {"receiptFile"}, code, label)
    _validate_digest(summary["receiptFile"], code, f"{label} receipt file")
    _schema_version(summary["schemaVersion"], 2, code, label)
    if summary["kind"] != "verification-receipt" or summary["status"] != "passed":
        _reject(code, f"{label} has an invalid receipt identity or result")
    if any(summary[field] != commit for field in ("gitHead", "headBefore", "headAfter")):
        _reject(code, f"{label} is not from the exact head")

    gates = summary["gates"]
    if (
        not isinstance(gates, list)
        or not gates
        or any(not isinstance(gate, str) for gate in gates)
        or gates != sorted(set(gates))
        or not set(gates) <= set(allowed_gates)
    ):
        _reject(code, f"{label} contains an invalid receipt gate inventory")
    if required_gate not in gates:
        _reject(code, f"{label} does not claim {required_gate}")

    command = _object(summary["command"], code, f"{label} command")
    _exact_keys(command, {"args", "executable", "shell"}, code, f"{label} command")
    if not isinstance(command["executable"], str) or not command["executable"]:
        _reject(code, f"{label} command executable is missing")
    if not isinstance(command["args"], list) or any(
        not isinstance(argument, str) for argument in command["args"]
    ):
        _reject(code, f"{label} command arguments are invalid")
    if not isinstance(command["shell"], bool):
        _reject(code, f"{label} command shell flag is invalid")

    result = _object(summary["result"], code, f"{label} result")
    _exact_keys(result, {"exitCode", "signal", "stderr", "stdout"}, code, f"{label} result")
    if result["exitCode"] != 0 or result["signal"] is not None:
        _reject(code, f"{label} command did not pass")
    _validate_digest(result["stdout"], code, f"{label} stdout")
    _validate_digest(result["stderr"], code, f"{label} stderr")

    artifacts = _object(summary["artifacts"], code, f"{label} artifacts")
    for name, artifact in artifacts.items():
        if not isinstance(name, str) or not ARTIFACT_NAME.fullmatch(name):
            _reject(code, f"{label} contains an invalid artifact name")
        _validate_digest(artifact, code, f"{label} artifact {name}")

    workspace = _object(summary["workspace"], code, f"{label} workspace")
    _exact_keys(
        workspace,
        {"after", "algorithm", "allowedOutputs", "before", "status"},
        code,
        f"{label} workspace",
    )
    if workspace["algorithm"] != "git-workspace-v1" or workspace["status"] != "unchanged":
        _reject(code, f"{label} workspace was not unchanged")
    allowed_outputs = workspace["allowedOutputs"]
    if (
        not isinstance(allowed_outputs, list)
        or any(not isinstance(path, str) or not path for path in allowed_outputs)
        or allowed_outputs != sorted(set(allowed_outputs))
    ):
        _reject(code, f"{label} workspace allowed outputs are invalid")
    before = _validate_digest(workspace["before"], code, f"{label} workspace before", nonempty=True)
    after = _validate_digest(workspace["after"], code, f"{label} workspace after", nonempty=True)
    if before != after:
        _reject(code, f"{label} workspace changed during verification")
    return summary


def _validate_receipt_inventory(
    evidence: dict[str, Any],
    required_gates: tuple[str, ...] | list[str],
    commit: str,
    code: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    gates = _object(evidence.get("gates"), code, f"{label} gates")
    if set(gates) != set(required_gates) or any(gates[gate] is not True for gate in gates):
        _reject(code, f"{label} gate inventory is incomplete or contains unknown gates")
    receipts = evidence.get("receipts")
    if not isinstance(receipts, dict) or set(receipts) != set(required_gates):
        _reject(code, f"{label} receipt inventory is incomplete or contains unknown receipts")
    return {
        gate: _validate_receipt_summary(
            receipts[gate],
            gate,
            commit,
            required_gates,
            code,
            f"{label} receipt {gate}",
        )
        for gate in required_gates
    }


def _validate_file_grant_mediation(value: Any, runner: str) -> None:
    label = f"{runner} file-grant evidence"
    evidence = _object(value, "RQ006", label)
    _exact_keys(
        evidence,
        {
            "schemaVersion",
            "kind",
            "status",
            "verificationOnlyDialogAdapter",
            "observations",
        },
        "RQ006",
        label,
    )
    _schema_version(evidence["schemaVersion"], 1, "RQ006", label)
    if (
        evidence["kind"] != "ancestryllm-packaged-file-grant-evidence"
        or evidence["status"] != "passed"
        or evidence["verificationOnlyDialogAdapter"] is not True
    ):
        _reject("RQ006", f"{label} identity or result is invalid")
    observations_label = f"{runner} file-grant observations"
    observations = _object(evidence["observations"], "RQ006", observations_label)
    _exact_keys(
        observations,
        set(FILE_GRANT_OBSERVATIONS),
        "RQ006",
        observations_label,
    )
    if observations != FILE_GRANT_OBSERVATIONS:
        _reject("RQ006", f"{observations_label} did not pass")


def _validate_fault_scenarios(value: Any, runner: str) -> None:
    scenarios = _object(value, "RQ006", f"{runner} fault scenarios")
    expected_names = {str(spec["name"]) for spec in FAULT_SCENARIOS.values()}
    if set(scenarios) != expected_names:
        _reject("RQ006", f"{runner} fault scenario inventory is incomplete or unknown")
    for spec in FAULT_SCENARIOS.values():
        name = str(spec["name"])
        label = f"{runner} {name} fault evidence"
        evidence = _object(scenarios[name], "RQ006", label)
        _exact_keys(
            evidence,
            {
                "schemaVersion",
                "kind",
                "scenario",
                "status",
                "packageCopy",
                "productionFaultHookUsed",
                "observations",
            },
            "RQ006",
            label,
        )
        _schema_version(evidence["schemaVersion"], 1, "RQ006", label)
        if (
            evidence["kind"] != "ancestryllm-packaged-fault-evidence"
            or evidence["scenario"] != name
            or evidence["status"] != "passed"
            or evidence["packageCopy"] is not True
            or evidence["productionFaultHookUsed"] is not False
        ):
            _reject("RQ006", f"{label} identity or result is invalid")
        observations_label = f"{label} observations"
        observations = _object(evidence["observations"], "RQ006", observations_label)
        expected_observations = spec["observations"]
        if not isinstance(expected_observations, dict):
            _reject("RQ006", f"{observations_label} contract is invalid")
        _exact_keys(
            observations,
            set(expected_observations),
            "RQ006",
            observations_label,
        )
        if observations != expected_observations:
            _reject("RQ006", f"{observations_label} did not pass")


def _validate_inspection(value: Any, runner: str, platform: str) -> None:
    label = f"{runner} package security inspection"
    inspection = _object(value, "RQ006", label)
    _exact_keys(
        inspection,
        {"schemaVersion", "kind", "platform", "fuses", "asar"},
        "RQ006",
        label,
    )
    _schema_version(inspection["schemaVersion"], 1, "RQ006", label)
    if (
        inspection["kind"] != "ancestryllm-desktop-package-security-inspection"
        or inspection["platform"] != platform
    ):
        _reject("RQ006", f"{label} identity is invalid")

    fuses_label = f"{runner} fuses"
    fuses = _object(inspection["fuses"], "RQ006", fuses_label)
    _exact_keys(fuses, {"status", "count", "items"}, "RQ006", fuses_label)
    items = fuses["items"]
    count = fuses["count"]
    if (
        fuses["status"] != "verified"
        or type(count) is not int
        or count <= 0
        or not isinstance(items, list)
        or len(items) != count
    ):
        _reject("RQ006", f"{fuses_label} evidence is invalid")
    for index, raw_item in enumerate(items):
        item_label = f"{fuses_label} item {index}"
        item = _object(raw_item, "RQ006", item_label)
        _exact_keys(
            item,
            {"name", "expected", "actual", "status"},
            "RQ006",
            item_label,
        )
        if (
            not isinstance(item["name"], str)
            or not item["name"].strip()
            or item["status"] != "verified"
            or item["actual"] != item["expected"]
        ):
            _reject("RQ006", f"{item_label} was not verified")

    asar_label = f"{runner} ASAR inspection"
    asar = _object(inspection["asar"], "RQ006", asar_label)
    _exact_keys(asar, {"presence", "integrity"}, "RQ006", asar_label)
    presence = _object(asar["presence"], "RQ006", f"{asar_label} presence")
    _exact_keys(presence, {"status"}, "RQ006", f"{asar_label} presence")
    if presence["status"] != "verified":
        _reject("RQ006", f"{asar_label} presence was not verified")
    integrity = _object(asar["integrity"], "RQ006", f"{asar_label} integrity")
    if platform == "darwin":
        _exact_keys(
            integrity,
            {"status", "scope", "algorithm", "hash"},
            "RQ006",
            f"{asar_label} integrity",
        )
        if (
            integrity["status"] != "verified"
            or not isinstance(integrity["scope"], str)
            or not integrity["scope"].strip()
            or integrity["algorithm"] != "SHA256"
            or not isinstance(integrity["hash"], str)
            or not SHA256.fullmatch(integrity["hash"])
        ):
            _reject("RQ006", f"{asar_label} integrity is invalid")
    else:
        _exact_keys(
            integrity,
            {"status", "scope", "reason"},
            "RQ006",
            f"{asar_label} integrity",
        )
        if integrity["status"] != "not-applicable" or any(
            not isinstance(integrity[field], str) or not integrity[field].strip()
            for field in ("scope", "reason")
        ):
            _reject("RQ006", f"{asar_label} integrity is invalid")


def _validate_performance(
    desktop: dict[str, Any], policy: dict[str, Any], commit: str
) -> list[dict[str, Any]]:
    performance = _object(policy["performance"], "RQ001", "performance policy")
    metrics = performance.get("metrics")
    targets_policy = performance.get("targets")
    if not isinstance(metrics, list) or not metrics or len(set(metrics)) != len(metrics):
        _reject("RQ001", "performance metrics are invalid")
    if not isinstance(targets_policy, list) or not targets_policy:
        _reject("RQ001", "performance targets are missing")
    expected = {row["runner"]: row for row in targets_policy}
    targets = desktop.get("targets")
    if not isinstance(targets, list):
        _reject("RQ006", "desktop target matrix is missing")
    actual = {row.get("runner"): row for row in targets if isinstance(row, dict)}
    if len(actual) != len(targets) or set(actual) != set(expected):
        _reject("RQ006", "desktop target matrix is incomplete or contains an unknown runner")

    summaries: list[dict[str, Any]] = []
    for runner in sorted(expected):
        row = actual[runner]
        contract = expected[runner]
        _exact_keys(row, TARGET_EVIDENCE_KEYS, "RQ006", f"{runner} target")
        _schema_version(row["schemaVersion"], 2, "RQ006", f"{runner} target")
        if row["kind"] != "target" or row["gitHead"] != commit:
            _reject("RQ006", f"{runner} target identity is not from the exact head")
        for field in ("sidecarTarget", "expectedOs", "arch", "hostArch"):
            if row[field] != contract[field]:
                _reject("RQ006", f"{runner} target matrix {field} mismatch")
        if row["actualOs"] != contract["expectedOs"]:
            _reject("RQ006", f"{runner} did not execute on the required OS")
        if row["packageBoundary"] != "unpacked-native" or row["platformValidated"] is not True:
            _reject("RQ006", f"{runner} target matrix boundary was not validated")
        if (
            row["artifactKind"] != "unpublished-unpacked-native"
            or row["signingVerified"] is not False
        ):
            _reject("RQ006", f"{runner} target artifact boundary is invalid")
        receipts = _validate_receipt_inventory(
            row,
            TARGET_RECEIPT_GATES,
            commit,
            "RQ006",
            f"{runner} target",
        )
        for gate, expected_artifacts in TARGET_RECEIPT_ARTIFACTS.items():
            receipt_artifacts = receipts[gate]["artifacts"]
            if set(receipt_artifacts) != expected_artifacts:
                expected_inventory = ", ".join(sorted(expected_artifacts)) or "no artifacts"
                _reject(
                    "RQ006",
                    f"{runner} target receipt {gate} artifact inventory must be exactly {expected_inventory}",
                )
            for name in sorted(expected_artifacts):
                _validate_digest(
                    receipt_artifacts[name],
                    "RQ006",
                    f"{runner} target receipt {gate} artifact {name}",
                    nonempty=True,
                )
        gates = _object(row["gates"], "RQ006", f"{runner} target gates")
        for flag, gate in TARGET_GATE_FLAGS.items():
            if row[flag] is not gates[gate]:
                _reject("RQ006", f"{runner} target {flag} does not match gate {gate}")
        if row["performancePassed"] is not True:
            _reject("RQ006", f"{runner} performance did not pass")

        artifacts = _object(row["artifacts"], "RQ006", f"{runner} target artifacts")
        target_artifact_names = {
            target_artifact for _, _, target_artifact in TARGET_ARTIFACT_BINDINGS
        }
        _exact_keys(
            artifacts,
            target_artifact_names,
            "RQ006",
            f"{runner} target artifacts",
        )
        for name in sorted(target_artifact_names):
            _validate_digest(
                artifacts[name],
                "RQ006",
                f"{runner} target artifact {name}",
                nonempty=True,
            )
        for gate, receipt_artifact, target_artifact in TARGET_ARTIFACT_BINDINGS:
            if receipts[gate]["artifacts"].get(receipt_artifact) != artifacts[target_artifact]:
                _reject(
                    "RQ006",
                    f"{runner} {target_artifact} is not bound to its successful receipt",
                )
        _validate_file_grant_mediation(row["fileGrantMediation"], runner)
        _validate_fault_scenarios(row["faultScenarios"], runner)
        platform = str(contract["sidecarTarget"]).split("-", maxsplit=1)[0]
        _validate_inspection(row["inspection"], runner, platform)

        evidence = _object(row["performance"], "RQ006", f"{runner} performance evidence")
        _exact_keys(
            evidence,
            {"policyVersion", "runner", "platform", "observed", "ceilings", "checks", "passed"},
            "RQ006",
            f"{runner} performance evidence",
        )
        if (
            evidence["policyVersion"] != performance["policyVersion"]
            or evidence["runner"] != runner
            or evidence["platform"] != contract["sidecarTarget"]
            or evidence["passed"] is not True
        ):
            _reject("RQ006", f"{runner} performance identity or result is invalid")
        ceilings = contract["performanceCeilings"]
        if evidence["ceilings"] != ceilings:
            _reject("RQ006", f"{runner} performance ceilings do not match policy")
        observed = evidence["observed"]
        checks = evidence["checks"]
        if not isinstance(observed, dict) or set(observed) != set(metrics):
            _reject("RQ006", f"{runner} performance observations are incomplete")
        if not isinstance(checks, dict) or set(checks) != set(metrics):
            _reject("RQ006", f"{runner} performance checks are incomplete")
        for metric in metrics:
            value = observed[metric]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                _reject("RQ006", f"{runner} performance metric {metric} is invalid")
            if (
                checks[metric]
                != {
                    "observed": value,
                    "ceiling": ceilings[metric],
                    "passed": True,
                }
                or value > ceilings[metric]
            ):
                _reject("RQ006", f"{runner} performance metric {metric} did not pass")
        summaries.append(
            {
                "runner": runner,
                "sidecarTarget": contract["sidecarTarget"],
                "observed": copy.deepcopy(observed),
                "ceilings": copy.deepcopy(ceilings),
            }
        )
    return summaries


def build_manifest(
    *,
    version: str,
    commit: str,
    readiness: dict[str, Any],
    desktop: dict[str, Any],
    policy: dict[str, Any],
    as_of: date | None = None,
) -> dict[str, Any]:
    """Validate inputs and return a deterministic release-quality approval manifest."""

    if not VERSION.fullmatch(version):
        _reject("RQ003", "release version must be X.Y.Z")
    if not SHA.fullmatch(commit):
        _reject("RQ002", "commit must be a lowercase full Git SHA")
    checked_policy = copy.deepcopy(_object(policy, "RQ001", "quality policy"))
    exceptions = _validate_policy(checked_policy, as_of or datetime.now(UTC).date())
    checked_readiness = _object(copy.deepcopy(readiness), "RQ003", "readiness evidence")
    checked_desktop = _object(copy.deepcopy(desktop), "RQ003", "desktop evidence")

    readiness_urls = _validate_readiness(
        checked_readiness,
        version,
        commit,
        checked_policy["qa"]["readinessGates"],
    )
    _exact_keys(
        checked_desktop,
        {
            "schemaVersion",
            "kind",
            "gitHead",
            "status",
            "platformValidated",
            "toolVersions",
            "targets",
            "security",
            "publicationRequirements",
        },
        "RQ003",
        "desktop aggregate evidence",
    )
    if checked_desktop["gitHead"] != commit:
        _reject("RQ002", "desktop evidence is not from the exact head")
    _schema_version(
        checked_desktop["schemaVersion"],
        2,
        "RQ003",
        "desktop aggregate evidence",
    )
    if (
        checked_desktop["kind"] != "aggregate"
        or checked_desktop["status"] != "passed"
        or checked_desktop["platformValidated"] is not True
        or checked_desktop["publicationRequirements"] != {"desktopInstaller": True}
    ):
        _reject("RQ003", "desktop aggregate identity or status is invalid")
    if checked_desktop["toolVersions"] != checked_policy["qa"]["toolVersions"]:
        _reject("RQ005", "desktop tool versions do not match the pinned policy")

    security = _object(checked_desktop["security"], "RQ007", "desktop security evidence")
    if "receipts" not in security:
        _reject("RQ007", "desktop security receipt inventory is missing")
    _exact_keys(
        security,
        {"schemaVersion", "kind", "gitHead", "gates", "receipts", "sbom"},
        "RQ007",
        "desktop security evidence",
    )
    _schema_version(
        security["schemaVersion"],
        2,
        "RQ007",
        "desktop security evidence",
    )
    if security["kind"] != "security" or security["gitHead"] != commit:
        _reject("RQ007", "desktop security evidence is not from the exact head")
    gates = _object(security.get("gates"), "RQ007", "desktop security gates")
    required_gates = checked_policy["security"]["desktopReceiptGates"]
    canary = checked_policy["diagnostics"]["syntheticCanaryGate"]
    if gates.get(canary) is not True:
        _reject("RQ008", f"diagnostics canary {canary} is missing or failed")
    for gate in required_gates:
        if gates.get(gate) is not True:
            _reject("RQ007", f"desktop quality gate {gate} is missing or failed")
    if set(gates) != set(required_gates):
        _reject("RQ007", "desktop security gate inventory contains unknown gates")
    security_receipts = _validate_receipt_inventory(
        security,
        required_gates,
        commit,
        "RQ007",
        "desktop security",
    )
    sbom = _validate_digest(security["sbom"], "RQ007", "desktop security SBOM")
    sbom_receipt = security_receipts["sbomGeneratedPassed"]
    if sbom_receipt["artifacts"].get("sbom") != sbom:
        _reject("RQ007", "desktop security SBOM is not bound to its verification receipt")

    performance = _validate_performance(checked_desktop, checked_policy, commit)
    diagnostics = checked_policy["diagnostics"]
    return {
        "schemaVersion": 1,
        "kind": "release-quality-approval",
        "policy": {
            "id": checked_policy["policyId"],
            "schemaVersion": checked_policy["schemaVersion"],
            "sha256": _canonical_sha256(checked_policy),
        },
        "release": version,
        "commit": commit,
        "status": "approved",
        "evidence": copy.deepcopy(checked_policy["evidence"]),
        "inputDigests": [
            {
                **checked_policy["evidence"]["inputs"][0],
                "sha256": _canonical_sha256(checked_readiness),
            },
            {
                **checked_policy["evidence"]["inputs"][1],
                "sha256": _canonical_sha256(checked_desktop),
            },
        ],
        "families": {
            "qa": {
                "status": "passed",
                "owner": checked_policy["families"]["qa"]["owner"],
                "commands": copy.deepcopy(checked_policy["families"]["qa"]["commands"]),
                "readinessGates": readiness_urls,
                "toolVersions": copy.deepcopy(checked_policy["qa"]["toolVersions"]),
                "desktopCoverage": copy.deepcopy(checked_policy["qa"]["desktopCoverage"]),
            },
            "security": {
                "status": "passed",
                "owner": checked_policy["families"]["security"]["owner"],
                "commands": copy.deepcopy(checked_policy["families"]["security"]["commands"]),
                "desktopReceiptGates": dict.fromkeys(required_gates, True),
            },
            "performance": {
                "status": "passed",
                "owner": checked_policy["families"]["performance"]["owner"],
                "commands": copy.deepcopy(checked_policy["families"]["performance"]["commands"]),
                "policyVersion": checked_policy["performance"]["policyVersion"],
                "method": copy.deepcopy(checked_policy["performance"]["method"]),
                "targets": performance,
            },
            "diagnostics": {
                "status": "passed",
                "owner": checked_policy["families"]["diagnostics"]["owner"],
                "commands": copy.deepcopy(checked_policy["families"]["diagnostics"]["commands"]),
                **copy.deepcopy(diagnostics),
            },
        },
        "exceptions": exceptions,
    }


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _reject("RQ010", f"cannot load {label}: {error}")
    return _object(value, "RQ010", label)


def main(argv: list[str] | None = None) -> int:
    """Validate release evidence from CLI arguments and write an approval."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--gates", required=True, type=Path)
    parser.add_argument("--desktop-evidence", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            version=args.version,
            commit=args.commit,
            readiness=_load(args.gates, "readiness gates"),
            desktop=_load(args.desktop_evidence, "desktop evidence"),
            policy=_load(args.policy, "quality policy"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ReleaseQualityError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
