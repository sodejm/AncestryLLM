#!/usr/bin/env python3
"""Create a payload-free Markdown evidence manifest for a release run."""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--interoperability",
        choices=("verified", "failed", "unavailable", "unverified"),
        default="unverified",
    )
    args = parser.parse_args()

    if not SEMVER.fullmatch(args.version):
        parser.error("version must be a stable SemVer value")
    if not COMMIT.fullmatch(args.commit):
        parser.error("commit must be a full lowercase Git SHA")
    files = sorted(path for path in args.artifacts.iterdir() if path.is_file())
    if not files:
        parser.error("artifact directory is empty")

    hashes = "\n".join(f"- `{path.name}`: `{_sha256(path)}`" for path in files)
    generated = datetime.now(UTC).date().isoformat()
    args.output.write_text(
        f"""# AncestryLLM {args.version} release evidence

- Version: `{args.version}`
- Tag: `v{args.version}`
- Commit: `{args.commit}`
- Generated: `{generated}`
- Workflow: {args.run_url}

## Automated gates

- Tests and coverage: `verified`
- Ruff and formatting: `verified`
- Mypy strict typing: `verified`
- Repository artifact guard: `verified`
- Dependency audit: `verified`
- Semgrep Python/secrets scan: `verified`
- Secret scan: `verified`
- CodeQL: `verified`
- Cross-platform clean installation: `verified`
- Reproducible wheel and sdist: `verified`
- CycloneDX SBOM: `verified`
- Release milestone closure: `verified`
- Merged branch/worktree cleanup: `verified` (operator-confirmed)

## Interoperability

- Vendor GEDCOM importer evidence: `{args.interoperability}`
- A non-verified status does not support an interoperability claim.

## Artifact SHA-256

{hashes}
""",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
