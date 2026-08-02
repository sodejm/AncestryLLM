"""Contracts for the version-aware desktop binary-signing policy."""

from __future__ import annotations

import pytest
from scripts.release_signing_policy import (
    allowed_signing_modes,
    default_signing_mode,
    signing_disclosure,
    validate_signing_mode,
)


@pytest.mark.parametrize("version", ("0.1.0", "0.5.0", "0.99.99"))
def test_prerelease_major_versions_are_not_fully_signed(version: str) -> None:
    assert default_signing_mode(version) == "unsigned"
    assert allowed_signing_modes(version) == frozenset({"unsigned", "self-signed"})

    validate_signing_mode(version, "unsigned")
    validate_signing_mode(version, "self-signed")
    with pytest.raises(ValueError, match=r"full trusted binary signing starts at v1\.0\.0"):
        validate_signing_mode(version, "trusted")


@pytest.mark.parametrize("version", ("1.0.0", "1.4.2", "2.0.0"))
def test_full_versions_require_trusted_binary_signing(version: str) -> None:
    assert default_signing_mode(version) == "trusted"
    assert allowed_signing_modes(version) == frozenset({"trusted"})

    validate_signing_mode(version, "trusted")
    for mode in ("unsigned", "self-signed"):
        with pytest.raises(ValueError, match="requires trusted binary signing"):
            validate_signing_mode(version, mode)


@pytest.mark.parametrize("version", ("v1.0.0", "1.0", "1.0.0-rc.1"))
def test_policy_rejects_non_stable_semver(version: str) -> None:
    with pytest.raises(ValueError, match="stable SemVer"):
        default_signing_mode(version)


def test_prerelease_disclosure_explicitly_defers_full_signing() -> None:
    notice = signing_disclosure("0.5.0", "unsigned")

    assert "not fully production/trusted signed" in notice
    assert "Full trusted binary signing starts with v1.0.0" in notice
    assert "Binary-signing mode for this release: `unsigned`" in notice
