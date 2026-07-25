# Release evidence

Each release workflow produces an immutable evidence bundle attached to its
GitHub Release. The bundle identifies the semantic version, signed tag, commit,
workflow run, artifact hashes, supported platform/Python matrix, SBOM, quality
gates, security finding dispositions, and GEDCOM importer status.

Release evidence contains fictional or aggregate test data only. It must never
contain genealogy payloads, credentials, databases, backups, local paths, logs,
prompt/response bodies, or secret values.

A gate is recorded as `verified`, `failed`, `unavailable`, or `unverified`.
Only `verified` supports a positive compatibility claim. `unavailable` and
`unverified` remain visible limitations in the release notes.
