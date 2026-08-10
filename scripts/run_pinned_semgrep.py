#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = [
#     "pyyaml==6.0.3",
#     "semgrep==1.170.0",
# ]
# ///
"""Run lockfile-pinned Semgrep with content-pinned registry rule bundles."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
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
    semantic_sha256: str
    revisions: tuple[RuleRevision, ...]
    include_rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleArchive:
    """A commit- and content-pinned archive containing reviewed rule files."""

    name: str
    url: str
    semantic_sha256: str
    revision: RuleRevision
    members: tuple[str, ...]


RULE_BUNDLES = (
    RuleBundle(
        name="python",
        url="https://semgrep.dev/c/p/python",
        semantic_sha256="80eeb6e5e772926c4fb1e0c6dd49def6d197305127e002a6bdb24b35bf3e6b80",
        # Registry edges served these reviewed YAML and JSON encodings of the
        # same 151-rule set. All remain byte-pinned and must also match the
        # canonical semantic digest; every other response fails.
        revisions=(
            RuleRevision(
                sha256="f65001af74892a76d941e11694a0f6b84c3b5ab558ae5202af6b3fd070566f56",
                size=487_962,
            ),
            RuleRevision(
                sha256="6c5830b3c92994be81404c599c7d5595538aa8d6036fb8042eb3861e6608638d",
                size=487_962,
            ),
            RuleRevision(
                sha256="31c1dfa46e8ddd97f9ac98c607ddd77b20a2c3356d7ec987359961d47ec27035",
                size=487_962,
            ),
            RuleRevision(
                sha256="c475a61f25c07ed68fbe20b1f0747a89218063a9ba85465999c53db57255017a",
                size=487_962,
            ),
            RuleRevision(
                sha256="0b7d2717d79da2ce99bffa329d954833e2ecc034b7ff86f0932a38ce416b5946",
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
        semantic_sha256="58f2b776c275f1adad42aecf8ace71774632d95e1d81db717bc63aeeb12308fa",
        # Registry edges served reviewed YAML and JSON encodings of the same
        # 52-rule set. The raw pins constrain transport bytes while the shared
        # semantic digest prevents a newly pinned encoding from changing rules.
        revisions=(
            RuleRevision(
                sha256="139b35ad3442bc83d1f0864db82fa4fdc7e1f1ee4b5ac872bfbeb604c82c6518",
                size=89_772,
            ),
            RuleRevision(
                sha256="7c0b0163d7cbfe44f16cec78556662655bedeec1d41c6e091ddef5d85c1d5eff",
                size=82_768,
            ),
        ),
    ),
    RuleBundle(
        name="trailofbits",
        url="https://semgrep.dev/c/p/trailofbits",
        semantic_sha256="8b1a7d47796f1d002fa6ca1a960efefaf775b2d0cd864a413aa23a5210ed59ea",
        revisions=(
            RuleRevision(
                sha256="d734464c3a746401a5293d44bec10a2be2bf718814b52ef3bd0f284f52863343",
                size=185_537,
            ),
        ),
        include_rule_ids=(
            "trailofbits.generic.curl-insecure.curl-insecure",
            "trailofbits.generic.curl-unencrypted-url.curl-unencrypted-url",
            "trailofbits.generic.gpg-insecure-flags.gpg-insecure-flags",
            "trailofbits.generic.installer-allow-untrusted.installer-allow-untrusted",
            "trailofbits.generic.node-disable-certificate-validation.node-disable-certificate-validation",
            "trailofbits.generic.openssl-insecure-flags.openssl-insecure-flags",
            "trailofbits.generic.ssh-disable-host-key-checking.ssh-disable-host-key-checking",
            "trailofbits.generic.tar-insecure-flags.tar-insecure-flags",
            "trailofbits.generic.wget-no-check-certificate.wget-no-check-certificate",
            "trailofbits.generic.wget-unencrypted-url.wget-unencrypted-url",
        ),
    ),
)

RULE_ARCHIVES = (
    RuleArchive(
        name="apiiro-prevent",
        url=(
            "https://codeload.github.com/apiiro/malicious-code-ruleset/tar.gz/"
            "a21246b666f34db899f0e33add7237ed70fab790"
        ),
        semantic_sha256="078d02c35e13f132282b4bf50f9756262d806365d64589b70644588f580e4ff5",
        revision=RuleRevision(
            sha256="d5dd3bd153c442761244d1493b855dc16d872d18bcde765bfca9f630fa64e2f4",
            size=32_782,
        ),
        members=(
            "dynamic_execution/python/python_dynamic-execution-system.yml",
            "dynamic_execution/python/python_dynamic-execution_exec_eval.yml",
            "dynamic_execution/python/python_dynamic-execution_functiontype.yml",
            "dynamic_execution/python/python_dynamic-execution_pickle.yml",
            "dynamic_execution/javascript_typescript/javascript_dynamic-execution_eval-Function.yml",
            "dynamic_execution/javascript_typescript/javascript_dynamic-execution_system.yml",
            "obfuscation/python/python_obfuscation_indirect-eval.yml",
            "obfuscation/python/python_obfuscation_indirect-exec.yml",
            "obfuscation/python/python_obfuscation_indirect-pickle.yml",
            "obfuscation/javascript_typescript/javascript_obfuscation_blatant.yml",
            "obfuscation/javascript_typescript/javascript_obfuscation_indirect-execution.yml",
            "obfuscation/generic_hide_remotely.yml",
        ),
    ),
    RuleArchive(
        name="elttam",
        url=(
            "https://codeload.github.com/elttam/semgrep-rules/tar.gz/"
            "244268562cc92d33f54b8a60a187df5520f91b26"
        ),
        semantic_sha256="f13c542a65ff1898c75f1aca20574dbb96b12ece72ca794fed0d7678958b9ea6",
        revision=RuleRevision(
            sha256="4a1c064f24391ec8423e53b9d423c5ba2cdedd5b481a0c73237ca32d6cd9d748",
            size=444_683,
        ),
        members=(
            "rules/yaml/github-actions/security/save-state.yaml",
            "rules/yaml/github-actions/security/set-output.yaml",
        ),
    ),
)

_DOWNLOAD_ATTEMPTS = 3


def _yaml_module() -> ModuleType:
    """Load the script-only YAML dependency after argument parsing."""
    import yaml

    return yaml


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


def _validate_archive_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "codeload.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"Semgrep rule archive URL is not trusted: {url}")


def _load_rule_document(payload: bytes) -> dict[str, object]:
    yaml = _yaml_module()
    try:
        document = yaml.safe_load(payload)
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError("Semgrep rule bundle is not valid YAML or JSON") from error
    if not isinstance(document, dict):
        raise ValueError("Semgrep rule bundle must be a mapping")
    rules = document.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Semgrep rule bundle must contain a rules list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Semgrep rule bundle contains a non-mapping rule")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError("Semgrep rule bundle contains a rule without an id")
    return document


def _semantic_rule_digest(payload: bytes) -> str:
    """Hash a rule bundle after normalizing reviewed registry encodings."""
    document = _load_rule_document(payload)
    rules = document["rules"]
    assert isinstance(rules, list)
    rule_ids: list[str] = []
    for rule in rules:
        assert isinstance(rule, dict)
        rule_id = rule.get("id")
        assert isinstance(rule_id, str)
        rule_ids.append(rule_id)
    if len(set(rule_ids)) != len(rule_ids):
        raise ValueError("Semgrep rule bundle contains duplicate rule ids")

    normalized = dict(document)
    normalized.pop("missed", None)
    normalized["rules"] = sorted(rules, key=lambda rule: rule["id"])
    try:
        canonical = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ValueError("Semgrep rule bundle cannot be normalized") from error
    return hashlib.sha256(canonical).hexdigest()


def _rules_from_payload(payload: bytes) -> list[dict[str, object]]:
    document = _load_rule_document(payload)
    rules = document["rules"]
    assert isinstance(rules, list)
    return [dict(rule) for rule in rules if isinstance(rule, dict)]


def _selected_bundle_rules(bundle: RuleBundle, payload: bytes) -> list[dict[str, object]]:
    rules = _rules_from_payload(payload)
    if not bundle.include_rule_ids:
        return rules
    by_id = {rule["id"]: rule for rule in rules}
    missing = set(bundle.include_rule_ids) - by_id.keys()
    if missing:
        raise RuntimeError(
            f"Semgrep {bundle.name} rule bundle is missing reviewed rules: "
            + ", ".join(sorted(missing))
        )
    return [by_id[rule_id] for rule_id in bundle.include_rule_ids]


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
            try:
                semantic_digest = _semantic_rule_digest(payload)
            except ValueError:
                mismatch = "semantic content"
                observations[-1] += ", semantic=invalid"
                continue
            if semantic_digest == bundle.semantic_sha256:
                destination.write_bytes(payload)
                return
            mismatch = "semantic content"
            observations[-1] += f", semantic_sha256={semantic_digest}"
            continue
        mismatch = "size" if matching_revisions else "content hash"

    observed = "; ".join(dict.fromkeys(observations))
    raise RuntimeError(
        f"Semgrep {bundle.name} rule bundle {mismatch} differs from the committed "
        f"release-security contract after {_DOWNLOAD_ATTEMPTS} attempts; observed {observed}"
    )


def _download_rule_archive(archive: RuleArchive) -> bytes:
    """Download one immutable commit archive and verify its exact bytes."""
    _validate_archive_url(archive.url)
    request = urllib.request.Request(  # noqa: S310
        archive.url,
        headers={"User-Agent": "AncestryLLM-release-security-gate"},
    )
    observations: list[str] = []
    for _attempt in range(_DOWNLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                final_url = response.geturl()
                _validate_archive_url(final_url)
                if final_url != archive.url:
                    raise RuntimeError(
                        f"Semgrep {archive.name} rule archive redirected unexpectedly"
                    )
                payload = response.read(archive.revision.size + 1)
        except OSError as error:
            observations.append(f"transport_error={type(error).__name__}")
            continue

        digest = hashlib.sha256(payload).hexdigest()
        observations.append(f"sha256={digest}, size={len(payload)}")
        if digest == archive.revision.sha256 and len(payload) == archive.revision.size:
            return payload

    observed = "; ".join(dict.fromkeys(observations))
    raise RuntimeError(
        f"Semgrep {archive.name} rule archive differs from the committed "
        f"release-security contract after {_DOWNLOAD_ATTEMPTS} attempts; "
        f"observed {observed}"
    )


def _archive_rules(archive: RuleArchive, payload: bytes) -> list[dict[str, object]]:
    """Read only explicitly reviewed members without extracting the archive."""
    documents: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            for member in tar.getmembers():
                relative_name = member.name.partition("/")[2]
                if relative_name not in archive.members:
                    continue
                if not member.isfile() or relative_name in documents:
                    raise ValueError(
                        f"Semgrep {archive.name} archive has an invalid member: {relative_name}"
                    )
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise ValueError(
                        f"Semgrep {archive.name} archive member cannot be read: {relative_name}"
                    )
                documents[relative_name] = extracted.read()
    except (tarfile.TarError, OSError) as error:
        raise ValueError(f"Semgrep {archive.name} rule archive is invalid") from error
    missing = set(archive.members) - documents.keys()
    if missing:
        raise ValueError(
            f"Semgrep {archive.name} archive is missing reviewed members: "
            + ", ".join(sorted(missing))
        )
    rules: list[dict[str, object]] = []
    for member in archive.members:
        rules.extend(_rules_from_payload(documents[member]))
    semantic_payload = _yaml_module().safe_dump({"rules": rules}, sort_keys=False).encode()
    if _semantic_rule_digest(semantic_payload) != archive.semantic_sha256:
        raise ValueError(f"Semgrep {archive.name} reviewed rules changed semantically")
    return rules


def _canonical_rule(rule: dict[str, object], *, matching_only: bool = False) -> bytes:
    normalized = dict(rule)
    if matching_only:
        for key in ("id", "message", "metadata", "severity"):
            normalized.pop(key, None)
    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ValueError("Semgrep rule cannot be normalized") from error


def _deduplicate_rules(
    rule_groups: Iterable[Iterable[dict[str, object]]],
) -> tuple[list[dict[str, object]], int]:
    """Remove identical IDs and matching logic while rejecting ID collisions."""
    selected: list[dict[str, object]] = []
    by_id: dict[str, bytes] = {}
    matching_fingerprints: set[bytes] = set()
    duplicate_count = 0
    for rules in rule_groups:
        for rule in rules:
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise ValueError("Semgrep rule id must be a non-empty string")
            canonical = _canonical_rule(rule)
            previous = by_id.get(rule_id)
            if previous is not None:
                if previous != canonical:
                    raise ValueError(f"Semgrep rule id collision has different content: {rule_id}")
                duplicate_count += 1
                continue
            by_id[rule_id] = canonical
            matching_fingerprint = _canonical_rule(rule, matching_only=True)
            if matching_fingerprint in matching_fingerprints:
                duplicate_count += 1
                continue
            matching_fingerprints.add(matching_fingerprint)
            selected.append(rule)
    return selected, duplicate_count


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
    yaml = _yaml_module()
    with tempfile.TemporaryDirectory(prefix="ancestryllm-semgrep-") as temp_dir:
        rule_groups: list[list[dict[str, object]]] = []
        for bundle in RULE_BUNDLES:
            config_path = Path(temp_dir) / f"{bundle.name}.yml"
            download_rule_bundle(bundle, config_path)
            rule_groups.append(_selected_bundle_rules(bundle, config_path.read_bytes()))
        for archive in RULE_ARCHIVES:
            rule_groups.append(_archive_rules(archive, _download_rule_archive(archive)))

        rules, duplicate_count = _deduplicate_rules(rule_groups)
        config_path = Path(temp_dir) / "reviewed-rules.yml"
        config_path.write_text(yaml.safe_dump({"rules": rules}, sort_keys=False), encoding="utf-8")
        print(
            f"Loaded {len(rules)} reviewed Semgrep rules "
            f"({duplicate_count} duplicate{'s' if duplicate_count != 1 else ''} removed)."
        )

        command = [
            str(_semgrep_executable()),
            "scan",
            "--error",
            "--metrics=off",
            "--disable-version-check",
            "--config",
            str(config_path),
        ]
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
