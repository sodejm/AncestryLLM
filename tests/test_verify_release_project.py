"""Tests for the Project-native release gate verifier."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import cast

import pytest


def _load_module():
    path = Path("scripts/verify_release_project.py")
    spec = importlib.util.spec_from_file_location("verify_release_project", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _field(name: str, value: str, *, iteration: bool = False) -> dict[str, object]:
    field_type = "ProjectV2IterationField" if iteration else "ProjectV2SingleSelectField"
    result: dict[str, object] = {
        "field": {"name": name, "__typename": field_type},
        "__typename": (
            "ProjectV2ItemFieldIterationValue"
            if iteration
            else "ProjectV2ItemFieldSingleSelectValue"
        ),
    }
    if iteration:
        result.update({"title": value, "iterationId": "iteration-v050"})
    else:
        result["name"] = value
    return result


def _item(
    number: int,
    *,
    state: str = "CLOSED",
    status: str = "Done",
    validation: str = "Verified",
    priority: str = "P0",
    iteration: str = "v0.5.0 — Foundation",
    blockers: tuple[dict[str, object], ...] = (),
    blockers_have_next_page: bool = False,
) -> dict[str, object]:
    return {
        "id": f"PVTITEM_{number}",
        "content": {
            "__typename": "Issue",
            "number": number,
            "state": state,
            "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
            "blockedBy": {
                "nodes": list(blockers),
                "pageInfo": {
                    "hasNextPage": blockers_have_next_page,
                    "endCursor": "dependency-cursor" if blockers_have_next_page else None,
                },
            },
        },
        "fieldValues": {
            "nodes": [
                _field("Release iteration", iteration, iteration=True),
                _field("Priority", priority),
                _field("Status", status),
                _field("Validation", validation),
            ]
        },
    }


def _page(items: list[dict[str, object]], *, has_next_page: bool = False) -> dict[str, object]:
    return {
        "data": {
            "user": {
                "projectV2": {
                    "number": 2,
                    "title": "AncestryLLM Feature Releases",
                    "items": {
                        "nodes": items,
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": "cursor" if has_next_page else None,
                        },
                    },
                }
            }
        }
    }


def _milestone_issue(
    number: int,
    *,
    state: str = "CLOSED",
    repository: str = "sodejm/AncestryLLM",
    typename: str = "Issue",
) -> dict[str, object]:
    return {
        "__typename": typename,
        "number": number,
        "state": state,
        "repository": {"nameWithOwner": repository},
    }


def _release_page(
    items: list[dict[str, object]],
    milestone_items: list[dict[str, object]],
    *,
    milestone_number: int = 6,
    milestone_title: str = "0.7.0 Genealogy Workflows",
    milestone_has_next_page: bool = False,
) -> dict[str, object]:
    page = _page(items)
    page["data"]["repository"] = {
        "nameWithOwner": "sodejm/AncestryLLM",
        "milestone": {
            "number": milestone_number,
            "title": milestone_title,
            "issues": {
                "nodes": milestone_items,
                "pageInfo": {
                    "hasNextPage": milestone_has_next_page,
                    "endCursor": "milestone-cursor" if milestone_has_next_page else None,
                },
            },
        },
    }
    return page


@pytest.fixture()
def verifier():
    return _load_module()


@pytest.fixture()
def gate(verifier):
    return verifier.ProjectGate(
        owner="sodejm",
        number=2,
        title="AncestryLLM Feature Releases",
        iteration="v0.5.0 — Foundation",
        priorities=("P0",),
        status="Done",
        validation="Verified",
    )


@pytest.fixture()
def release_gate(verifier):
    return verifier.ProjectGate(
        owner="sodejm",
        number=2,
        title="AncestryLLM Feature Releases",
        iteration="v0.7.0 — Genealogy workflows",
        priorities=("P0", "P1"),
        status="Done",
        validation="Verified",
        repository="AncestryLLM",
        milestone_number=6,
        milestone_title="0.7.0 Genealogy Workflows",
    )


def test_accepts_complete_paginated_project_items(verifier, gate):
    pages = [_page([_item(202)], has_next_page=True), _page([_item(224)])]

    verifier.verify_project_gate(pages, gate)


def test_rejects_non_text_release_priority(verifier, gate):
    malformed = verifier.ProjectGate(
        owner=gate.owner,
        number=gate.number,
        title=gate.title,
        iteration=gate.iteration,
        priorities=cast("tuple[str, ...]", (None,)),
        status=gate.status,
        validation=gate.validation,
    )

    with pytest.raises(verifier.ProjectVerificationError, match="priorities are malformed"):
        verifier.verify_project_schema(_page([_item(202)]), malformed)


def test_rejects_unfinished_project_item_pagination(verifier, gate):
    with pytest.raises(verifier.ProjectVerificationError, match="pagination is incomplete"):
        verifier.verify_project_gate(_page([_item(202)], has_next_page=True), gate)


def test_accepts_complete_project_schema_while_target_p0_is_not_release_ready(verifier, gate):
    not_ready = _item(99, state="OPEN", status="In progress", validation="Pending")

    verifier.verify_project_schema(_page([not_ready]), gate)

    with pytest.raises(verifier.ProjectVerificationError, match="issue #99 is still open"):
        verifier.verify_project_gate(_page([not_ready]), gate)


def test_schema_validation_rejects_incomplete_target_iteration_fields(verifier, gate):
    item = _item(99)
    item["fieldValues"]["nodes"] = [
        field for field in item["fieldValues"]["nodes"] if field["field"]["name"] != "Validation"
    ]

    with pytest.raises(
        verifier.ProjectVerificationError, match="missing required field 'Validation'"
    ):
        verifier.verify_project_schema(_page([item]), gate)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"data": {"user": {"projectV2": None}}}, "not accessible"),
        ({"errors": [{"message": "Resource not accessible"}]}, "response has errors"),
    ],
)
def test_rejects_missing_project_access_or_malformed_responses(verifier, gate, payload, expected):
    with pytest.raises(verifier.ProjectVerificationError, match=expected):
        verifier.verify_project_gate(payload, gate)

    with pytest.raises(verifier.ProjectVerificationError, match=expected):
        verifier.verify_project_schema(payload, gate)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda item: item["fieldValues"].update(
                {
                    "nodes": [
                        field
                        for field in item["fieldValues"]["nodes"]
                        if field["field"]["name"] != "Status"
                    ]
                }
            ),
            "missing required field 'Status'",
        ),
        (
            lambda item: item["fieldValues"]["nodes"].append(_field("Status", "Done")),
            "duplicate required field 'Status'",
        ),
        (
            lambda item: item["fieldValues"]["nodes"].__setitem__(2, {"field": {"name": "Status"}}),
            "malformed required field 'Status'",
        ),
    ],
)
def test_rejects_absent_duplicate_or_malformed_required_fields(verifier, gate, mutate, expected):
    item = _item(202)
    mutate(item)
    with pytest.raises(verifier.ProjectVerificationError, match=expected):
        verifier.verify_project_gate(_page([item]), gate)


def test_ignores_legacy_project_items_without_target_iteration_fields(verifier, gate):
    legacy = _item(60, state="OPEN")
    legacy["fieldValues"] = {"nodes": [_field("Priority", "P0")]}

    verifier.verify_project_gate(_page([_item(224), legacy]), gate)


def test_rejects_target_iteration_item_with_incomplete_fields_even_when_not_p0(verifier, gate):
    target_p1 = _item(202, priority="P1")
    target_p1["fieldValues"]["nodes"] = [
        field
        for field in target_p1["fieldValues"]["nodes"]
        if field["field"]["name"] != "Validation"
    ]

    with pytest.raises(
        verifier.ProjectVerificationError, match="missing required field 'Validation'"
    ):
        verifier.verify_project_gate(_page([_item(224), target_p1]), gate)


@pytest.mark.parametrize(
    "item, expected",
    [
        (_item(202, state="OPEN"), "issue #202 is still open"),
        (_item(202, status="In progress"), "Status must be 'Done'"),
        (_item(202, validation="Accepted"), "Validation must be 'Verified'"),
    ],
)
def test_rejects_incomplete_p0_items(verifier, gate, item, expected):
    with pytest.raises(verifier.ProjectVerificationError, match=expected):
        verifier.verify_project_gate(_page([item]), gate)


def test_allows_open_non_p0_post_release_item_without_issue_exception(verifier, gate):
    verifier.verify_project_gate(
        _page(
            [
                _item(224),
                _item(
                    202, priority="P1", state="OPEN", status="In progress", validation="Accepted"
                ),
            ]
        ),
        gate,
    )


def test_rejects_open_dependency_even_when_it_is_not_a_project_item(verifier, gate):
    open_blocker = {
        "number": 224,
        "state": "OPEN",
        "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
    }
    with pytest.raises(verifier.ProjectVerificationError, match="open dependency #224"):
        verifier.verify_project_gate(_page([_item(202, blockers=(open_blocker,))]), gate)


def test_accepts_closed_historical_dependency_outside_selected_iteration(verifier, gate):
    closed_blocker = {
        "number": 13,
        "state": "CLOSED",
        "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
    }

    verifier.verify_project_gate(_page([_item(202, blockers=(closed_blocker,))]), gate)


def test_rejects_incomplete_dependency_pagination(verifier, gate):
    with pytest.raises(verifier.ProjectVerificationError, match="dependency pagination"):
        verifier.verify_project_gate(_page([_item(202, blockers_have_next_page=True)]), gate)


def test_rejects_dependency_that_is_not_done_and_verified(verifier, gate):
    blocker = {
        "number": 224,
        "state": "CLOSED",
        "repository": {"nameWithOwner": "sodejm/AncestryLLM"},
    }
    with pytest.raises(verifier.ProjectVerificationError, match="Validation must be 'Verified'"):
        verifier.verify_project_gate(
            _page([_item(202, blockers=(blocker,)), _item(224, validation="Accepted")]),
            gate,
        )


def test_rejects_duplicate_project_item_ids(verifier, gate):
    with pytest.raises(verifier.ProjectVerificationError, match="duplicate Project item"):
        verifier.verify_project_gate(_page([_item(202), _item(202)]), gate)


def test_rejects_inconsistent_duplicate_issue_data(verifier, gate):
    duplicate = _item(202)
    duplicate["id"] = "PVTITEM_duplicate"
    with pytest.raises(verifier.ProjectVerificationError, match="inconsistent"):
        verifier.verify_project_gate(_page([_item(202), duplicate]), gate)


def test_accepts_all_configured_priorities_with_exact_milestone_parity(verifier, release_gate):
    items = [
        _item(202, iteration=release_gate.iteration, priority="P0"),
        _item(224, iteration=release_gate.iteration, priority="P1"),
    ]
    milestone_items = [_milestone_issue(202), _milestone_issue(224)]

    verifier.verify_project_gate(_release_page(items, milestone_items), release_gate)


def test_rejects_unready_issue_in_any_configured_priority(verifier, release_gate):
    items = [
        _item(202, iteration=release_gate.iteration, priority="P0"),
        _item(
            224,
            iteration=release_gate.iteration,
            priority="P1",
            state="OPEN",
            status="In progress",
            validation="Pending",
        ),
    ]

    with pytest.raises(verifier.ProjectVerificationError, match="issue #224 is still open"):
        verifier.verify_project_gate(
            _release_page(items, [_milestone_issue(202), _milestone_issue(224, state="OPEN")]),
            release_gate,
        )


def test_rejects_unconfigured_target_priority(verifier, release_gate):
    item = _item(202, iteration=release_gate.iteration, priority="P2")

    with pytest.raises(verifier.ProjectVerificationError, match="unconfigured Priority 'P2'"):
        verifier.verify_project_schema(
            _release_page([item], [_milestone_issue(202)]),
            release_gate,
        )


def test_rejects_non_issue_in_target_iteration(verifier, release_gate):
    draft = _item(202, iteration=release_gate.iteration)
    draft["content"] = {"__typename": "DraftIssue", "title": "untracked work"}

    with pytest.raises(verifier.ProjectVerificationError, match="non-Issue item"):
        verifier.verify_project_schema(
            _release_page([draft], []),
            release_gate,
        )


@pytest.mark.parametrize(
    ("items", "milestone_items", "expected"),
    (
        (
            [_item(202, iteration="v0.7.0 — Genealogy workflows")],
            [_milestone_issue(202), _milestone_issue(224)],
            "milestone-only issues: #224",
        ),
        (
            [
                _item(202, iteration="v0.7.0 — Genealogy workflows"),
                _item(224, iteration="v0.7.0 — Genealogy workflows"),
            ],
            [_milestone_issue(202)],
            "Project-only issues: #224",
        ),
    ),
)
def test_rejects_project_and_milestone_issue_set_mismatch(
    verifier, release_gate, items, milestone_items, expected
):
    with pytest.raises(verifier.ProjectVerificationError, match=expected):
        verifier.verify_project_schema(
            _release_page(items, milestone_items),
            release_gate,
        )


def test_rejects_pull_request_in_release_milestone(verifier, release_gate):
    item = _item(202, iteration=release_gate.iteration)

    with pytest.raises(verifier.ProjectVerificationError, match="non-Issue item"):
        verifier.verify_project_schema(
            _release_page([item], [_milestone_issue(202, typename="PullRequest")]),
            release_gate,
        )


def test_rejects_incomplete_milestone_pagination(verifier, release_gate):
    item = _item(202, iteration=release_gate.iteration)

    with pytest.raises(verifier.ProjectVerificationError, match="milestone pagination"):
        verifier.verify_project_schema(
            _release_page(
                [item],
                [_milestone_issue(202)],
                milestone_has_next_page=True,
            ),
            release_gate,
        )
