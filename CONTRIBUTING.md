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
