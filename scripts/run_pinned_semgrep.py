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
class RuleBundle:
    """An immutable Semgrep registry rule-bundle reference."""

    name: str
    url: str
    sha256: str
    size: int


RULE_BUNDLES = (
    RuleBundle(
        name="python",
        url="https://semgrep.dev/c/p/python",
        sha256="31c1dfa46e8ddd97f9ac98c607ddd77b20a2c3356d7ec987359961d47ec27035",
        size=487_962,
    ),
    RuleBundle(
        name="secrets",
        url="https://semgrep.dev/c/p/secrets",
        sha256="139b35ad3442bc83d1f0864db82fa4fdc7e1f1ee4b5ac872bfbeb604c82c6518",
        size=89_772,
    ),
)


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
    """Download and verify one exact rule bundle before writing it."""
    _validate_rule_url(bundle.url)
    request = urllib.request.Request(  # noqa: S310
        bundle.url,
        headers={"User-Agent": "AncestryLLM-release-security-gate"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        final_url = response.geturl()
        _validate_rule_url(final_url)
        if final_url != bundle.url:
            raise RuntimeError(f"Semgrep {bundle.name} rule bundle redirected unexpectedly")
        payload = response.read(bundle.size + 1)

    digest = hashlib.sha256(payload).hexdigest()
    if digest != bundle.sha256:
        raise RuntimeError(
            f"Semgrep {bundle.name} rule bundle content hash differs from the "
            "committed release-security contract"
        )
    if len(payload) != bundle.size:
        raise RuntimeError(
            f"Semgrep {bundle.name} rule bundle size differs from the committed "
            "release-security contract"
        )
    destination.write_bytes(payload)


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
