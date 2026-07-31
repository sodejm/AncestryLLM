#!/usr/bin/env python3
"""Fail closed unless a GitHub Project release iteration has cleared its gate."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any


class ProjectVerificationError(ValueError):
    """The Project response cannot prove that a release gate is clear."""


@dataclass(frozen=True)
class ProjectGate:
    """The release-specific Project fields that define a cleared gate."""

    owner: str
    number: int
    title: str
    iteration: str
    priority: str
    status: str
    validation: str


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


_REQUIRED_FIELDS = ("Release iteration", "Priority", "Status", "Validation")


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


def verify_project_gate(payload: object, gate: ProjectGate) -> None:
    """Verify all selected P0 items and their dependency closure from API data."""
    items: list[ProjectIssue] = []
    pages = _pages(payload)
    for page_index, page in enumerate(pages, start=1):
        project = _project_from_page(page)
        if project.get("number") != gate.number or project.get("title") != gate.title:
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
            issue = _issue_from_item(item)
            if issue is None:
                continue
            items.append(issue)

    target_items = [
        issue for issue in items if issue.fields.get("Release iteration") == gate.iteration
    ]
    seen_item_ids: set[str] = set()
    by_issue: dict[tuple[str, int], ProjectIssue] = {}
    for issue in target_items:
        _require_complete_target_fields(issue)
        if issue.item_id in seen_item_ids:
            raise ProjectVerificationError(f"duplicate Project item {issue.item_id!r}")
        seen_item_ids.add(issue.item_id)
        key = (issue.repository, issue.number)
        if key in by_issue:
            raise ProjectVerificationError(
                f"duplicate Project item data for issue #{issue.number} is inconsistent"
            )
        by_issue[key] = issue

    selected = [issue for issue in target_items if issue.fields["Priority"] == gate.priority]
    if not selected:
        raise ProjectVerificationError(
            f"GitHub Project has no {gate.priority} issues in iteration {gate.iteration!r}"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-owner", required=True)
    parser.add_argument("--project-number", required=True, type=int)
    parser.add_argument("--project-title", required=True)
    parser.add_argument("--iteration", required=True)
    parser.add_argument("--priority", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--validation", required=True)
    args = parser.parse_args()
    gate = ProjectGate(
        owner=args.project_owner,
        number=args.project_number,
        title=args.project_title,
        iteration=args.iteration,
        priority=args.priority,
        status=args.status,
        validation=args.validation,
    )
    try:
        verify_project_gate(json.load(sys.stdin), gate)
    except (json.JSONDecodeError, ProjectVerificationError) as error:
        print(f"release Project verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
