"""Contracts for the version-aware desktop binary-signing policy."""

from __future__ import annotations

import pytest
from scripts.release_signing_policy import (
    allowed_signing_modes,
    default_signing_mode,
    release_tag_mode,
    signing_disclosure,
    validate_signing_mode,
)


@pytest.mark.parametrize("version", ("0.1.0", "0.5.0", "0.99.99"))
def test_pre_1_versions_require_every_release_output_to_be_unsigned(version: str) -> None:
    assert default_signing_mode(version) == "unsigned"
    assert allowed_signing_modes(version) == frozenset({"unsigned"})
    assert release_tag_mode(version) == "unsigned-annotated"

    validate_signing_mode(version, "unsigned")
    with pytest.raises(ValueError, match=r"0\.x releases must remain unsigned"):
        validate_signing_mode(version, "trusted")
    with pytest.raises(ValueError, match="unsupported binary-signing mode"):
        validate_signing_mode(version, "self-signed")


@pytest.mark.parametrize("version", ("1.0.0", "1.4.2", "2.0.0"))
def test_full_versions_require_trusted_binary_signing(version: str) -> None:
    assert default_signing_mode(version) == "trusted"
    assert allowed_signing_modes(version) == frozenset({"trusted"})
    assert release_tag_mode(version) == "signed-annotated"

    validate_signing_mode(version, "trusted")
    with pytest.raises(ValueError, match="requires trusted binary signing"):
        validate_signing_mode(version, "unsigned")
    with pytest.raises(ValueError, match="unsupported binary-signing mode"):
        validate_signing_mode(version, "self-signed")


@pytest.mark.parametrize("version", ("v1.0.0", "1.0", "1.0.0-rc.1"))
def test_policy_rejects_non_stable_semver(version: str) -> None:
    with pytest.raises(ValueError, match="stable SemVer"):
        default_signing_mode(version)


def test_pre_1_disclosure_prohibits_every_release_signature() -> None:
    notice = signing_disclosure("0.5.0", "unsigned")

    assert "installers and release tag are intentionally unsigned" in notice
    assert "Code signing, notarization, Authenticode, and detached package signatures" in notice
    assert "prohibited for every stable 0.x release" in notice
    assert "Trusted release signing starts with v1.0.0" in notice
    assert "Binary-signing mode for this release: `unsigned`" in notice
