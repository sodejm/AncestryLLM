# Release evidence

Each release workflow produces an immutable evidence bundle attached to its
GitHub Release. The bundle identifies the semantic version, annotated tag and
its version-dependent signing mode, commit,
workflow run, artifact hashes, supported platform/Python matrix, SBOM, quality
gates, security finding dispositions, and GEDCOM importer status.

Release evidence contains fictional or aggregate test data only. It must never
contain genealogy payloads, credentials, databases, backups, local paths, logs,
prompt/response bodies, or secret values.

A gate is recorded as `verified`, `failed`, `unavailable`, or `unverified`.
Only `verified` supports a positive compatibility claim. `unavailable` and
`unverified` remain visible limitations in the release notes.

Versioned source records live in `docs/release-evidence/<version>/`. Before
release readiness:

- `findings.json` must enumerate the dependency, Semgrep, CodeQL, secret-scan,
  and repository-safety finding sources. The readiness workflow, not this
  versioned source file, supplies their exact run status and evidence URL so a
  stale historical run cannot be presented as current release evidence. Every
  finding must be `fixed`, `false-positive`, or `accepted-risk`, with an owner,
  review expiry on or after the UTC readiness-evidence date, rationale, and
  HTTPS evidence link. An empty list is valid only with the explicit
  complete-inventory attestation.
- `interoperability.json` must include Ancestry, Geni, and MyHeritage, plus any
  other named interoperability target, as `verified`, `failed`, `unavailable`,
  or `unverified`, with dated evidence links. A `verified` or `failed` manual
  smoke test must name only fictional fixture IDs and record the vendor
  application version.

The readiness workflow creates `gates.json` for the exact candidate commit and
run. The evidence generator requires the complete gate inventory, validates
these versioned records, and copies them into both the readiness artifact and
final GitHub Release. Do not change a non-verified vendor to `verified` until
its linked, dated, fictional-data import record is reviewable.
