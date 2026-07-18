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
