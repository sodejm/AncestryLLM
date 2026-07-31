#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = [
#     "semgrep==1.170.0",
# ]
# ///
"""Run lockfile-pinned Semgrep with content-pinned registry rule bundles."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class RuleRevision:
    """One reviewed byte-for-byte representation of a rule bundle."""

    sha256: str
    size: int


@dataclass(frozen=True)
class RuleBundle:
    """An immutable Semgrep registry rule-bundle reference."""

    name: str
    url: str
    revisions: tuple[RuleRevision, ...]


RULE_BUNDLES = (
    RuleBundle(
        name="python",
        url="https://semgrep.dev/c/p/python",
        # Registry edges served these reviewed YAML and JSON encodings of the
        # same 151-rule set. All remain byte-pinned; every other response fails.
        revisions=(
            RuleRevision(
                sha256="6c5830b3c92994be81404c599c7d5595538aa8d6036fb8042eb3861e6608638d",
                size=487_962,
            ),
            RuleRevision(
                sha256="31c1dfa46e8ddd97f9ac98c607ddd77b20a2c3356d7ec987359961d47ec27035",
                size=487_962,
            ),
            RuleRevision(
                sha256="084e9272b4297bbdc7afcd0b8ece70816f2e9c9973639b26eab2c071456ccc6b",
                size=432_695,
            ),
        ),
    ),
    RuleBundle(
        name="secrets",
        url="https://semgrep.dev/c/p/secrets",
        revisions=(
            RuleRevision(
                sha256="139b35ad3442bc83d1f0864db82fa4fdc7e1f1ee4b5ac872bfbeb604c82c6518",
                size=89_772,
            ),
        ),
    ),
)

_DOWNLOAD_ATTEMPTS = 3


def _validate_rule_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "semgrep.dev"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"Semgrep rule URL is not trusted: {url}")


def download_rule_bundle(bundle: RuleBundle, destination: Path) -> None:
    """Download and verify one reviewed rule bundle before writing it."""
    _validate_rule_url(bundle.url)
    if not bundle.revisions:
        raise ValueError(f"Semgrep {bundle.name} rule bundle has no reviewed revisions")
    maximum_size = max(revision.size for revision in bundle.revisions)
    request = urllib.request.Request(  # noqa: S310
        bundle.url,
        headers={"User-Agent": "AncestryLLM-release-security-gate"},
    )
    observations: list[str] = []
    mismatch = "content hash"
    for _attempt in range(_DOWNLOAD_ATTEMPTS):
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            final_url = response.geturl()
            _validate_rule_url(final_url)
            if final_url != bundle.url:
                raise RuntimeError(f"Semgrep {bundle.name} rule bundle redirected unexpectedly")
            payload = response.read(maximum_size + 1)

        digest = hashlib.sha256(payload).hexdigest()
        observations.append(f"sha256={digest}, size={len(payload)}")
        matching_revisions = tuple(
            revision for revision in bundle.revisions if revision.sha256 == digest
        )
        if matching_revisions and any(
            len(payload) == revision.size for revision in matching_revisions
        ):
            destination.write_bytes(payload)
            return
        mismatch = "size" if matching_revisions else "content hash"

    observed = "; ".join(dict.fromkeys(observations))
    raise RuntimeError(
        f"Semgrep {bundle.name} rule bundle {mismatch} differs from the committed "
        f"release-security contract after {_DOWNLOAD_ATTEMPTS} attempts; observed {observed}"
    )


def _semgrep_executable() -> Path:
    executable = Path(sys.executable).with_name("semgrep")
    if not executable.is_file():
        raise RuntimeError(
            "Semgrep is not installed beside the active Python interpreter; "
            "run this script through its checked-in uv lock"
        )
    return executable


def run_scan(targets: list[str]) -> int:
    """Run Semgrep against targets using only verified local rule files."""
    with tempfile.TemporaryDirectory(prefix="ancestryllm-semgrep-") as temp_dir:
        config_paths: list[Path] = []
        for bundle in RULE_BUNDLES:
            config_path = Path(temp_dir) / f"{bundle.name}.yml"
            download_rule_bundle(bundle, config_path)
            config_paths.append(config_path)

        command = [
            str(_semgrep_executable()),
            "scan",
            "--error",
            "--metrics=off",
            "--disable-version-check",
        ]
        for config_path in config_paths:
            command.extend(("--config", str(config_path)))
        command.extend(targets)
        environment = os.environ.copy()
        environment["SEMGREP_LOG_FILE"] = str(Path(temp_dir) / "semgrep.log")
        environment["SEMGREP_SETTINGS_FILE"] = str(Path(temp_dir) / "settings.yml")
        completed = subprocess.run(command, check=False, env=environment)  # noqa: S603
        return completed.returncode


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Run lockfile-pinned Semgrep with content-pinned registry rule bundles.")
    )
    parser.add_argument("targets", nargs="+", help="Paths for Semgrep to scan")
    args = parser.parse_args(arguments)
    return run_scan(args.targets)


if __name__ == "__main__":
    raise SystemExit(main())
