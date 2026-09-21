# Durable native mutation coordination

Issue #200 supplies one local coordination boundary for native CLI, REPL,
worker, and sidecar processes using the shared publication helpers. It does not
implement remote coordination or the Issue #119 RootsMagic desktop workbench.

## Ownership and privacy

`MutationCoordinator` and its serializable DTOs live in the application layer.
`LocalMutationCoordinator` implements the port using a protected SQLite journal
under the OS account's `.ancestryllm-coordination` directory. Workspace,
configuration, and environment home overrides do not split this namespace.
Bootstrap uses private files directly, avoiding dependency on the publication
helpers it coordinates. POSIX ownership/modes and Windows account ACL checks
protect journal files; Windows reparse points are rejected.

The journal stores operation IDs, keyed opaque resource identities, expected
revisions, intent and idempotency digests, owner/session IDs, fences, deadlines,
leases, artifact references, and operation-owned staging metadata. It contains
no genealogy payloads, credentials, or private host paths. Generated temporary
basenames and sync-generation names are coordination metadata. Actual resource
paths remain private to the invoking process.

Resource locks are acquired in deterministic order. Existing inode identities,
canonical paths, and conservatively normalized missing names identify aliases.
Case-folding and Unicode-equivalent names may therefore contend even on a
case-sensitive filesystem. This conservative contention does not authorize
recovery through a different spelling: recovery still validates the original
selector and parent identity. Independent resource scopes can proceed together.

Bounded leases do not release OS ownership locks. A paused process cannot lose
its scope merely because its lease expires and then race a replacement owner.
Every publication boundary validates ownership; token and monotonic fence
checks reject stale owners. Current helper operations use a five-minute bound;
an expired operation must reconcile before further mutation.

## Publication and restart

The durable lifecycle is `prepared` → `committing` → `committed` or `aborted`.
Interrupted or ambiguous operations retain `recovery_required` ownership.
Matching idempotency keys return a recorded terminal result, or require recovery
of the original intent. Reusing a key with different intent is rejected.

| Integration | Publication and recovery contract |
|---|---|
| Settings persistence | Stage, synchronize, and verify a complete file before replacement; reconcile an interrupted replacement against the owned old/new identities. Existing in-memory synchronization and optimistic revision checks remain. |
| Sync update and rebase | Reserve the generation root across processes; track owned stage members and verify the complete generation before exclusive directory publication. A committed directory remains the successful outcome if cancellation arrives afterward. |
| Shared artifacts and RootsMagic exports | Journal destination, backup, installation, verification, and cleanup ownership. Legacy CLI paths remain compatible. Recovery restores the old complete set or finishes the verified new set. |

**Legacy separate filenames do not become simultaneously visible through one
filesystem operation.** Readers outside the coordinator can observe individual
installations. No incomplete set is recorded committed. New directory-based
publication can provide one atomic visibility boundary for a complete folder.

Recovery is invoked with freshly authorized resource paths; the journal cannot
recover a destination path or revive an expired desktop grant. Parent and object
identities are checked again before changes. Unknown files, replaced staging,
modified content, and missing ownership evidence stop recovery and preserve
ambiguous state. Cleanup removes only proven operation-owned objects, preserving
unrelated files. Narrow creation-to-journal interruption windows deliberately
fail closed when ownership cannot be proved.

Do not remove the journal or staging to clear a conflict. Preserve them and
retry the original authorized selection. See [backup procedures](../ENCRYPTED_BACKUPS.md)
for the distinction between coordination recovery and genealogy backups.

## Stable failures

| Code | Meaning and next action |
|---|---|
| `MUTATION_CONFLICT` | Another process owns the scope. Retry after its operation finishes. |
| `MUTATION_REVISION_STALE` | The authorized revision changed. Inspect and authorize the current resource again. |
| `MUTATION_IDEMPOTENCY_MISMATCH` | A retry key was reused for different intent. Preserve the original operation identity. |
| `MUTATION_LEASE_EXPIRED`, `MUTATION_DEADLINE_EXCEEDED` | The operation exceeded its ownership time budget. Reconcile through a fresh authorized invocation. |
| `MUTATION_OWNER_STALE` | Ownership validation failed; this process cannot publish. |
| `MUTATION_REAUTHORIZATION_REQUIRED` | Recovery needs renewed authority for the original resource. |
| `MUTATION_RECOVERY_REQUIRED`, `MUTATION_RECOVERY_INVALID` | Recovery cannot prove a safe complete state. Preserve the files and journal for investigation. |
| `MUTATION_JOURNAL_UNSAFE`, `MUTATION_JOURNAL_UNAVAILABLE` | The private journal cannot safely coordinate writes. Restore valid account permissions or availability before retrying. |

Errors are path-free. No error grants permission to delete an unknown object or
bypass a resource lock.

## Acceptance evidence and remaining delivery gates

All fixtures are fictional. The following tests are source-level evidence,
not a claim of target-matched packaged acceptance:

| Requirement | Evidence |
|---|---|
| Strict transport-neutral requests, retry intent, terminal outcomes | `test_mutation_coordinator.py`: DTO boundaries, matching/mismatched retries, recorded outcomes. |
| Cross-process contention, independent scopes, aliases, revisions | `test_mutation_coordinator.py`: spawned-process ownership, independent resources, inode/canonical/name aliases, stale revisions. |
| Leases, fencing, cancellation, deadlines | `test_mutation_coordinator.py` and `test_atomic_file_mutation.py`: expired owner cannot transfer live ownership; stale owner rejected; cancellation and timeout boundaries. |
| Single-file process interruption | `test_atomic_file_mutation.py`: forced process exit at persisted and publication checkpoints, existing/absent destinations, repeated reconciliation and unrelated-file preservation. |
| Legacy separate-file recovery | `test_bundle_mutation.py`: installation/backup checkpoints, old/new complete state, terminal outcome, replaced or modified objects preserved. |
| Directory and sync recovery | `test_directory_mutation.py` and `test_sync_mutation_recovery.py`: update/rebase checkpoints, individual member writes, exact owned cleanup, complete generation validation, late cancellation, unexpected-file preservation. |
| Private journal and cross-platform adapter | `test_windows_mutation.py`: ACL parsing and Windows-only native account/ACL checks; coordinator tests cover private bootstrap. |
| Existing integrations | Settings, incremental sync, shared publication, RootsMagic/export and GEDCOM suites exercise their existing behavior through the shared coordinator. |

The CI `mutation-recovery` job runs all six focused modules on Ubuntu, macOS,
and Windows with Python 3.12, and is required by `pr-gate`. Adding that job does
not establish that a hosted run has passed. Canonical local test, lint,
typecheck, security, applicable desktop checks, signed commits, required review,
and hosted gates must be recorded against the final revision before closure.
Target-matched packaged acceptance and power-loss durability are not established
by process-exit tests. Issue #200 remains open until its integrations and required
recovery evidence are reviewed and merged; Issue #119 starts on that merged
foundation without changing either milestone.
