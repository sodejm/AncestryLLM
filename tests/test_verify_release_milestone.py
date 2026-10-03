"""Verify a release milestone is closed with no incomplete project work."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_release_milestone.py"
_SPEC = importlib.util.spec_from_file_location("verify_release_milestone", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _issue(
    number: int,
    *,
    labels: tuple[str, ...] = (),
    pull_request: bool = False,
) -> dict[str, object]:
    item: dict[str, object] = {
        "number": number,
        "title": f"Issue {number}",
        "labels": [{"name": label} for label in labels],
    }
    if pull_request:
        item["pull_request"] = {"url": f"https://example.invalid/pulls/{number}"}
    return item


def test_accepts_exactly_the_open_release_tracker() -> None:
    verifier.verify_open_items(
        [_issue(133, labels=("release-tracker",))],
        tracker_number=133,
        tracker_label="release-tracker",
    )


@pytest.mark.parametrize(
    ("items", "message"),
    (
        ([], "must remain open"),
        ([_issue(132)], "#132"),
        ([_issue(133)], "release-tracker"),
        ([_issue(133, labels=("release-tracker",), pull_request=True)], "pull request"),
        (
            [
                _issue(133, labels=("release-tracker",)),
                _issue(134, labels=("release-tracker",)),
            ],
            "#134",
        ),
        (
            [
                _issue(133, labels=("release-tracker",)),
                _issue(134, pull_request=True),
            ],
            "#134",
        ),
    ),
)
def test_rejects_every_other_open_item_or_invalid_exception(
    items: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        verifier.verify_open_items(
            items,
            tracker_number=133,
            tracker_label="release-tracker",
        )


def test_rejects_malformed_or_duplicate_api_items() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        verifier.verify_open_items(
            [
                _issue(133, labels=("release-tracker",)),
                _issue(133, labels=("release-tracker",)),
            ],
            tracker_number=133,
            tracker_label="release-tracker",
        )
