#!/usr/bin/env python3
"""Validate the repository-owned release Project configuration."""

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
    milestone_number: int | None = None
    milestone_title: str | None = None
    tracker_number: int | None = None
    tracker_label: str | None = None
    project_owner: str | None = None
    project_number: int | None = None
    project_title: str | None = None
    project_iteration: str | None = None
    project_priority: str | None = None
    project_priorities: tuple[str, ...] = ()
    project_status: str | None = None
    project_validation: str | None = None


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


def _require_text_list(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{location} must be a non-empty list")
    result = tuple(_require_text(item, f"{location}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ValueError(f"{location} values must be unique")
    return result


def validate_release_configuration(
    payload: object,
    *,
    expected_version: str,
) -> ReleaseConfiguration:
    """Validate a release configuration against the requested stable version."""
    if STABLE_SEMVER.fullmatch(expected_version) is None:
        raise ValueError(f"requested version {expected_version!r} is not stable SemVer")

    root = _require_mapping(payload, "release configuration")
    schema_version = root.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {1, 2, 3}:
        raise ValueError("release configuration schema_version must be 1, 2, or 3")

    release = _require_text(root.get("release"), "release")
    if STABLE_SEMVER.fullmatch(release) is None:
        raise ValueError(f"configured release {release!r} is not stable SemVer")
    if release != expected_version:
        raise ValueError(
            f"configured release {release!r} does not match requested version {expected_version!r}"
        )

    if schema_version == 1:
        _require_exact_keys(
            root,
            {"schema_version", "release", "milestone", "tracker"},
            "release configuration",
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

    _require_exact_keys(root, {"schema_version", "release", "project"}, "release configuration")
    project = _require_mapping(root["project"], "project")
    if schema_version == 2:
        _require_exact_keys(
            project,
            {"owner", "number", "title", "iteration", "priority", "status", "validation"},
            "project",
        )
    else:
        _require_exact_keys(
            project,
            {
                "owner",
                "number",
                "title",
                "iteration",
                "priorities",
                "milestone",
                "status",
                "validation",
            },
            "project",
        )
    common = {
        "release": release,
        "project_owner": _require_text(project.get("owner"), "project.owner"),
        "project_number": _require_positive_integer(project.get("number"), "project.number"),
        "project_title": _require_text(project.get("title"), "project.title"),
        "project_iteration": _require_text(project.get("iteration"), "project.iteration"),
        "project_status": _require_text(project.get("status"), "project.status"),
        "project_validation": _require_text(project.get("validation"), "project.validation"),
    }
    if schema_version == 2:
        priority = _require_text(project["priority"], "project.priority")
        return ReleaseConfiguration(
            **common,
            project_priority=priority,
            project_priorities=(priority,),
        )

    milestone = _require_mapping(project["milestone"], "project.milestone")
    _require_exact_keys(milestone, {"number", "title"}, "project.milestone")
    return ReleaseConfiguration(
        **common,
        project_priorities=_require_text_list(project["priorities"], "project.priorities"),
        milestone_number=_require_positive_integer(milestone["number"], "project.milestone.number"),
        milestone_title=_require_text(milestone["title"], "project.milestone.title"),
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
    """Run the verify release configuration command and return its exit status."""
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
