#!/usr/bin/env python3
"""Emit a deterministic, dependency-free provider-framework evaluation.

This benchmark compares architectural capabilities using fictional workload
metadata. It never imports, installs, contacts, or invokes LangChain, LiteLLM,
or a remote provider, and it never stores prompts, responses, or genealogy
records.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Framework = Literal["native", "langchain", "litellm"]


@dataclass(frozen=True)
class FrameworkAssessment:
    framework: Framework
    dependency_footprint: str
    provider_control: str
    caching_batching: str
    consent_enforcement: str
    endpoint_policy: str
    structured_output: str
    observability: str
    security_surface: str
    maintenance: str
    recommendation: str


ASSESSMENTS: tuple[FrameworkAssessment, ...] = (
    FrameworkAssessment(
        "native",
        "small explicit contract; existing locked provider SDK extras",
        "full control of provider selection, timeouts, retries, and streams",
        "must be implemented explicitly; avoids hidden cache behavior",
        "implemented before provider calls in the application service",
        "central allowlist and loopback policy remain authoritative",
        "explicit schema validation and provider-specific adapters",
        "privacy-minimal audit metadata under application control",
        "smallest trusted surface and no framework tool abstraction",
        "already owned by the project and covered by current tests",
        "adopt",
    ),
    FrameworkAssessment(
        "langchain",
        "large optional abstraction and transitive dependency surface",
        "wrappers may obscure provider-specific lifecycle and policy controls",
        "rich primitives available but require policy integration and auditing",
        "must be proven at every wrapper boundary; not inherited automatically",
        "requires explicit enforcement around model and transport construction",
        "broad integrations but adapter behavior must remain schema-validated",
        "framework callbacks add value but require payload-redaction review",
        "larger surface including chains, memory, tools, and prompt abstractions",
        "additional upgrades and compatibility matrix to maintain",
        "defer",
    ),
    FrameworkAssessment(
        "litellm",
        "proxy/client dependency plus provider normalization layer",
        "centralizes routing but can hide rerouting and provider identity changes",
        "first-class cache and batch options require consent-aware integration",
        "must prevent proxy defaults from bypassing local consent and retention",
        "requires verification of endpoint, routing, and credential boundaries",
        "normalization helps breadth but missing fields must fail safely",
        "proxy telemetry must be reconciled with the encrypted run ledger",
        "adds proxy configuration, routing, and remote persistence concerns",
        "provider and proxy release changes increase operational burden",
        "defer",
    ),
)


def build_report() -> dict[str, object]:
    """Return stable, synthetic comparison data suitable for review or CI."""
    return {
        "benchmark": "provider-framework-evaluation",
        "version": 1,
        "network": "disabled",
        "data": "fictional-metadata-only",
        "frameworks": [asdict(assessment) for assessment in ASSESSMENTS],
        "decision": {
            "recommendation": "retain-native-adapters",
            "reason": "The current native contract best preserves explicit consent, endpoint policy, structured validation, and payload-minimal auditing without adding framework dependencies.",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the deterministic JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    report = build_report()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args = build_parser().parse_args(argv)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
