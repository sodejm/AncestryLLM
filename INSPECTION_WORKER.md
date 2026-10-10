# Offline GEDCOM inspection worker

GEDCOM inspection runs in a dedicated Python child process. The parent owns
admission, supervision, cancellation, and cleanup; the child owns parsing and
creation of a private root-candidate index. This boundary keeps large parser
state out of the long-lived application process and gives every untrusted input
a fixed resource envelope.

## Process and data flow

The parent fingerprints the immutable granted source and performs a streaming
preflight through the standard GEDCOM ingress policy. It then waits for one of
two process slots, creates a mode `0700` lease directory, and starts
`python -m ancestryllm.gedcom.inspection_worker --child` in an ordinary Python
runtime. A frozen native sidecar instead starts its own executable with the
exact `--inspection-worker` flag. The native entry point dispatches that flag
directly to the same private protocol without starting the API server; extra
worker arguments are rejected. This avoids treating a native executable as a
Python interpreter.

The command line contains no source or staging path. A single JSON control
document, limited to 8 KiB, travels over standard input. Standard output and
standard error are discarded. The child receives only the source grant,
request, effective ingress policy, worker limits, and private directory it
needs. The child reconstructs that ingress policy before parsing, so custom
limits remain effective across the process boundary. Results use the existing
typed JSON DTO contract; Python object serialization such as pickle is not
accepted.

The child calls the worker-only local inspection seam, writes root candidates to
a private SQLite index, removes the candidate tuple from the result, and records
the candidate count. The summary is limited to 1 MiB. `root_index_ref` remains
unset in the child result; the parent creates an opaque reference after it has
validated and retained the successful lease. Names and source paths therefore
do not cross the public result or error boundary.

## Enforced ceilings

Limits are inclusive: an input exactly at a ceiling is accepted and one unit
over is rejected. All values must be positive. Concurrency is fixed at two so a
caller cannot relax the application-wide process bound.

| Resource | Default ceiling | Enforcement |
| --- | ---: | --- |
| Source bytes | 512 MiB | Shared GEDCOM ingress policy |
| Physical line bytes | 1 MiB | Shared GEDCOM ingress policy |
| Parsed records | 5,000,000 | Shared GEDCOM ingress policy |
| Parsed record bytes | 16 MiB | Shared GEDCOM ingress policy |
| GEDCOM nesting level | 99 | Shared GEDCOM ingress policy |
| Individuals | 250,000 | GEDCOM service admission |
| Physical lines | 5,000,000 | Parent streaming preflight |
| Cross-reference tokens | 2,000,000 | Parent streaming preflight |
| Queue plus execution time | 120 seconds | Parent monotonic deadline |
| Resident memory | 1 GiB | Parent watchdog on Linux, macOS, and Windows |
| Child address space | 1 GiB | Linux `RLIMIT_AS` |
| Private root index | 256 MiB | Streaming index builder and parent validation |
| Serialized summary | 1 MiB | Child writer and parent validation |
| Control document | 8 KiB | Parent encoder and child reader |
| Failure document | 4 KiB | Child writer and parent parser |
| Concurrent workers | 2 | Process-wide bounded semaphore |

The wall deadline starts before preflight and semaphore acquisition. Queueing,
preflight, process startup, parsing, indexing, and result validation all consume
the same budget. The watchdog samples RSS at intervals no longer than 50 ms. A
platform where RSS cannot be measured fails closed with `WORKER_FAILED`.

Linux combines supervisor RSS checks with an address-space rlimit. macOS uses
the fixed `/bin/ps` executable. Windows uses the operating system process-memory
API. These platform controls have different accounting semantics, so the
portable promise is the supervisor's 1 GiB resident-memory ceiling.

## Privacy and integrity

The child environment contains UTF-8 settings, an application-controlled source
path, and only the minimum Windows process variables when Windows requires them.
Provider credentials, `HOME`, caller `PYTHONPATH`, and dotenv search roots are
not inherited. The worker invokes only the local inspection service and never
selects a provider, preserving the provider `none` network-free contract.

The parent fingerprints the source before work and verifies the same path,
identity, size, and digest after the child exits. It never opens the source for
writing. Lease directories must be mode `0700`; every accepted output must be a
single-link regular file at mode `0600`. Unknown files, SQLite sidecars,
symlinks, permission drift, and oversized files fail closed.

This is a process and credential boundary rather than an operating-system
network sandbox. Its network-free property follows from the fixed local code
path, minimal environment, and absence of provider invocation. Deployments that
require syscall-level network denial should also apply their host sandbox.

## Failure and cleanup behavior

Public failures are serializable, path-free, and use stable codes:

| Condition | Code | Bounded detail |
| --- | --- | --- |
| Physical lines, xrefs, RSS, index, or summary over budget | `WORKER_LIMIT` | `budget` only |
| Queue or execution deadline | `WORKER_TIMEOUT` | None |
| Abrupt exit, invalid output, unavailable RSS, or unclassified child error | `WORKER_FAILED` | None |
| Cooperative parent cancellation | `CANCELLED` | Existing cancellation mapping |

Ingress errors retain only the shared allowlisted coded details. The failure
channel rejects nested values, absolute paths, unknown fields, unknown codes,
and more than eight scalar details.

On cancellation, timeout, crash, output rejection, and ordinary failure, the
parent terminates the child, waits briefly, escalates to kill when required,
reaps it, and removes the staging directory. Existing outputs and the input are
unchanged. A successful directory is transferred to the service lease manager;
it is removed on explicit release, service shutdown, or its 15-minute expiry.

Each invocation also scans at most 32 worker-prefixed directories under the
private scratch root. It removes a directory only when its private owner marker
names a dead supervisor PID. Live leases, malformed markers, and unrelated
directories are preserved. This bounded restart cleanup avoids an unbounded
startup traversal.

## Testing and operations

Deterministic tests inject process polling, RSS readings, clocks, and sleepers
through private call seams. There are no environment-controlled crash, sleep,
or output backdoors in production. Focused tests cover exact-limit admission,
one-over rejection, path-free failure parsing, crash/memory/timeout mapping,
private output checks, forced termination and reaping, and bounded orphan
cleanup.

Entry-routing regressions cover ordinary Python and frozen worker launches,
exclusive native worker dispatch, and rejection of extra arguments. Native
validation must also build the sidecar through `scripts/build_sidecar.py`, run
its normal control smoke test, and inspect fictional GEDCOM input through the
packaged worker. Source-level routing tests alone do not prove the frozen
payload contains every worker dependency.

Operators should investigate repeated `WORKER_FAILED` results as local runtime,
permission, resource-measurement, or child-integrity failures. Source paths and
GEDCOM payloads must stay out of logs and diagnostic messages. Raising a ceiling
requires a reviewed code change and matching tests; no user-facing request can
override the concurrency bound.
