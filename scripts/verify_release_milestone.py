#!/usr/bin/env python3
"""Enforce the release milestone's open-item policy."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _item_labels(item: dict[str, Any], number: int) -> set[str]:
    raw_labels = item.get("labels")
    if not isinstance(raw_labels, list):
        raise ValueError(f"open item #{number} has malformed labels")
    labels: set[str] = set()
    for raw_label in raw_labels:
        if not isinstance(raw_label, dict):
            raise ValueError(f"open item #{number} has a malformed label")
        name = raw_label.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"open item #{number} has a malformed label name")
        labels.add(name)
    return labels


def verify_open_items(
    items: object,
    *,
    tracker_number: int,
    tracker_label: str,
) -> None:
    """Require exactly the designated labeled tracker issue to remain open."""
    if not isinstance(items, list):
        raise ValueError("GitHub API response must be a list of open milestone items")
    if tracker_number <= 0 or not tracker_label:
        raise ValueError("release tracker configuration is invalid")

    seen: set[int] = set()
    rejected: list[str] = []
    tracker_found = False
    for raw_item in items:
        if not isinstance(raw_item, dict):
            raise ValueError("GitHub API response contains a malformed open item")
        number = raw_item.get("number")
        title = raw_item.get("title")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("GitHub API response contains an invalid item number")
        if not isinstance(title, str):
            raise ValueError(f"open item #{number} has a malformed title")
        if number in seen:
            raise ValueError(f"GitHub API response contains duplicate open item #{number}")
        seen.add(number)
        labels = _item_labels(raw_item, number)
        is_pull_request = "pull_request" in raw_item

        if number == tracker_number:
            if is_pull_request:
                raise ValueError(
                    f"release tracker #{tracker_number} is a pull request, not an issue"
                )
            if tracker_label not in labels:
                raise ValueError(f"release tracker #{tracker_number} lacks label {tracker_label!r}")
            tracker_found = True
            continue

        kind = "pull request" if is_pull_request else "issue"
        rejected.append(f"{kind} #{number}")

    if rejected:
        raise ValueError("release milestone contains forbidden open items: " + ", ".join(rejected))
    if not tracker_found:
        raise ValueError(
            f"release tracker #{tracker_number} must remain open until publication is verified"
        )


def main() -> int:
    """Run the verify release milestone command and return its exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracker-number", required=True, type=int)
    parser.add_argument("--tracker-label", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        verify_open_items(
            payload,
            tracker_number=args.tracker_number,
            tracker_label=args.tracker_label,
        )
    except (json.JSONDecodeError, ValueError) as error:
        print(f"release milestone verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
