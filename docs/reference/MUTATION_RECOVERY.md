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
Every publication boundary validates ownership and lease validity; token and
monotonic fence checks reject stale owners. Current helper operations use a
five-minute bound. Expiry stops installation and complete-set validation while
the original owner retains authority to roll back and release its locks.

## Publication and restart

The durable lifecycle is `prepared` → `committing` → `committed` or `aborted`.
Interrupted or ambiguous operations retain `recovery_required` ownership.
Matching idempotency keys return a recorded terminal result, or require recovery
of the original intent. Reusing a key with different intent is rejected.

`MutationRequest.retain_outcome` defaults to `true`, preserving recorded outcomes
for callers that reuse idempotency keys. Internal publication helpers use unique
keys and set it to `false`; maintenance retains the newest 256 terminal internal
operations and removes older operation metadata together. Nonterminal recovery
records and retained retry outcomes are never pruned. An account-scoped catalog
lock coordinates resource-lock creation and collection so removing an unused
lock file cannot split ownership across two inodes. Maintenance failures are
retried on later invocations without changing an already recorded outcome.

| Integration | Publication and recovery contract |
|---|---|
| Settings persistence | Stage, synchronize, and verify a complete file before replacement; reconcile an interrupted replacement against the owned old/new identities. Existing in-memory synchronization and optimistic revision checks remain. |
| Sync update and rebase | Reserve the generation root across processes; track owned stage members and verify the complete generation before exclusive directory publication. A committed directory remains the successful outcome if cancellation arrives afterward. If a retry recovers a committed generation, it stops before staging another generation; select the recovered master and manifest to continue. |
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

Copy fallbacks record private-directory and exclusive-file ownership before
copying bytes. Recovery may delete an interrupted copy only after checking its
file identity, single-link state, and private parent. Unsealed bytes never
authorize installation or restoration. Replaced files, hard links, or changed
parents preserve the recovery barrier. Sync destination metadata commits in the
same transaction as ownership, so failed acquisition leaves no orphan row.

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
| `SYNC_RECOVERED_GENERATION` | Recovery completed an interrupted sync generation. Select its recovered master and manifest before starting another generation, so a retry does not reuse the previous generation number. |

Errors are path-free. No error grants permission to delete an unknown object or
bypass a resource lock.

Settings and deployment services translate coordinator conflicts and stale
revisions into their existing `SETTINGS_REVISION_CONFLICT` and
`DEPLOYMENT_REVISION_CONFLICT` contracts. The settings API returns HTTP 409 so
clients can reload and retry. Other coordinator failures use
`SETTINGS_SAVE_FAILED` or `DEPLOYMENT_PERSISTENCE_FAILED`; failed saves leave the
active configuration and revision unchanged.

## Acceptance evidence and remaining delivery gates

All fixtures are fictional. The following tests are source-level evidence,
not a claim of target-matched packaged acceptance:

| Requirement | Evidence |
|---|---|
| Strict transport-neutral requests, retry intent, terminal outcomes | `test_mutation_coordinator.py`: DTO boundaries, matching/mismatched retries, recorded outcomes, bounded internal history and lock collection with retry/recovery authority preserved. |
| Cross-process contention, independent scopes, aliases, revisions | `test_mutation_coordinator.py`: spawned-process ownership, independent resources, inode/canonical/name aliases, stale revisions. |
| Leases, fencing, cancellation, deadlines | `test_mutation_coordinator.py` and `test_atomic_file_mutation.py`: expired owner cannot transfer live ownership; stale owner rejected; cancellation and timeout boundaries. |
| Single-file process interruption | `test_atomic_file_mutation.py`: forced process exit at persisted and publication checkpoints, existing/absent destinations, repeated reconciliation, unrelated-file preservation, and Windows creation-time tunneling without losing file identity. |
| Legacy separate-file recovery | `test_bundle_mutation.py`: installation/backup checkpoints, lease expiry at publication boundaries, forced exit during backup/install/restore copies and after symlink restoration, old/new complete state, terminal outcome, replaced or modified objects preserved. |
| Directory and sync recovery | `test_directory_mutation.py` and `test_sync_mutation_recovery.py`: update/rebase checkpoints, individual member writes, exact owned cleanup, complete generation validation, late cancellation, unexpected-file preservation, and stale retries stopped after recovering a committed generation. |
| Private journal and cross-platform adapter | `test_windows_mutation.py`: ACL parsing and Windows-only native account/ACL checks; coordinator tests cover private bootstrap. |
| Existing integrations | Settings, incremental sync, shared publication, RootsMagic/export and GEDCOM suites exercise their existing behavior through the shared coordinator. |

The CI `mutation-recovery` job runs seven focused modules on Ubuntu, macOS,
and Windows with Python 3.12, and is required by `pr-gate`. Adding that job does
not establish that a hosted run has passed. Canonical local test, lint,
typecheck, security, applicable desktop checks, signed commits, required review,
and hosted gates must be recorded against the final revision before closure.
Target-matched packaged acceptance and power-loss durability are not established
by process-exit tests. Issue #200 remains open until its integrations and required
recovery evidence are reviewed and merged; Issue #119 starts on that merged
foundation without changing either milestone.
