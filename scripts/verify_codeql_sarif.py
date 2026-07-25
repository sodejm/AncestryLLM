#!/usr/bin/env python3
"""Require valid CodeQL SARIF evidence with no analysis results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_sarif(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 SARIF JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"SARIF root must be an object: {path.name}")
    return payload


def _result_count(payload: dict[str, Any], path: Path) -> int:
    if payload.get("version") != "2.1.0":
        raise ValueError(f"unsupported SARIF version: {path.name}")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"SARIF runs must be a non-empty list: {path.name}")

    result_count = 0
    for run_index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError(f"SARIF run {run_index} must be an object: {path.name}")
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        tool_name = driver.get("name") if isinstance(driver, dict) else None
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"SARIF run {run_index} must identify its tool driver: {path.name}")
        results = run.get("results", [])
        if not isinstance(results, list):
            raise ValueError(f"SARIF run {run_index} results must be a list: {path.name}")
        if not all(isinstance(result, dict) for result in results):
            raise ValueError(f"SARIF run {run_index} contains a non-object result: {path.name}")
        result_count += len(results)
    return result_count


def verify_codeql_sarif(directory: Path) -> tuple[Path, ...]:
    """Return verified SARIF paths or reject missing, invalid, or non-empty evidence."""

    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("CodeQL SARIF directory does not exist or is not a directory")
    sarif_files = tuple(sorted(directory.rglob("*.sarif")))
    if not sarif_files:
        raise ValueError("CodeQL analysis produced no SARIF files")

    total_results = 0
    for path in sarif_files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"CodeQL SARIF evidence is not a regular file: {path.name}")
        total_results += _result_count(_load_sarif(path), path)
    if total_results:
        raise ValueError(f"CodeQL SARIF contains {total_results} undispositioned result(s)")
    return sarif_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        verified = verify_codeql_sarif(args.directory)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Verified {len(verified)} zero-result CodeQL SARIF file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
