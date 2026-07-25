# Contributing

Use Python 3.12-3.14, create a focused branch, and run `make setup`. Put domain
logic in services, not console adapters. New providers implement the common
contract and mocked timeout/malformed-output/consent/offline tests. New modules
must be explicit built-ins with one-shot and console parity.

Before a pull request run `make test lint typecheck security sbom`. Describe
scope, privacy impact, threat-model changes, migration impact, and exact test
evidence. Do not commit real GEDCOM, RootsMagic, database, backup, report, log,
prompt/response, secrets, or person details; use clearly fictional fixtures.

GEDCOM changes must preserve citations, custom/vendor structures, pointers,
families, conflicts, and conservative removal invariants. RootsMagic fixtures
must be synthetic and source files must remain hash-identical after tests.

## Secure desktop development

Desktop work is governed by
[`docs/ADR-0025-electron-fastapi-desktop.md`](docs/ADR-0025-electron-fastapi-desktop.md)
and [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). Treat the renderer,
loopback clients, imported files, model output, plugins, packages, and updates
as untrusted. Preserve the network-free `provider=none` contract.

Before implementation, map the change to its threat/control and abuse-case IDs,
OWASP Top 10:2025 category, applicable versioned OWASP ASVS 5.0.0 requirements,
and NIST SP 800-218 (`PO`, `PS`, `PW`, or `RV`) outcomes. Add positive,
boundary, and negative regression tests before or with the behavior. A scanner
result alone does not close a control.

Use the dedicated issue branch/worktree and remain within its exclusive path
ownership. Do not edit shared lockfiles, generated contracts, workflows,
architecture/security documents, or another issue's subtree opportunistically.
Wait for every hard dependency to merge before branching from updated `main`.

Renderer code has no Node types/imports, direct filesystem/network/keyring/
provider/database access, generic IPC, raw HTML, remote assets, or secrets in
Vite environment values. Privileged IPC, sidecar routes, file grants, secrets,
events, plugins, and update paths must use strict versioned DTOs, size limits,
deny-by-default behavior, and the negative tests named by the threat ledger.

Desktop pull requests run all applicable Python and desktop format, lint,
strict type, unit, contract, integration, and packaged tests plus Semgrep,
CodeQL, secret scanning, dependency audit, lockfile review, and SBOM
generation. Record findings as fixed, evidence-backed false positives, or
permitted time-bounded residual risks. Never waive a finding silently; expired
exceptions and untriaged Critical/High findings fail the gate.

## Documentation and wiki publishing

The Markdown files under `docs/` are the authoritative source for documentation
published to the AncestryLLM GitHub Wiki. Make documentation changes in `docs/`
on a focused branch and submit them through the normal pull-request workflow.
The wiki is a generated publishing target, not a second documentation source.

All version-controlled Markdown files under `docs/` are in synchronization
scope, including the wiki home and navigation sources. Generated wiki pages
must not be copied back into the repository or included as generated artifacts
in a pull request. Removing a source page from `docs/` means its managed wiki
page will also be removed by synchronization.

Do not edit a managed GitHub Wiki page directly. A direct edit is allowed only
when a documented recovery procedure explicitly requires it; reproduce any
lasting correction in `docs/` immediately so the next synchronization does not
discard it.
