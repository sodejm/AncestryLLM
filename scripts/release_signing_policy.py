#!/usr/bin/env python3
"""Resolve and validate AncestryLLM desktop binary-signing policy by version."""

from __future__ import annotations

import argparse
import json
import re
import sys

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PRE_1_MODES = frozenset({"unsigned", "self-signed"})
FULL_RELEASE_MODES = frozenset({"trusted"})
SIGNING_MODES = PRE_1_MODES | FULL_RELEASE_MODES

PRE_1_NOTICE = (
    "This pre-1.0 release is not fully production/trusted signed. "
    "Official 0.x binaries are unsigned by default; locally produced binaries may be "
    "self-signed, and operating systems may show an unknown-publisher warning. "
    "Full trusted binary signing starts with v1.0.0."
)
TRUSTED_NOTICE = (
    "This full-version release requires production/trusted binary signing on every "
    "supported desktop platform."
)


def _major(version: str) -> int:
    match = SEMVER.fullmatch(version)
    if match is None:
        raise ValueError("version must be stable SemVer")
    return int(match.group(1))


def allowed_signing_modes(version: str) -> frozenset[str]:
    """Return binary-signing modes permitted for a stable release version."""

    return PRE_1_MODES if _major(version) == 0 else FULL_RELEASE_MODES


def default_signing_mode(version: str) -> str:
    """Return the mode used by official release automation."""

    return "unsigned" if _major(version) == 0 else "trusted"


def validate_signing_mode(version: str, mode: str) -> None:
    """Reject a signing mode that violates the v1.0.0 policy boundary."""

    if mode not in SIGNING_MODES:
        raise ValueError(f"unsupported binary-signing mode: {mode}")
    allowed = allowed_signing_modes(version)
    if mode in allowed:
        return
    if _major(version) == 0:
        raise ValueError(
            f"{version} cannot use trusted binary signing; "
            "full trusted binary signing starts at v1.0.0"
        )
    raise ValueError(f"{version} requires trusted binary signing")


def signing_disclosure(version: str, mode: str) -> str:
    """Return the required user-facing binary-signing disclosure."""

    validate_signing_mode(version, mode)
    if _major(version) == 0:
        return f"{PRE_1_NOTICE} Binary-signing mode for this release: `{mode}`."
    return f"{TRUSTED_NOTICE} Binary-signing mode for this release: `{mode}`."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--mode", choices=sorted(SIGNING_MODES))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--notice", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        mode = args.mode or default_signing_mode(args.version)
        validate_signing_mode(args.version, mode)
    except ValueError as exc:
        print(f"release signing policy error: {exc}", file=sys.stderr)
        return 1
    if args.notice:
        print("## Binary signing\n")
        print(signing_disclosure(args.version, mode))
    elif args.json:
        print(
            json.dumps(
                {
                    "allowedBinarySigningModes": sorted(allowed_signing_modes(args.version)),
                    "binarySigningMode": mode,
                    "version": args.version,
                },
                sort_keys=True,
            )
        )
    else:
        print(mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
