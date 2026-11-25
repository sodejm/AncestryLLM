#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.16"
# dependencies = [
#     "pyyaml==6.0.4",
#     "semgrep==1.186.0",
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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType


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


@dataclass(frozen=True)
class RegistryRule:
    """One Semgrep registry rule selected by its immutable version identifier."""

    rule_id: str
    version_id: str


@dataclass(frozen=True)
class RuleRegistrySnapshot:
    """A reviewed set of versioned Semgrep registry rules."""

    name: str
    semantic_sha256: str
    rules: tuple[RegistryRule, ...]


RULE_BUNDLES = (
    RuleBundle(
        name="python",
        url="https://semgrep.dev/c/p/python",
        semantic_sha256="058375e039e6ddf738c23faa469d195dacc3751851dc02464311a22652c2e271",
        revisions=(
            RuleRevision(
                sha256="de5bc79d074c3b4146338a0bf95830406f169bd91ac590a2f1ef111f162e23f5",
                size=502_198,
            ),
        ),
    ),
)

RULE_REGISTRY_SNAPSHOTS = (
    RuleRegistrySnapshot(
        name="secrets",
        semantic_sha256="989abeac7bb3f93823bdc0ed498e8ad371f4be9ac99758c870e1e89d075774cb",
        rules=(
            RegistryRule(
                "generic.secrets.security.detected-amazon-mws-auth-token.detected-amazon-mws-auth-token",
                "K3TKkGj",
            ),
            RegistryRule(
                "generic.secrets.security.detected-artifactory-password.detected-artifactory-password",
                "qkTR7BB",
            ),
            RegistryRule(
                "generic.secrets.security.detected-artifactory-token.detected-artifactory-token",
                "l4TJR6J",
            ),
            RegistryRule(
                "generic.secrets.security.detected-aws-access-key-id-value.detected-aws-access-key-id-value",
                "YDTZenE",
            ),
            RegistryRule(
                "generic.secrets.security.detected-aws-appsync-graphql-key.detected-aws-appsync-graphql-key",
                "o5TbD9o",
            ),
            RegistryRule(
                "generic.secrets.security.detected-aws-secret-access-key.detected-aws-secret-access-key",
                "zyTb2Dr",
            ),
            RegistryRule(
                "generic.secrets.security.detected-aws-session-token.detected-aws-session-token",
                "pZT03Lx",
            ),
            RegistryRule(
                "generic.secrets.security.detected-codeclimate.detected-codeclimate", "X0Tzy2o"
            ),
            RegistryRule(
                "generic.secrets.security.detected-facebook-access-token.detected-facebook-access-token",
                "1QTyp7J",
            ),
            RegistryRule(
                "generic.secrets.security.detected-facebook-oauth.detected-facebook-oauth",
                "9lT4b5N",
            ),
            RegistryRule(
                "generic.secrets.security.detected-google-gcm-service-account.detected-google-gcm-service-account",
                "w8TRoDX",
            ),
            RegistryRule(
                "generic.secrets.security.detected-google-oauth-access-token.detected-google-oauth-access-token",
                "xyTjzp6",
            ),
            RegistryRule(
                "generic.secrets.security.detected-heroku-api-key.detected-heroku-api-key",
                "e1Tyj3N",
            ),
            RegistryRule(
                "generic.secrets.security.detected-hockeyapp.detected-hockeyapp", "vdT068z"
            ),
            RegistryRule(
                "generic.secrets.security.detected-mailchimp-api-key.detected-mailchimp-api-key",
                "nWT2LoR",
            ),
            RegistryRule(
                "generic.secrets.security.detected-mailgun-api-key.detected-mailgun-api-key",
                "ExTExAG",
            ),
            RegistryRule(
                "generic.secrets.security.detected-outlook-team.detected-outlook-team", "LjTkgA1"
            ),
            RegistryRule(
                "generic.secrets.security.detected-paypal-braintree-access-token.detected-paypal-braintree-access-token",
                "8KT5ryb",
            ),
            RegistryRule(
                "generic.secrets.security.detected-pgp-private-key-block.detected-pgp-private-key-block",
                "gETB7O4",
            ),
            RegistryRule(
                "generic.secrets.security.detected-picatic-api-key.detected-picatic-api-key",
                "QkTGqwK",
            ),
            RegistryRule(
                "generic.secrets.security.detected-private-key.detected-private-key", "3ZT4X4Y"
            ),
            RegistryRule(
                "generic.secrets.security.detected-sauce-token.detected-sauce-token", "44TEjER"
            ),
            RegistryRule(
                "generic.secrets.security.detected-slack-token.detected-slack-token", "JdTzxz3"
            ),
            RegistryRule(
                "generic.secrets.security.detected-slack-webhook.detected-slack-webhook", "5PTo1oO"
            ),
            RegistryRule(
                "generic.secrets.security.detected-sonarqube-docs-api-key.detected-sonarqube-docs-api-key",
                "bZTQ72r",
            ),
            RegistryRule(
                "generic.secrets.security.detected-square-access-token.detected-square-access-token",
                "BjTkZkz",
            ),
            RegistryRule(
                "generic.secrets.security.detected-square-oauth-secret.detected-square-oauth-secret",
                "DkTRbRG",
            ),
            RegistryRule(
                "generic.secrets.security.detected-ssh-password.detected-ssh-password", "WrTqKqY"
            ),
            RegistryRule(
                "generic.secrets.security.detected-stripe-api-key.detected-stripe-api-key",
                "0bTKzK1",
            ),
            RegistryRule(
                "generic.secrets.security.detected-stripe-restricted-api-key.detected-stripe-restricted-api-key",
                "K3TKkKj",
            ),
            RegistryRule(
                "generic.secrets.security.detected-telegram-bot-api-key.detected-telegram-bot-api-key",
                "qkTR7RB",
            ),
            RegistryRule(
                "generic.secrets.security.detected-twilio-api-key.detected-twilio-api-key",
                "l4TJRJJ",
            ),
            RegistryRule(
                "generic.secrets.security.detected-username-and-password-in-uri.detected-username-and-password-in-uri",
                "YDTZeZE",
            ),
            RegistryRule("go.jwt-go.security.jwt.hardcoded-jwt-key", "pZT0305"),
            RegistryRule(
                "java.java-jwt.security.jwt-hardcode.java-jwt-hardcoded-secret", "d6Tyx8j"
            ),
            RegistryRule(
                "javascript.express.security.express-jwt-hardcoded-secret.express-jwt-hardcoded-secret",
                "pZT03Q0",
            ),
            RegistryRule("javascript.jose.security.jwt-hardcode.hardcoded-jwt-secret", "d6TyxbX"),
            RegistryRule(
                "javascript.jsonwebtoken.security.jwt-hardcode.hardcoded-jwt-secret", "gETB75D"
            ),
            RegistryRule(
                "javascript.passport-jwt.security.passport-hardcode.hardcoded-passport-secret",
                "X0TzyoE",
            ),
            RegistryRule("python.boto3.security.hardcoded-token.hardcoded-token", "gETB78n"),
            RegistryRule(
                "ruby.lang.security.hardcoded-http-auth-in-controller.hardcoded-http-auth-in-controller",
                "0bTKzNK",
            ),
            RegistryRule(
                "generic.secrets.security.detected-bcrypt-hash.detected-bcrypt-hash", "2KTv236"
            ),
            RegistryRule(
                "generic.secrets.security.detected-etc-shadow.detected-etc-shadow", "jQTn5yp"
            ),
            RegistryRule(
                "generic.secrets.security.detected-npm-registry-auth-token.detected-npm-registry-auth-token",
                "7ZTE3n2",
            ),
            RegistryRule(
                "terraform.lang.security.rds-insecure-password-storage-in-source-code.rds-insecure-password-storage-in-source-code",
                "gETB77b",
            ),
            RegistryRule(
                "terraform.aws.security.aws-provider-static-credentials.aws-provider-static-credentials",
                "rxTAK76",
            ),
            RegistryRule(
                "terraform.aws.security.aws-lambda-environment-credentials.aws-lambda-environment-credentials",
                "jQTn573",
            ),
            RegistryRule(
                "yaml.kubernetes.security.secrets-in-config-file.secrets-in-config-file", "xyTjz5B"
            ),
            RegistryRule(
                "ruby.lang.security.hardcoded-secret-rsa-passphrase.hardcoded-secret-rsa-passphrase",
                "K3TKkEo",
            ),
            RegistryRule(
                "javascript.express.security.audit.express-session-hardcoded-secret.express-session-hardcoded-secret",
                "LjTkgle",
            ),
            RegistryRule(
                "kotlin.gradle.security.build-gradle-password-hardcoded.build-gradle-password-hardcoded",
                "DkTRbLX",
            ),
            RegistryRule(
                "generic.secrets.security.detected-onfido-live-api-token.detected-onfido-live-api-token",
                "A8TJzE2",
            ),
        ),
    ),
    RuleRegistrySnapshot(
        name="trailofbits",
        semantic_sha256="4aa0085c653b340cf98ff1eabd231e7975ac69d81d272df59b3e111030b35fda",
        rules=(
            RegistryRule(
                "trailofbits.generic.curl-insecure.curl-insecure",
                "RGTKGnY",
            ),
            RegistryRule(
                "trailofbits.generic.curl-unencrypted-url.curl-unencrypted-url",
                "1QTokGb",
            ),
            RegistryRule(
                "trailofbits.generic.gpg-insecure-flags.gpg-insecure-flags",
                "BjTe0Jg",
            ),
            RegistryRule(
                "trailofbits.generic.installer-allow-untrusted.installer-allow-untrusted",
                "DkTG0Xg",
            ),
            RegistryRule(
                "trailofbits.generic.node-disable-certificate-validation.node-disable-certificate-validation",
                "GxTPxzL",
            ),
            RegistryRule(
                "trailofbits.generic.openssl-insecure-flags.openssl-insecure-flags",
                "WrTdpG9",
            ),
            RegistryRule(
                "trailofbits.generic.ssh-disable-host-key-checking.ssh-disable-host-key-checking",
                "0bTwbgN",
            ),
            RegistryRule(
                "trailofbits.generic.tar-insecure-flags.tar-insecure-flags",
                "K3Trq8x",
            ),
            RegistryRule(
                "trailofbits.generic.wget-no-check-certificate.wget-no-check-certificate",
                "qkTQnL3",
            ),
            RegistryRule(
                "trailofbits.generic.wget-unencrypted-url.wget-unencrypted-url",
                "l4TyOon",
            ),
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
_MAX_REGISTRY_RULE_RESPONSE_BYTES = 1_000_000


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


def _registry_rule_url(version_id: str) -> str:
    if not version_id.isalnum():
        raise ValueError(f"Semgrep registry rule version is invalid: {version_id}")
    return f"https://semgrep.dev/api/registry/rules/{version_id}"


def _validate_registry_rule_url(url: str) -> None:
    _validate_rule_url(url)
    parsed = urlparse(url)
    if parsed.query or parsed.fragment or not parsed.path.startswith("/api/registry/rules/"):
        raise ValueError(f"Semgrep registry rule URL is not trusted: {url}")
    version_id = parsed.path.removeprefix("/api/registry/rules/")
    if not version_id or "/" in version_id or not version_id.isalnum():
        raise ValueError(f"Semgrep registry rule URL is not trusted: {url}")


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


def _download_registry_snapshot(snapshot: RuleRegistrySnapshot) -> list[dict[str, object]]:
    """Fetch every committed Semgrep registry rule version and verify the aggregate."""
    expected_rule_ids = [rule.rule_id for rule in snapshot.rules]
    if not expected_rule_ids or len(set(expected_rule_ids)) != len(expected_rule_ids):
        raise ValueError(f"Semgrep {snapshot.name} registry snapshot has duplicate rule ids")

    rules: list[dict[str, object]] = []
    for registry_rule in snapshot.rules:
        url = _registry_rule_url(registry_rule.version_id)
        _validate_registry_rule_url(url)
        request = urllib.request.Request(  # noqa: S310
            url,
            headers={"User-Agent": "AncestryLLM-release-security-gate"},
        )
        observations: list[str] = []
        for _attempt in range(_DOWNLOAD_ATTEMPTS):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                    final_url = response.geturl()
                    _validate_registry_rule_url(final_url)
                    if final_url != url:
                        raise RuntimeError(
                            f"Semgrep {snapshot.name} registry rule redirected unexpectedly"
                        )
                    payload = response.read(_MAX_REGISTRY_RULE_RESPONSE_BYTES + 1)
            except OSError as error:
                observations.append(f"transport_error={type(error).__name__}")
                continue

            if len(payload) > _MAX_REGISTRY_RULE_RESPONSE_BYTES:
                observations.append("response_too_large")
                continue
            try:
                document = json.loads(payload)
                version_id = document["meta"]["rule"]["version_id"]
                downloaded_rules = document["definition"]["rules"]
            except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                observations.append("invalid_response")
                continue
            if version_id != registry_rule.version_id:
                observations.append(f"version_id={version_id!r}")
                continue
            if not isinstance(downloaded_rules, list) or len(downloaded_rules) != 1:
                observations.append("unexpected_rule_count")
                continue
            downloaded_rule = downloaded_rules[0]
            if not isinstance(downloaded_rule, dict):
                observations.append("invalid_rule")
                continue
            rule = dict(downloaded_rule)
            rule["id"] = registry_rule.rule_id
            rules.append(rule)
            break
        else:
            observed = "; ".join(dict.fromkeys(observations))
            raise RuntimeError(
                f"Semgrep {snapshot.name} registry rule {registry_rule.rule_id} differs "
                f"from the committed release-security contract after "
                f"{_DOWNLOAD_ATTEMPTS} attempts; observed {observed}"
            )

    semantic_payload = json.dumps({"rules": rules}, separators=(",", ":")).encode()
    if _semantic_rule_digest(semantic_payload) != snapshot.semantic_sha256:
        raise ValueError(f"Semgrep {snapshot.name} reviewed rules changed semantically")
    return rules


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


def _canonical_detection_rule(rule: dict[str, object]) -> bytes:
    """Normalize non-matching Semgrep Registry presentation metadata."""
    normalized = dict(rule)
    metadata = normalized.get("metadata")
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        for key in ("semgrep.dev", "shortlink", "source"):
            metadata.pop(key, None)
        if metadata:
            normalized["metadata"] = metadata
        else:
            normalized.pop("metadata", None)
    return _canonical_rule(normalized)


def _deduplicate_rules(
    rule_groups: Iterable[Iterable[dict[str, object]]],
) -> tuple[list[dict[str, object]], int]:
    """Remove identical IDs and matching logic while rejecting ID collisions."""
    selected: list[dict[str, object]] = []
    by_id: dict[str, bytes] = {}
    detection_by_id: dict[str, bytes] = {}
    matching_fingerprints: set[bytes] = set()
    duplicate_count = 0
    for rules in rule_groups:
        for rule in rules:
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise ValueError("Semgrep rule id must be a non-empty string")
            canonical = _canonical_rule(rule)
            previous = by_id.get(rule_id)
            if previous is not None and previous != canonical:
                if detection_by_id[rule_id] != _canonical_detection_rule(rule):
                    raise ValueError(f"Semgrep rule id collision has different content: {rule_id}")
                duplicate_count += 1
                continue
            by_id[rule_id] = canonical
            detection_by_id[rule_id] = _canonical_detection_rule(rule)
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
        rule_groups.extend(
            _download_registry_snapshot(snapshot) for snapshot in RULE_REGISTRY_SNAPSHOTS
        )
        rule_groups.extend(
            _archive_rules(archive, _download_rule_archive(archive)) for archive in RULE_ARCHIVES
        )

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
    """Run the run pinned semgrep command and return its exit status."""
    parser = argparse.ArgumentParser(
        description=("Run lockfile-pinned Semgrep with content-pinned registry rule bundles.")
    )
    parser.add_argument("targets", nargs="+", help="Paths for Semgrep to scan")
    args = parser.parse_args(arguments)
    return run_scan(args.targets)


if __name__ == "__main__":
    raise SystemExit(main())
