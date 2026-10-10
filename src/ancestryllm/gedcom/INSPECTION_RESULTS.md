# Inspection result ownership

`GedcomService.execute_inspect` supervises the
[offline inspection worker](../../../INSPECTION_WORKER.md) and retains a
[private root index](ROOT_INDEX.md) only after the worker output and immutable
source fingerprint have been verified. Parsing and eager candidate creation are
confined to the child. The parent retains metadata and an opaque `root-index:`
reference, not the complete candidate tuple or an index path in a service DTO.
`summary_result()` exposes the count and at most 100 coded findings.

## Lease and page contracts

Each service instance owns at most eight successful index leases. A lease binds
its reference to the input artifact ID, SHA-256 digest, and candidate count.
`read_inspection_roots` verifies that binding and the lifetime before opening the
index. It accepts queries of at most 128 characters, non-negative scan offsets,
and page limits from 1 through 100. Its transport-neutral `RootCandidateWindow`
contains bounded candidates and a resume offset; it never exposes a host path.

`GedcomJobFacade.root_candidates` signs the continuation with the job, source
fingerprint, normalized query, and offset. A changed query, another job, or an
invalid offset rejects a reused cursor. Substring search may return an empty
page with a continuation because every call scans a bounded window. Exact
identifier lookup has a known total and does not fall back to fuzzy matches.

The default lease lifetime is 900 seconds measured by a monotonic clock. A daemon
timer revokes and erases each index even if no further query arrives. Reads and
revocation share a lock so a reader cannot race deletion. Erasure happens outside
that lock after revocation, keeping job cleanup independent of manager locks.

## Cleanup and composition

`release_inspection` is idempotent. Inspection jobs register it as their result
cleanup callback, covering explicit discard, cancellation after execution,
manager shutdown, and results arriving after a job has been discarded. The
façade also releases a result if cancellation interrupts outcome reporting
before the manager can retain it. Failed admission or timer startup erases the
unretained worker directory.

Composition owners call `GedcomService.close()` after stopping intake and jobs.
The sidecar does this for the service it created. Closing revokes every retained
lease and rejects any in-flight worker result that arrives later; externally
injected services remain their caller's responsibility. A missing, expired, or
source-mismatched lease maps to `GEDCOM_JOB_RESULT_UNAVAILABLE` at the job façade.
Reinspection is the recovery action; private indexes are never durable job data.

Cleanup failures emit only `GEDCOM_INDEX_CLEANUP_FAILED` and the exception class.
They do not emit a path, name, search, or genealogy payload. The worker's bounded
dead-owner scan provides restart cleanup for residual owned directories.

## Compatibility and verification

The eager tuple field remains for the worker-local parser and existing in-memory
adapters. Public service inspection always uses the worker and a lease. No
provider, CLI command, or renderer-specific registry is added.

Fictional-data tests cover summary privacy, bounded reads, source-binding
rejection, repeated release, expiry without requests, timer startup failure,
shutdown during a worker, manager result cleanup, and signed cursor behavior.
These local tests do not qualify a packaged desktop target or replace its manual
keyboard and screen-reader acceptance.
