# Application-service contracts

Status: implemented for the `0.3.0` release candidate. These contracts are the
framework-independent boundary shared by the current terminal adapters and the
future FastAPI/Electron adapters. `ARCHITECTURE.md` owns the repository-wide
dependency graph and current-versus-target status.

## Public boundary

The public boundary is intentionally small:

| Module | Ownership |
|---|---|
| `ancestryllm.application.dto` | Strict, immutable, deterministic JSON DTOs, opaque artifact and secret capabilities, and decision/progress records. |
| `ancestryllm.application.operations` | One exact request/result pair for every `DispatchKey`. |
| `ancestryllm.application.ports` | Cancellation, progress, decision, identity-resolution, and quality-resolution protocols. |
| `ancestryllm.application.errors` | Complete mapping from pure domain failures to stable coded application errors and transport envelopes. |
| `ancestryllm.domain.errors` | Framework-independent failure categories and bounded safe detail values. |

The private modules `application._artifacts` and `application._compat` belong to
adapter composition. `application._rootsmagic` owns RootsMagic query runtime
orchestration behind the public operation DTOs and the
`rootsmagic.query.RootsMagicQueryService` compatibility façade. These modules
are not alternate service APIs or operation registries.

Every boundary dataclass is frozen and slotted. `BoundaryDTO.to_json()` emits a
versioned envelope with sorted keys, finite JSON numbers, and a one-megabyte
limit. `BoundaryDTO.from_json()` requires the exact DTO type and rejects
unknown, missing, or incorrectly typed fields. Requests and results contain no
Click, prompt-toolkit, Rich, FastAPI/Pydantic, Electron, provider-SDK,
database-session, callback, exception, or host-filesystem objects.

## Operation inventory

`OPERATION_CONTRACTS` pairs each command dispatch identity with one request and
one result. Import-time and test-time drift checks require exact equality with
the shared command specifications:

| Command | Operations |
|---|---|
| Modules | `modules.list`, `modules.enable`, `modules.disable` |
| RootsMagic | `rootsmagic.list`, `rootsmagic.query`, `rootsmagic.export` |
| GEDCOM | `gedcom.merge`, `gedcom.subtree`, `gedcom.quality`, `gedcom.sync` |
| Prompts | `prompts.list`, `prompts.save`, `prompts.show`, `prompts.render` |
| People | `people.list`, `people.add` |
| Providers | `providers.list`, `providers.create`, `providers.consent`, `providers.revoke` |
| Secrets | `secrets.set`, `secrets.delete`, `secrets.status` |
| OCR | `ocr.extract` |
| Database | `database.backup`, `database.diagnose` |

This inventory is the only application-operation registry. Terminal, HTTP, and
desktop adapters may translate their inputs into these requests but must not
create a second UI-specific registry or redefine result semantics.

`RootsMagicQueryRequest` admits exactly one non-empty direct SQL statement or
natural-language question. Direct SQL is deterministic and provider-free even
when credentials are present. Questions require an explicit non-`none`
provider and model, then reuse the same immutable reader, SQL validator,
authorizer, row bound, timeout, and source-fingerprint checks. The serialized
`RootsMagicQueryResult` contains canonical scalar rows and coded execution
metadata, while progress contains only operation/stage codes and counters.

## Ports and adapter responsibilities

Services depend on five narrow structural protocols:

- `CancellationPort` checks for cooperative cancellation at safe boundaries.
- `ProgressPort` emits operation/stage codes, bounded counters, sequence
  numbers, and optional opaque artifact IDs. It cannot carry genealogy
  content, SQL, credentials, host paths, or arbitrary messages.
- `DecisionPort` returns one declared coded option or explicit cancellation.
- `IdentityResolutionPort` resolves only opaque source/candidate references.
- `QualityResolutionPort` returns one declared coded resolution or
  cancellation.

The current job and cancellation objects are translated by private
compatibility adapters. Future FastAPI and Electron code must implement the
same protocols at their adapter boundary. A port implementation may collect
user input or update presentation state; it does not acquire genealogy,
provider, persistence, or publication ownership.

## Artifact and secret capabilities

Host paths never cross the public application boundary. A trusted adapter
registers a selected input or destination with the private artifact registry
and passes an `ArtifactGrantRef` to a request. Each unpredictable grant is
scoped to one operation and one access mode, can be revoked, and resolves to a
path only inside the owning process. Results return an `ArtifactRef` containing
an unpredictable identity, media type, artifact type, status, bounded size,
and optional digest—not a path.

Output publication is staged, claimed, cancellation-checked, and atomically
published through the hardened publication helpers. Cancellation before
publication removes the staged artifact and preserves any previous
destination. Publication failures map to a sanitized code; partial external
outputs and raw exception details are not returned.

Secrets use a separate write-only `SecretGrantRef`. Secret values remain in the
owning adapter/secret-store boundary and never enter a request, result, error,
progress event, or deterministic JSON envelope. Secret results expose presence
only.

## Provider and genealogy safety

`ProviderSelection` contains identifiers only. The explicit `none` provider is
network-disabled even when credentials or provider SDKs are present. A cloud
selection is not authorization: existing provider policy must still require a
matching explicit consent grant before any disclosure or network call.

The operation DTOs expose deterministic change, conflict, quality, and
provenance records. The implemented service-owned genealogy aggregate owns the
rules that produce those records. Adapters only translate and render them.
RootsMagic inputs remain immutable and RootsMagic/GEDCOM outputs remain
loss-visible and atomically published.

## Stable failure contract

`DomainFailureCode` is complete for the application boundary. Every member has
one `DOMAIN_ERROR_MAPPINGS` entry defining its stable public code, sanitized
message, optional remediation, and exit status. Mapping ignores raw exception
text and admits only allowlisted, bounded, path-free scalar details. Unknown
exceptions are caught at the owning boundary and converted to the generic
internal category before transport rendering.

CLI and REPL compatibility continues to use the existing coded-error rendering.
Future transports serialize the corresponding `ErrorEnvelope`; they must not
invent transport-specific domain codes or expose tracebacks, filesystem
locations, SQL, provider payloads, credentials, or genealogy content.

## Contract validation

`tests/modular/test_application_contracts.py` proves:

- exact operation coverage and deterministic round trips for every request and
  result;
- immutable DTOs and forbidden dependency/type exclusion;
- framework-free imports in an isolated interpreter;
- structural port conformance and legacy cancellation mapping;
- strict JSON, finite-number, bounded-value, and path/content rejection;
- complete stable failure mapping with sanitized envelopes;
- scoped, revocable, opaque artifact grants;
- atomic publication, cancellation preservation, and absence of partial
  external output;
- write-only secret capability use.

The core-contract characterization suite remains the compatibility authority
for shipped CLI/REPL behavior, JSON, errors, consent, network-free `none`,
RootsMagic immutability, rooted/loss-minimal GEDCOM behavior, and existing
artifact/report behavior.
