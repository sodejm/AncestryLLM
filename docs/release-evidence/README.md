# Release evidence

Each release workflow produces an immutable evidence bundle attached to its
GitHub Release. The bundle identifies the semantic version, annotated tag and
its version-dependent signing mode, commit,
workflow run, artifact hashes, supported platform/Python matrix, SBOM, quality
gates, verified-bootstrap identity, security finding dispositions, and GEDCOM
importer status.

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

`bootstrap-verification` is a required gate. Its input is the schema-v1 receipt
from the repository-local
[verified uv bootstrap](../security/verified-uv-bootstrap.md), not a manually
entered claim. The generator validates the receipt against the current policy,
including its policy digest, normalized platform/architecture, exact `uv` and
GitHub CLI assets and hashes, source repository and commit/ref, signer workflow,
OIDC issuer, SLSA predicate, UTC timestamp, and success status. The manifest
records that identity and the receipt digest. Unknown fields, omitted fields,
non-success receipts, local paths, or identity drift fail evidence generation.

## Native control-foundation records

[`issue-363-macos-arm64-container-supervisor.json`](issue-363-macos-arm64-container-supervisor.json)
is a sanitized, schema-v1 engineering record for the isolated native macOS
arm64 Docker-control exercise. It binds the tested platform, engine and Compose
identities, image digest, lifecycle operations, ambient-context preservation,
and final owned-resource cleanup without recording a local path, socket,
username, hostname, environment value, token, or response body.

This record is partial source-feature evidence, not a release-gate result. It
does not prove an application container runtime, workload authentication,
secret or genealogy-data custody, migration, backup and recovery, all-platform
support, packaged integration, or independent G5/G7 review. Future readiness
evidence must not interpret this file as satisfying those remaining gates.

## Tooling evaluation records

Checked tooling evaluations such as
[`uv-build-evaluation-v1.json`](uv-build-evaluation-v1.json) are reproducible
engineering decision records, not release-gate results. The uv_build report
binds exact backend versions, source commit and epoch, artifact hashes,
comparison results, accepted archive-only normalizations, stable failure codes,
and a compatible or incompatible status. Its closed schema rejects unknown or
missing fields and its sanitization contract rejects local paths.

The report documents why the evaluated candidate may or may not proceed to a
separate adoption change. It does not prove a later commit or release candidate,
cannot replace exact-candidate readiness evidence, and cannot turn an
incompatible comparison into an authorized release backend. See the human
[uv_build evaluation](../UV_BUILD_EVALUATION.md) for the disposition.
