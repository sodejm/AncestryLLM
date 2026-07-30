#!/usr/bin/env python3
"""Validate the repository-owned release milestone and tracker configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STABLE_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class ReleaseConfiguration:
    """Validated release control-plane identifiers."""

    release: str
    milestone_number: int
    milestone_title: str
    tracker_number: int
    tracker_label: str


def _require_exact_keys(value: dict[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing!r}")
        if unexpected:
            details.append(f"unexpected {unexpected!r}")
        raise ValueError(f"{location} keys are invalid: {', '.join(details)}")


def _require_mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a JSON object with string keys")
    return value


def _require_positive_integer(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{location} must be a positive integer")
    return value


def _require_text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{location} must be a non-empty trimmed string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{location} must not contain control characters")
    return value


def validate_release_configuration(
    payload: object,
    *,
    expected_version: str,
) -> ReleaseConfiguration:
    """Validate a release configuration against the requested stable version."""
    if STABLE_SEMVER.fullmatch(expected_version) is None:
        raise ValueError(f"requested version {expected_version!r} is not stable SemVer")

    root = _require_mapping(payload, "release configuration")
    _require_exact_keys(
        root,
        {"schema_version", "release", "milestone", "tracker"},
        "release configuration",
    )
    if root["schema_version"] != 1 or isinstance(root["schema_version"], bool):
        raise ValueError("release configuration schema_version must be 1")

    release = _require_text(root["release"], "release")
    if STABLE_SEMVER.fullmatch(release) is None:
        raise ValueError(f"configured release {release!r} is not stable SemVer")
    if release != expected_version:
        raise ValueError(
            f"configured release {release!r} does not match requested version {expected_version!r}"
        )

    milestone = _require_mapping(root["milestone"], "milestone")
    _require_exact_keys(milestone, {"number", "title"}, "milestone")
    tracker = _require_mapping(root["tracker"], "tracker")
    _require_exact_keys(tracker, {"number", "label"}, "tracker")

    return ReleaseConfiguration(
        release=release,
        milestone_number=_require_positive_integer(milestone["number"], "milestone.number"),
        milestone_title=_require_text(milestone["title"], "milestone.title"),
        tracker_number=_require_positive_integer(tracker["number"], "tracker.number"),
        tracker_label=_require_text(tracker["label"], "tracker.label"),
    )


def load_release_configuration(
    path: Path,
    *,
    expected_version: str,
) -> ReleaseConfiguration:
    """Load and validate a release configuration file."""
    with path.open(encoding="utf-8") as handle:
        return validate_release_configuration(
            json.load(handle),
            expected_version=expected_version,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        load_release_configuration(args.config, expected_version=args.version)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"release configuration verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
