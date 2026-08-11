"""Contract tests for the Version 1 security dependency Project gate."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "config" / "version-1-security-policy.json"
QUERY_PATH = ROOT / "config" / "release-project-query-v1.graphql"


def _load_module():
    path = ROOT / "scripts" / "verify_release_project.py"
    spec = importlib.util.spec_from_file_location("verify_release_project_security", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _field(name: str, value: str, *, iteration: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "field": {"name": name},
        "__typename": (
            "ProjectV2ItemFieldIterationValue"
            if iteration
            else "ProjectV2ItemFieldSingleSelectValue"
        ),
    }
    if iteration:
        result.update({"title": value, "iterationId": f"iteration-{value}"})
    else:
        result["name"] = value
    return result


def _blocker(number: int, *, state: str = "CLOSED") -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
    }


def _item(
    number: int,
    *,
    iteration: str,
    state: str = "OPEN",
    status: str = "Backlog",
    owners: tuple[str, ...] = ("sodejm",),
    blockers: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    return {
        "id": f"PVTITEM_{number}",
        "content": {
            "__typename": "Issue",
            "number": number,
            "state": state,
            "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
            "assignees": {
                "nodes": [{"login": owner} for owner in owners],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
            "blockedBy": {
                "nodes": list(blockers),
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        },
        "fieldValues": {
            "nodes": [
                _field("Release iteration", iteration, iteration=True),
                _field("Priority", "P0"),
                _field("Status", status),
                _field("Validation", "Needs validation"),
            ]
        },
    }


def _page(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "data": {
            "user": {
                "projectV2": {
                    "number": 2,
                    "title": "AncestryLLM Feature Releases",
                    "items": {
                        "nodes": items,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            }
        }
    }


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "sodejm/AncestryLLM",
        "project": {
            "owner": "sodejm",
            "number": 2,
            "title": "AncestryLLM Feature Releases",
        },
        "iteration_order": ["v0.6", "v0.7", "v0.8", "v0.9", "v1.0"],
        "required_issues": [
            {"number": 1, "iteration": "v0.6", "owner": "sodejm"},
            {"number": 2, "iteration": "v0.7", "owner": "sodejm"},
        ],
        "required_dependencies": [{"blocked": 2, "blocked_by": 1}],
        "evidence_consumer": {"issue": 99, "gate": "version-1-security-dependencies"},
    }


def _passing_payload() -> dict[str, object]:
    return _page(
        [
            _item(1, iteration="v0.6", state="CLOSED", status="Done"),
            _item(2, iteration="v0.7", blockers=(_blocker(1),)),
            _item(99, iteration="v1.0"),
        ]
    )


@pytest.fixture()
def verifier():
    return _load_module()


def test_versioned_security_gate_accepts_complete_project_graph(verifier) -> None:
    policy_payload = _policy()
    policy = verifier.parse_security_dependency_policy(policy_payload)

    report = verifier.verify_security_dependency_gate(_passing_payload(), policy)

    assert report == {
        "schema_version": 1,
        "status": "verified",
        "policy_schema_version": 1,
        "policy_sha256": policy.policy_sha256,
        "repository": "sodejm/AncestryLLM",
        "project": {
            "owner": "sodejm",
            "number": 2,
            "title": "AncestryLLM Feature Releases",
        },
        "checked_issues": [1, 2, 99],
        "required_dependencies": [{"blocked": 2, "blocked_by": 1}],
        "evidence_consumer": {
            "issue": 99,
            "gate": "version-1-security-dependencies",
        },
    }


@pytest.mark.parametrize(
    ("mutate", "code", "issues"),
    [
        (
            lambda payload: payload["data"]["user"]["projectV2"]["items"]["nodes"].pop(),
            "V1_CONSUMER_MISSING",
            "99",
        ),
        (
            lambda payload: payload["data"]["user"]["projectV2"]["items"]["nodes"][1][
                "fieldValues"
            ].update({"nodes": []}),
            "V1_PROJECT_FIELDS_MISSING",
            "2",
        ),
        (
            lambda payload: payload["data"]["user"]["projectV2"]["items"]["nodes"][1]["content"][
                "assignees"
            ].update({"nodes": []}),
            "V1_OWNER_MISSING",
            "2",
        ),
        (
            lambda payload: payload["data"]["user"]["projectV2"]["items"]["nodes"][1]["content"][
                "blockedBy"
            ].update({"nodes": []}),
            "V1_DEPENDENCY_MISSING",
            "1,2",
        ),
    ],
)
def test_security_gate_fails_closed_with_stable_sanitized_codes(
    verifier, mutate, code: str, issues: str
) -> None:
    payload = _passing_payload()
    mutate(payload)
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=rf"^\[{code}\] issues={issues}$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


def test_security_gate_rejects_dependency_cycles_before_other_state_checks(verifier) -> None:
    payload = _passing_payload()
    first, second, _consumer = payload["data"]["user"]["projectV2"]["items"]["nodes"]
    first["content"]["blockedBy"]["nodes"] = [_blocker(2, state="OPEN")]
    second["content"]["blockedBy"]["nodes"] = [_blocker(1)]
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_DEPENDENCY_CYCLE\] issues=1,2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


@pytest.mark.parametrize("connection", ["assignees", "blockedBy"])
def test_security_gate_rejects_incomplete_nested_pagination(verifier, connection: str) -> None:
    payload = _passing_payload()
    second = payload["data"]["user"]["projectV2"]["items"]["nodes"][1]
    second["content"][connection]["pageInfo"]["hasNextPage"] = True
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_PAGINATION_INCOMPLETE\] issues=2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


def test_security_gate_rejects_reversed_dependency_edges(verifier) -> None:
    payload = _passing_payload()
    first, second, _consumer = payload["data"]["user"]["projectV2"]["items"]["nodes"]
    first["content"]["blockedBy"]["nodes"] = [_blocker(2, state="OPEN")]
    second["content"]["blockedBy"]["nodes"] = []
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_DEPENDENCY_REVERSED\] issues=1,2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


def test_security_gate_rejects_contradictory_dependency_state(verifier) -> None:
    payload = _passing_payload()
    second = payload["data"]["user"]["projectV2"]["items"]["nodes"][1]
    second["content"]["blockedBy"]["nodes"] = [_blocker(1, state="OPEN")]
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_PROJECT_RESPONSE\] issues=1,2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


def test_security_gate_rejects_inverted_iteration_order(verifier) -> None:
    payload = _passing_payload()
    first = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]
    first["fieldValues"]["nodes"][0] = _field("Release iteration", "v0.9", iteration=True)
    policy_payload = _policy()
    policy_payload["required_issues"][0]["iteration"] = "v0.9"
    policy = verifier.parse_security_dependency_policy(policy_payload)

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_ITERATION_INVERTED\] issues=1,2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


def test_security_gate_rejects_prematurely_closed_dependent(verifier) -> None:
    payload = _passing_payload()
    first, second, _consumer = payload["data"]["user"]["projectV2"]["items"]["nodes"]
    first["content"]["state"] = "OPEN"
    second["content"]["state"] = "CLOSED"
    second["fieldValues"]["nodes"][2] = _field("Status", "Done")
    second["content"]["blockedBy"]["nodes"] = [_blocker(1, state="OPEN")]
    policy = verifier.parse_security_dependency_policy(_policy())

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_PREMATURE_CLOSURE\] issues=1,2$",
    ):
        verifier.verify_security_dependency_gate(payload, policy)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy.update({"unexpected": True}),
        lambda policy: policy.pop("required_dependencies"),
        lambda policy: policy.update({"schema_version": 2}),
        lambda policy: policy["required_issues"].append(
            copy.deepcopy(policy["required_issues"][0])
        ),
    ],
)
def test_security_policy_rejects_unknown_missing_or_duplicate_fields(verifier, mutation) -> None:
    policy = _policy()
    mutation(policy)

    with pytest.raises(
        verifier.SecurityDependencyVerificationError,
        match=r"^\[V1_POLICY_SCHEMA\] issues=none$",
    ):
        verifier.parse_security_dependency_policy(policy)


def test_repository_policy_encodes_issue_369_contract(verifier) -> None:
    policy = verifier.load_security_dependency_policy(POLICY_PATH)

    assert {(item.number, item.iteration) for item in policy.required_issues} == {
        *((number, "v0.6") for number in (*range(305, 311), 312, 363)),
        (311, "v0.7"),
        (364, "v0.9"),
        (365, "v0.9"),
    }
    assert {(edge.blocked_by, edge.blocked) for edge in policy.required_dependencies} == {
        (346, 363),
        (363, 348),
        (363, 349),
        *((number, dependent) for number in (349, 350, 351) for dependent in (364, 365)),
        *((number, 110) for number in range(101, 106)),
        (110, 111),
        (111, 112),
        (111, 113),
        *((number, 132) for number in (353, 364, 365)),
        *((132, number) for number in range(359, 363)),
    }
    assert policy.evidence_consumer.issue == 131


def test_release_workflows_share_versioned_query_policy_and_evidence_contract() -> None:
    query = QUERY_PATH.read_text(encoding="utf-8")
    assert "blockedBy(first: 100)" in query
    assert "assignees(first: 100)" in query
    assert "dependencyPageInfo: pageInfo" in query
    assert "assigneePageInfo: pageInfo" in query

    for relative_path in (
        ".github/workflows/release-project-gate-proof.yml",
        ".github/workflows/release-readiness.yml",
        ".github/workflows/release.yml",
    ):
        workflow = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'project_query="$(< config/release-project-query-v1.graphql)"' in workflow
        assert "--security-policy config/version-1-security-policy.json" in workflow
        assert "--security-report" in workflow
        assert "project_query='query(" not in workflow

    readiness = (ROOT / ".github/workflows/release-readiness.yml").read_text(encoding="utf-8")
    assert "version-1-security-dependencies" in readiness
    assert "version-1-security-gate" in readiness
    assert "--security-dependency-report" in readiness


def test_security_report_schema_is_consumed_by_release_evidence(verifier, tmp_path: Path) -> None:
    policy_payload = _policy()
    report = verifier.verify_security_dependency_gate(
        _passing_payload(), verifier.parse_security_dependency_policy(policy_payload)
    )
    report_path = tmp_path / "security-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    policy_path = tmp_path / "security-policy.json"
    policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

    evidence_path = ROOT / "scripts" / "create_release_evidence.py"
    spec = importlib.util.spec_from_file_location("create_release_evidence_security", evidence_path)
    assert spec is not None and spec.loader is not None
    evidence = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evidence
    spec.loader.exec_module(evidence)

    assert evidence._validate_security_dependency_report(report_path, policy_path) == report
    assert "version-1-security-dependencies" in evidence.REQUIRED_GATES
