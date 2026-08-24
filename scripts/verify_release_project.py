#!/usr/bin/env python3
"""Fail closed unless a GitHub Project release iteration has cleared its gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProjectVerificationError(ValueError):
    """The Project response cannot prove that a release gate is clear."""


class SecurityDependencyVerificationError(ProjectVerificationError):
    """A Version 1 security dependency invariant could not be proven."""

    def __init__(self, code: str, issues: tuple[int, ...] = ()) -> None:
        issue_list = ",".join(str(number) for number in sorted(set(issues))) or "none"
        super().__init__(f"[{code}] issues={issue_list}")


@dataclass(frozen=True)
class ProjectGate:
    """The release-specific Project fields that define a cleared gate."""

    owner: str
    number: int
    title: str
    iteration: str
    priorities: tuple[str, ...]
    status: str
    validation: str
    repository_owner: str | None = None
    repository: str | None = None
    milestone_number: int | None = None
    milestone_title: str | None = None


@dataclass(frozen=True)
class ProjectIssue:
    """An issue represented by one Project item."""

    item_id: str
    number: int
    repository: str
    state: str
    fields: dict[str, str]
    field_errors: dict[str, str]
    blockers: tuple[dict[str, Any], ...]
    blockers_complete: bool
    owners: tuple[str, ...]
    owners_complete: bool


@dataclass(frozen=True)
class SecurityIssueRequirement:
    """One issue whose Version 1 release ownership and iteration are fixed."""

    number: int
    iteration: str
    owner: str


@dataclass(frozen=True)
class SecurityDependency:
    """One required native GitHub issue dependency."""

    blocked: int
    blocked_by: int


@dataclass(frozen=True)
class SecurityEvidenceConsumer:
    """The release issue and gate name that consume this verification."""

    issue: int
    gate: str


@dataclass(frozen=True)
class SecurityDependencyPolicy:
    """Reviewed Version 1 Project ownership and dependency policy."""

    schema_version: int
    policy_sha256: str
    repository: str
    project_owner: str
    project_number: int
    project_title: str
    iteration_order: tuple[str, ...]
    required_issues: tuple[SecurityIssueRequirement, ...]
    required_dependencies: tuple[SecurityDependency, ...]
    evidence_consumer: SecurityEvidenceConsumer


_REQUIRED_FIELDS = ("Release iteration", "Priority", "Status", "Validation")
_SECURITY_POLICY_FIELDS = {
    "schema_version",
    "repository",
    "project",
    "iteration_order",
    "required_issues",
    "required_dependencies",
    "evidence_consumer",
}
_ITERATION = re.compile(r"^(v\d+\.\d+)(?:\.\d+)?(?:\s+—\s+.+)?$")


def _require_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectVerificationError(f"{description} is malformed")
    return value


def _require_text(value: object, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectVerificationError(f"{description} is malformed")
    return value


def _require_positive_integer(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectVerificationError(f"{description} is malformed")
    return value


def _validate_project_gate(gate: ProjectGate) -> None:
    if not gate.priorities or any(
        not isinstance(priority, str) or not priority.strip() for priority in gate.priorities
    ):
        raise ProjectVerificationError("release priorities are malformed")
    if len(set(gate.priorities)) != len(gate.priorities):
        raise ProjectVerificationError("release priorities must be unique")
    milestone_values = (
        gate.repository_owner,
        gate.repository,
        gate.milestone_number,
        gate.milestone_title,
    )
    if any(value is not None for value in milestone_values) and not all(
        value is not None for value in milestone_values
    ):
        raise ProjectVerificationError(
            "repository owner, repository, and milestone coordinates must be supplied together"
        )
    if gate.repository_owner is not None and (
        not isinstance(gate.repository_owner, str)
        or not gate.repository_owner.strip()
        or "/" in gate.repository_owner
    ):
        raise ProjectVerificationError("release repository owner is malformed")
    if gate.repository is not None and (
        not isinstance(gate.repository, str)
        or not gate.repository.strip()
        or "/" in gate.repository
    ):
        raise ProjectVerificationError("release repository name is malformed")
    if gate.milestone_number is not None and (
        isinstance(gate.milestone_number, bool)
        or not isinstance(gate.milestone_number, int)
        or gate.milestone_number <= 0
    ):
        raise ProjectVerificationError("release milestone number is malformed")
    if gate.milestone_title is not None and (
        not isinstance(gate.milestone_title, str) or not gate.milestone_title.strip()
    ):
        raise ProjectVerificationError("release milestone title is malformed")


def _security_failure(code: str, *issues: int) -> SecurityDependencyVerificationError:
    return SecurityDependencyVerificationError(code, tuple(issues))


def _policy_object(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError
    return value


def _policy_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise ValueError
    return value.strip()


def _policy_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError
    return value


def _policy_graph_is_acyclic(dependencies: tuple[SecurityDependency, ...]) -> bool:
    adjacency: dict[int, set[int]] = {}
    for dependency in dependencies:
        adjacency.setdefault(dependency.blocked_by, set()).add(dependency.blocked)
        adjacency.setdefault(dependency.blocked, set())

    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(number: int) -> bool:
        if number in visiting:
            return False
        if number in visited:
            return True
        visiting.add(number)
        for dependent in adjacency[number]:
            if not visit(dependent):
                return False
        visiting.remove(number)
        visited.add(number)
        return True

    return all(visit(number) for number in adjacency)


def parse_security_dependency_policy(payload: object) -> SecurityDependencyPolicy:
    """Parse the exact, versioned Version 1 security dependency policy."""
    try:
        root = _policy_object(payload, _SECURITY_POLICY_FIELDS)
        policy_sha256 = hashlib.sha256(
            json.dumps(
                root,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            raise ValueError
        repository = _policy_text(root["repository"])
        if repository.count("/") != 1:
            raise ValueError

        project = _policy_object(root["project"], {"owner", "number", "title"})
        project_owner = _policy_text(project["owner"])
        project_number = _policy_integer(project["number"])
        project_title = _policy_text(project["title"])

        iteration_values = root["iteration_order"]
        if not isinstance(iteration_values, list) or not iteration_values:
            raise ValueError
        iteration_order = tuple(_policy_text(value) for value in iteration_values)
        if len(set(iteration_order)) != len(iteration_order):
            raise ValueError

        issue_values = root["required_issues"]
        if not isinstance(issue_values, list) or not issue_values:
            raise ValueError
        parsed_issues: list[SecurityIssueRequirement] = []
        for value in issue_values:
            issue = _policy_object(value, {"number", "iteration", "owner"})
            parsed_issues.append(
                SecurityIssueRequirement(
                    number=_policy_integer(issue["number"]),
                    iteration=_policy_text(issue["iteration"]),
                    owner=_policy_text(issue["owner"]),
                )
            )
        required_issues = tuple(parsed_issues)
        if len({item.number for item in required_issues}) != len(required_issues):
            raise ValueError
        if any(item.iteration not in iteration_order for item in required_issues):
            raise ValueError

        dependency_values = root["required_dependencies"]
        if not isinstance(dependency_values, list) or not dependency_values:
            raise ValueError
        parsed_dependencies: list[SecurityDependency] = []
        for value in dependency_values:
            dependency = _policy_object(value, {"blocked", "blocked_by"})
            parsed_dependencies.append(
                SecurityDependency(
                    blocked=_policy_integer(dependency["blocked"]),
                    blocked_by=_policy_integer(dependency["blocked_by"]),
                )
            )
        required_dependencies = tuple(parsed_dependencies)
        if any(item.blocked == item.blocked_by for item in required_dependencies):
            raise ValueError
        dependency_pairs = {(item.blocked, item.blocked_by) for item in required_dependencies}
        if len(dependency_pairs) != len(required_dependencies):
            raise ValueError
        if not _policy_graph_is_acyclic(required_dependencies):
            raise ValueError

        consumer = _policy_object(root["evidence_consumer"], {"issue", "gate"})
        evidence_consumer = SecurityEvidenceConsumer(
            issue=_policy_integer(consumer["issue"]),
            gate=_policy_text(consumer["gate"]),
        )
        if evidence_consumer.gate != "version-1-security-dependencies":
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise _security_failure("V1_POLICY_SCHEMA") from exc

    return SecurityDependencyPolicy(
        schema_version=1,
        policy_sha256=policy_sha256,
        repository=repository,
        project_owner=project_owner,
        project_number=project_number,
        project_title=project_title,
        iteration_order=iteration_order,
        required_issues=required_issues,
        required_dependencies=required_dependencies,
        evidence_consumer=evidence_consumer,
    )


def load_security_dependency_policy(path: Path) -> SecurityDependencyPolicy:
    """Load a UTF-8 JSON policy and fail with only the stable policy error code."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _security_failure("V1_POLICY_SCHEMA") from exc
    return parse_security_dependency_policy(payload)


def _pages(payload: object) -> list[dict[str, Any]]:
    pages = payload if isinstance(payload, list) else [payload]
    if not pages:
        raise ProjectVerificationError("GitHub Project response is empty")
    result: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        mapping = _require_mapping(page, f"GitHub Project response page {index}")
        if mapping.get("errors"):
            raise ProjectVerificationError("GitHub Project response has errors")
        result.append(mapping)
    return result


def _project_from_page(page: dict[str, Any]) -> dict[str, Any]:
    data = _require_mapping(page.get("data"), "GitHub Project response data")
    user = _require_mapping(data.get("user"), "GitHub Project owner")
    project = user.get("projectV2")
    if project is None:
        raise ProjectVerificationError("GitHub Project is not accessible")
    return _require_mapping(project, "GitHub Project")


def _fields(item: dict[str, Any], item_label: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse Project fields without rejecting items outside the release iteration."""
    values = item.get("fieldValues")
    if not isinstance(values, dict):
        return {}, {}
    nodes = values.get("nodes")
    if not isinstance(nodes, list):
        return {}, {}

    found: dict[str, str] = {}
    errors: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        field = node.get("field")
        if not isinstance(field, dict):
            continue
        field_name = field.get("name")
        if field_name not in _REQUIRED_FIELDS:
            continue
        if field_name in found:
            errors[field_name] = f"has duplicate required field {field_name!r}"
            continue
        if field_name == "Release iteration":
            value = node.get("title")
            iteration_id = node.get("iterationId")
            if (
                not isinstance(value, str)
                or not value
                or not isinstance(iteration_id, str)
                or not iteration_id
            ):
                errors[field_name] = f"has malformed required field {field_name!r}"
                continue
        else:
            value = node.get("name")
            if not isinstance(value, str) or not value:
                errors[field_name] = f"has malformed required field {field_name!r}"
                continue
        found[field_name] = value

    return found, errors


def _require_complete_target_fields(issue: ProjectIssue) -> None:
    """Fail closed only after an item explicitly identifies the target iteration."""
    item_label = f"Project issue #{issue.number}"
    for field_name in _REQUIRED_FIELDS:
        error = issue.field_errors.get(field_name)
        if error is not None:
            raise ProjectVerificationError(f"{item_label} {error}")
        if field_name not in issue.fields:
            raise ProjectVerificationError(f"{item_label} is missing required field {field_name!r}")


def _issue_from_item(item: object) -> ProjectIssue | None:
    mapping = _require_mapping(item, "GitHub Project item")
    content = mapping.get("content")
    if not isinstance(content, dict) or content.get("__typename") != "Issue":
        return None

    number = _require_positive_integer(content.get("number"), "GitHub Project issue number")
    item_label = f"Project issue #{number}"
    item_id = _require_text(mapping.get("id"), f"{item_label} id")
    state = _require_text(content.get("state"), f"{item_label} state")
    repository = _require_mapping(content.get("repository"), f"{item_label} repository")
    repository_name = _require_text(
        repository.get("nameWithOwner"), f"{item_label} repository name"
    )
    blocked_by = _require_mapping(content.get("blockedBy"), f"{item_label} dependencies")
    blockers = blocked_by.get("nodes")
    page_info = _require_mapping(
        blocked_by.get("dependencyPageInfo", blocked_by.get("pageInfo")),
        f"{item_label} dependency pagination",
    )
    if not isinstance(blockers, list) or not isinstance(page_info.get("hasNextPage"), bool):
        raise ProjectVerificationError(f"{item_label} dependencies are malformed")

    assignees = content.get("assignees")
    owners: tuple[str, ...] = ()
    owners_complete = False
    if assignees is not None:
        assignee_mapping = _require_mapping(assignees, f"{item_label} assignees")
        owner_nodes = assignee_mapping.get("nodes")
        assignee_page_info = _require_mapping(
            assignee_mapping.get(
                "assigneePageInfo",
                assignee_mapping.get("pageInfo"),
            ),
            f"{item_label} assignee pagination",
        )
        if not isinstance(owner_nodes, list) or not isinstance(
            assignee_page_info.get("hasNextPage"), bool
        ):
            raise ProjectVerificationError(f"{item_label} assignees are malformed")
        owners = tuple(
            _require_text(
                _require_mapping(owner, f"{item_label} assignee").get("login"),
                f"{item_label} assignee login",
            )
            for owner in owner_nodes
        )
        owners_complete = not assignee_page_info["hasNextPage"]

    fields, field_errors = _fields(mapping, item_label)
    return ProjectIssue(
        item_id=item_id,
        number=number,
        repository=repository_name,
        state=state,
        fields=fields,
        field_errors=field_errors,
        blockers=tuple(
            _require_mapping(blocker, f"{item_label} dependency") for blocker in blockers
        ),
        blockers_complete=not page_info["hasNextPage"],
        owners=owners,
        owners_complete=owners_complete,
    )


def _validate_cleared(issue: ProjectIssue, gate: ProjectGate, *, dependency: bool) -> None:
    prefix = "dependency" if dependency else "issue"
    if issue.state != "CLOSED":
        if dependency:
            raise ProjectVerificationError(f"open dependency #{issue.number}")
        raise ProjectVerificationError(f"issue #{issue.number} is still open")
    if issue.fields["Status"] != gate.status:
        raise ProjectVerificationError(f"{prefix} #{issue.number} Status must be {gate.status!r}")
    if issue.fields["Validation"] != gate.validation:
        raise ProjectVerificationError(
            f"{prefix} #{issue.number} Validation must be {gate.validation!r}"
        )


def _all_project_issues(
    payload: object,
    *,
    project_number: int,
    project_title: str,
    target_iteration: str | None = None,
) -> list[ProjectIssue]:
    """Collect every issue after proving Project coordinates and pagination."""
    items: list[ProjectIssue] = []
    pages = _pages(payload)
    for page_index, page in enumerate(pages, start=1):
        project = _project_from_page(page)
        if project.get("number") != project_number or project.get("title") != project_title:
            raise ProjectVerificationError("GitHub Project does not match release configuration")
        project_items = _require_mapping(project.get("items"), "GitHub Project items")
        nodes = project_items.get("nodes")
        page_info = _require_mapping(
            project_items.get("pageInfo"), "GitHub Project item pagination"
        )
        if not isinstance(nodes, list) or not isinstance(page_info.get("hasNextPage"), bool):
            raise ProjectVerificationError("GitHub Project items are malformed")
        if page_index == len(pages) and page_info["hasNextPage"]:
            raise ProjectVerificationError("GitHub Project item pagination is incomplete")
        for item in nodes:
            item_mapping = _require_mapping(item, "GitHub Project item")
            fields, _field_errors = _fields(item_mapping, "GitHub Project item")
            content = item_mapping.get("content")
            if (
                target_iteration is not None
                and fields.get("Release iteration") == target_iteration
                and (not isinstance(content, dict) or content.get("__typename") != "Issue")
            ):
                raise ProjectVerificationError(
                    "release iteration contains a draft or non-Issue item"
                )
            issue = _issue_from_item(item)
            if issue is not None:
                items.append(issue)
    return items


def _milestone_issues(
    payload: object,
    gate: ProjectGate,
) -> dict[tuple[str, int], str]:
    """Collect the configured repository milestone after proving exact coordinates."""
    if gate.repository is None or gate.milestone_number is None or gate.milestone_title is None:
        return {}

    expected_repository = f"{gate.repository_owner}/{gate.repository}"
    baseline: dict[tuple[str, int], str] | None = None
    for page in _pages(payload):
        data = _require_mapping(page.get("data"), "GitHub Project response data")
        repository = _require_mapping(data.get("repository"), "GitHub release milestone repository")
        if repository.get("nameWithOwner") != expected_repository:
            raise ProjectVerificationError(
                "GitHub release milestone repository does not match release configuration"
            )
        milestone = repository.get("milestone")
        if milestone is None:
            raise ProjectVerificationError("GitHub release milestone is not accessible")
        milestone_mapping = _require_mapping(milestone, "GitHub release milestone")
        if (
            milestone_mapping.get("number") != gate.milestone_number
            or milestone_mapping.get("title") != gate.milestone_title
        ):
            raise ProjectVerificationError(
                "GitHub release milestone does not match release configuration"
            )
        issues = _require_mapping(
            milestone_mapping.get("issues"), "GitHub release milestone issues"
        )
        nodes = issues.get("nodes")
        page_info = _require_mapping(issues.get("pageInfo"), "GitHub release milestone pagination")
        if not isinstance(nodes, list) or not isinstance(page_info.get("hasNextPage"), bool):
            raise ProjectVerificationError("GitHub release milestone issues are malformed")
        if page_info["hasNextPage"]:
            raise ProjectVerificationError("GitHub release milestone pagination is incomplete")

        pull_requests = _require_mapping(
            milestone_mapping.get("pullRequests"),
            "GitHub release milestone pull requests",
        )
        pull_request_nodes = pull_requests.get("nodes")
        pull_request_page_info = _require_mapping(
            pull_requests.get("pageInfo"),
            "GitHub release milestone pull request pagination",
        )
        if not isinstance(pull_request_nodes, list) or not isinstance(
            pull_request_page_info.get("hasNextPage"), bool
        ):
            raise ProjectVerificationError("GitHub release milestone pull requests are malformed")
        if pull_request_page_info["hasNextPage"]:
            raise ProjectVerificationError(
                "GitHub release milestone pull request pagination is incomplete"
            )
        for node in pull_request_nodes:
            pull_request = _require_mapping(node, "GitHub release milestone pull request")
            if pull_request.get("__typename") != "PullRequest":
                raise ProjectVerificationError(
                    "GitHub release milestone pull request data is malformed"
                )
            number = _require_positive_integer(
                pull_request.get("number"),
                "GitHub release milestone pull request number",
            )
            raise ProjectVerificationError(
                f"GitHub release milestone contains pull request #{number}"
            )

        current: dict[tuple[str, int], str] = {}
        for node in nodes:
            issue = _require_mapping(node, "GitHub release milestone item")
            if issue.get("__typename") != "Issue":
                raise ProjectVerificationError("GitHub release milestone contains a non-Issue item")
            number = _require_positive_integer(
                issue.get("number"), "GitHub release milestone issue number"
            )
            state = _require_text(
                issue.get("state"), f"GitHub release milestone issue #{number} state"
            )
            issue_repository = _require_mapping(
                issue.get("repository"),
                f"GitHub release milestone issue #{number} repository",
            )
            repository_name = _require_text(
                issue_repository.get("nameWithOwner"),
                f"GitHub release milestone issue #{number} repository name",
            )
            if repository_name != expected_repository:
                raise ProjectVerificationError(
                    f"GitHub release milestone issue #{number} belongs to another repository"
                )
            key = (repository_name, number)
            if key in current:
                raise ProjectVerificationError(
                    f"GitHub release milestone contains duplicate issue #{number}"
                )
            current[key] = state

        if baseline is None:
            baseline = current
        elif baseline != current:
            raise ProjectVerificationError(
                "GitHub release milestone data is inconsistent across Project pages"
            )

    return baseline or {}


def _format_issue_keys(
    keys: set[tuple[str, int]],
    *,
    expected_repository: str,
) -> str:
    return ", ".join(
        f"#{number}" if repository == expected_repository else f"{repository}#{number}"
        for repository, number in sorted(keys)
    )


def verify_project_schema(payload: object, gate: ProjectGate) -> list[ProjectIssue]:
    """Verify private Project access, pagination, and target-iteration field schema.

    This deliberately does not assert that the target iteration is release-ready.
    It is suitable for integration proof while implementation work remains open;
    ``verify_project_gate`` remains the strict release decision.
    """
    _validate_project_gate(gate)
    items = _all_project_issues(
        payload,
        project_number=gate.number,
        project_title=gate.title,
        target_iteration=gate.iteration,
    )

    target_items = [
        issue for issue in items if issue.fields.get("Release iteration") == gate.iteration
    ]
    seen_item_ids: set[str] = set()
    by_issue: dict[tuple[str, int], ProjectIssue] = {}
    for issue in target_items:
        _require_complete_target_fields(issue)
        if gate.milestone_number is not None and issue.fields["Priority"] not in gate.priorities:
            raise ProjectVerificationError(
                f"Project issue #{issue.number} has unconfigured Priority "
                f"{issue.fields['Priority']!r}"
            )
        if issue.item_id in seen_item_ids:
            raise ProjectVerificationError(f"duplicate Project item {issue.item_id!r}")
        seen_item_ids.add(issue.item_id)
        key = (issue.repository, issue.number)
        if key in by_issue:
            raise ProjectVerificationError(
                f"duplicate Project item data for issue #{issue.number} is inconsistent"
            )
        by_issue[key] = issue

    if gate.milestone_number is not None:
        milestone_items = _milestone_issues(payload, gate)
        project_keys = set(by_issue)
        milestone_keys = set(milestone_items)
        expected_repository = f"{gate.repository_owner}/{gate.repository}"
        milestone_only = milestone_keys - project_keys
        if milestone_only:
            raise ProjectVerificationError(
                "release scope mismatch; milestone-only issues: "
                + _format_issue_keys(
                    milestone_only,
                    expected_repository=expected_repository,
                )
            )
        project_only = project_keys - milestone_keys
        if project_only:
            raise ProjectVerificationError(
                "release scope mismatch; Project-only issues: "
                + _format_issue_keys(
                    project_only,
                    expected_repository=expected_repository,
                )
            )
        for key, issue in by_issue.items():
            if milestone_items[key] != issue.state:
                raise ProjectVerificationError(
                    f"release scope state is inconsistent for issue #{issue.number}"
                )

    return target_items


def verify_project_gate(payload: object, gate: ProjectGate) -> None:
    """Verify all configured release priorities and their dependency closure."""
    target_items = verify_project_schema(payload, gate)
    by_issue = {(issue.repository, issue.number): issue for issue in target_items}
    selected = [issue for issue in target_items if issue.fields["Priority"] in gate.priorities]
    for priority in gate.priorities:
        if not any(issue.fields["Priority"] == priority for issue in selected):
            raise ProjectVerificationError(
                f"GitHub Project has no {priority} issues in iteration {gate.iteration!r}"
            )

    for issue in selected:
        _validate_cleared(issue, gate, dependency=False)

    visited: set[tuple[str, int]] = set()

    def verify_dependencies(issue: ProjectIssue) -> None:
        key = (issue.repository, issue.number)
        if key in visited:
            return
        visited.add(key)
        if not issue.blockers_complete:
            raise ProjectVerificationError(
                f"dependency pagination is incomplete for issue #{issue.number}"
            )
        for blocker in issue.blockers:
            number = _require_positive_integer(blocker.get("number"), "GitHub dependency number")
            state = _require_text(blocker.get("state"), f"GitHub dependency #{number} state")
            repository = _require_mapping(
                blocker.get("repository"), f"GitHub dependency #{number} repository"
            )
            repository_name = _require_text(
                repository.get("nameWithOwner"), f"GitHub dependency #{number} repository name"
            )
            if state != "CLOSED":
                raise ProjectVerificationError(f"open dependency #{number}")
            dependency = by_issue.get((repository_name, number))
            if dependency is None:
                # A closed historical or externally tracked prerequisite does not
                # need to be carried into the current release iteration.
                continue
            _validate_cleared(dependency, gate, dependency=True)
            verify_dependencies(dependency)

    for issue in selected:
        verify_dependencies(issue)


def _iteration_key(title: str) -> str | None:
    match = _ITERATION.fullmatch(title)
    return match.group(1) if match is not None else None


def _security_project_issues(
    payload: object,
    policy: SecurityDependencyPolicy,
) -> dict[int, ProjectIssue]:
    try:
        issues = _all_project_issues(
            payload,
            project_number=policy.project_number,
            project_title=policy.project_title,
        )
    except ProjectVerificationError as exc:
        raise _security_failure("V1_PROJECT_RESPONSE") from exc

    item_ids: set[str] = set()
    by_number: dict[int, ProjectIssue] = {}
    duplicate_numbers: set[int] = set()
    for issue in issues:
        if issue.item_id in item_ids:
            raise _security_failure("V1_PROJECT_RESPONSE", issue.number)
        item_ids.add(issue.item_id)
        if issue.repository != policy.repository:
            continue
        if issue.number in by_number:
            duplicate_numbers.add(issue.number)
            continue
        by_number[issue.number] = issue
    if duplicate_numbers:
        raise SecurityDependencyVerificationError("V1_PROJECT_RESPONSE", tuple(duplicate_numbers))
    return by_number


def _security_fields(issue: ProjectIssue) -> tuple[str, str]:
    for field_name in ("Release iteration", "Status"):
        if field_name in issue.field_errors or field_name not in issue.fields:
            raise _security_failure("V1_PROJECT_FIELDS_MISSING", issue.number)
    return issue.fields["Release iteration"], issue.fields["Status"]


def _dependency_node(
    blocker: dict[str, Any],
    *,
    blocked: int,
) -> tuple[int, str, str]:
    try:
        number = _require_positive_integer(blocker.get("number"), "GitHub dependency number")
        state = _require_text(blocker.get("state"), f"GitHub dependency #{number} state")
        repository = _require_mapping(
            blocker.get("repository"), f"GitHub dependency #{number} repository"
        )
        repository_name = _require_text(
            repository.get("nameWithOwner"),
            f"GitHub dependency #{number} repository name",
        )
    except ProjectVerificationError as exc:
        raise _security_failure("V1_PROJECT_RESPONSE", blocked) from exc
    if state not in {"OPEN", "CLOSED"}:
        raise _security_failure("V1_PROJECT_RESPONSE", blocked, number)
    return number, state, repository_name


def _dependency_cycle_nodes(edges: set[tuple[int, int]]) -> tuple[int, ...]:
    """Return all nodes participating in a cycle in blocker-to-dependent edges."""
    adjacency: dict[int, set[int]] = {}
    for blocked, blocked_by in edges:
        adjacency.setdefault(blocked_by, set()).add(blocked)
        adjacency.setdefault(blocked, set())

    index = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    indices: dict[int, int] = {}
    lowlinks: dict[int, int] = {}
    cyclic: set[int] = set()

    def visit(number: int) -> None:
        nonlocal index
        indices[number] = index
        lowlinks[number] = index
        index += 1
        stack.append(number)
        on_stack.add(number)

        for dependent in adjacency[number]:
            if dependent not in indices:
                visit(dependent)
                lowlinks[number] = min(lowlinks[number], lowlinks[dependent])
            elif dependent in on_stack:
                lowlinks[number] = min(lowlinks[number], indices[dependent])

        if lowlinks[number] != indices[number]:
            return
        component: list[int] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == number:
                break
        if len(component) > 1 or (number, number) in edges:
            cyclic.update(component)

    for number in sorted(adjacency):
        if number not in indices:
            visit(number)
    return tuple(sorted(cyclic))


def verify_security_dependency_gate(
    payload: object,
    policy: SecurityDependencyPolicy,
) -> dict[str, Any]:
    """Verify Version 1 Project ownership, iteration, and dependency invariants."""
    by_number = _security_project_issues(payload, policy)
    consumer_number = policy.evidence_consumer.issue
    if consumer_number not in by_number:
        raise _security_failure("V1_CONSUMER_MISSING", consumer_number)

    required_issue_numbers = {item.number for item in policy.required_issues}
    dependency_numbers = {
        number
        for dependency in policy.required_dependencies
        for number in (dependency.blocked, dependency.blocked_by)
    }
    checked_numbers = required_issue_numbers | dependency_numbers | {consumer_number}
    missing_numbers = checked_numbers - by_number.keys()
    if missing_numbers:
        raise SecurityDependencyVerificationError("V1_ISSUE_MISSING", tuple(missing_numbers))

    iteration_indices = {
        iteration: position for position, iteration in enumerate(policy.iteration_order)
    }
    actual_iterations: dict[int, str] = {}
    invalid_iterations: set[int] = set()
    invalid_states: set[int] = set()
    for number in sorted(checked_numbers):
        issue = by_number[number]
        iteration_title, _status = _security_fields(issue)
        iteration = _iteration_key(iteration_title)
        if iteration is None or iteration not in iteration_indices:
            invalid_iterations.add(number)
        else:
            actual_iterations[number] = iteration
        if issue.state not in {"OPEN", "CLOSED"}:
            invalid_states.add(number)
    if invalid_iterations:
        raise SecurityDependencyVerificationError("V1_ITERATION_UNKNOWN", tuple(invalid_iterations))
    if invalid_states:
        raise SecurityDependencyVerificationError("V1_PROJECT_RESPONSE", tuple(invalid_states))

    iteration_mismatches = {
        requirement.number
        for requirement in policy.required_issues
        if actual_iterations[requirement.number] != requirement.iteration
    }
    if iteration_mismatches:
        raise SecurityDependencyVerificationError(
            "V1_ITERATION_MISMATCH", tuple(iteration_mismatches)
        )

    incomplete_owners = {
        requirement.number
        for requirement in policy.required_issues
        if not by_number[requirement.number].owners_complete
    }
    if incomplete_owners:
        raise SecurityDependencyVerificationError(
            "V1_PAGINATION_INCOMPLETE", tuple(incomplete_owners)
        )
    missing_owners = {
        requirement.number
        for requirement in policy.required_issues
        if requirement.owner.casefold()
        not in {owner.casefold() for owner in by_number[requirement.number].owners}
    }
    if missing_owners:
        raise SecurityDependencyVerificationError("V1_OWNER_MISSING", tuple(missing_owners))

    incomplete_dependencies = {
        number for number in checked_numbers if not by_number[number].blockers_complete
    }
    if incomplete_dependencies:
        raise SecurityDependencyVerificationError(
            "V1_PAGINATION_INCOMPLETE", tuple(incomplete_dependencies)
        )

    actual_edges: set[tuple[int, int]] = set()
    duplicate_edges: set[int] = set()
    inconsistent_states: set[int] = set()
    for blocked in sorted(checked_numbers):
        seen_blockers: set[tuple[str, int]] = set()
        for blocker in by_number[blocked].blockers:
            blocked_by, blocker_state, repository = _dependency_node(blocker, blocked=blocked)
            blocker_key = (repository, blocked_by)
            if blocker_key in seen_blockers:
                duplicate_edges.update((blocked, blocked_by))
                continue
            seen_blockers.add(blocker_key)
            if repository != policy.repository or blocked_by not in checked_numbers:
                continue
            actual_edges.add((blocked, blocked_by))
            if by_number[blocked_by].state != blocker_state:
                inconsistent_states.update((blocked, blocked_by))
    if duplicate_edges or inconsistent_states:
        raise SecurityDependencyVerificationError(
            "V1_PROJECT_RESPONSE", tuple(duplicate_edges | inconsistent_states)
        )

    cycle_nodes = _dependency_cycle_nodes(actual_edges)
    if cycle_nodes:
        raise SecurityDependencyVerificationError("V1_DEPENDENCY_CYCLE", cycle_nodes)

    required_edges = {
        (dependency.blocked, dependency.blocked_by) for dependency in policy.required_dependencies
    }
    reversed_edges = {
        number
        for blocked, blocked_by in required_edges
        if (blocked, blocked_by) not in actual_edges and (blocked_by, blocked) in actual_edges
        for number in (blocked, blocked_by)
    }
    if reversed_edges:
        raise SecurityDependencyVerificationError("V1_DEPENDENCY_REVERSED", tuple(reversed_edges))
    missing_edges = {
        number
        for blocked, blocked_by in required_edges - actual_edges
        for number in (blocked, blocked_by)
    }
    if missing_edges:
        raise SecurityDependencyVerificationError("V1_DEPENDENCY_MISSING", tuple(missing_edges))

    inverted_edges = {
        number
        for blocked, blocked_by in actual_edges
        if iteration_indices[actual_iterations[blocked_by]]
        > iteration_indices[actual_iterations[blocked]]
        for number in (blocked, blocked_by)
    }
    if inverted_edges:
        raise SecurityDependencyVerificationError("V1_ITERATION_INVERTED", tuple(inverted_edges))

    premature_closures = {
        number
        for blocked, blocked_by in actual_edges
        if by_number[blocked].state == "CLOSED" and by_number[blocked_by].state != "CLOSED"
        for number in (blocked, blocked_by)
    }
    if premature_closures:
        raise SecurityDependencyVerificationError("V1_PREMATURE_CLOSURE", tuple(premature_closures))

    return {
        "schema_version": 1,
        "status": "verified",
        "policy_schema_version": policy.schema_version,
        "policy_sha256": policy.policy_sha256,
        "repository": policy.repository,
        "project": {
            "owner": policy.project_owner,
            "number": policy.project_number,
            "title": policy.project_title,
        },
        "checked_issues": sorted(checked_numbers),
        "required_dependencies": [
            {"blocked": dependency.blocked, "blocked_by": dependency.blocked_by}
            for dependency in sorted(
                policy.required_dependencies,
                key=lambda item: (item.blocked, item.blocked_by),
            )
        ],
        "evidence_consumer": {
            "issue": consumer_number,
            "gate": policy.evidence_consumer.gate,
        },
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary_path.unlink()
        raise


def main() -> int:
    """Run the verify release project command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-owner", required=True)
    parser.add_argument("--project-number", required=True, type=int)
    parser.add_argument("--project-title", required=True)
    parser.add_argument("--iteration", required=True)
    parser.add_argument(
        "--priority",
        required=True,
        action="append",
        help="configured release priority; repeat for every accepted priority",
    )
    parser.add_argument("--status", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--repository-owner")
    parser.add_argument("--repository")
    parser.add_argument("--milestone-number", type=int)
    parser.add_argument("--milestone-title")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="validate Project access, pagination, coordinates, and target field schema only",
    )
    parser.add_argument(
        "--security-policy",
        type=Path,
        help="reviewed Version 1 security dependency policy",
    )
    parser.add_argument(
        "--security-report",
        type=Path,
        help="output path for the deterministic Version 1 security gate report",
    )
    args = parser.parse_args()
    if (args.security_policy is None) != (args.security_report is None):
        parser.error("--security-policy and --security-report must be supplied together")
    milestone_coordinates = (
        args.repository_owner,
        args.repository,
        args.milestone_number,
        args.milestone_title,
    )
    if any(value is not None for value in milestone_coordinates) and not all(
        value is not None for value in milestone_coordinates
    ):
        parser.error(
            "--repository-owner, --repository, --milestone-number, and --milestone-title "
            "must be supplied together"
        )
    gate = ProjectGate(
        owner=args.project_owner,
        number=args.project_number,
        title=args.project_title,
        iteration=args.iteration,
        priorities=tuple(args.priority),
        status=args.status,
        validation=args.validation,
        repository_owner=args.repository_owner,
        repository=args.repository,
        milestone_number=args.milestone_number,
        milestone_title=args.milestone_title,
    )
    try:
        payload = json.load(sys.stdin)
        if args.schema_only:
            verify_project_schema(payload, gate)
        else:
            verify_project_gate(payload, gate)
        if args.security_policy is not None:
            policy = load_security_dependency_policy(args.security_policy)
            if (
                policy.project_owner != gate.owner
                or policy.project_number != gate.number
                or policy.project_title != gate.title
            ):
                raise _security_failure("V1_POLICY_COORDINATES")
            report = verify_security_dependency_gate(payload, policy)
            try:
                _write_json_atomic(args.security_report, report)
            except OSError as exc:
                raise _security_failure("V1_REPORT_WRITE") from exc
    except (json.JSONDecodeError, ProjectVerificationError) as error:
        print(f"release Project verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
