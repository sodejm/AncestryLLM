# Application-service contracts

Status: implemented for the current source release line. These contracts are
the framework-independent boundary shared by the current terminal adapters and
the FastAPI/Electron foundations. `ARCHITECTURE.md` owns the repository-wide
dependency graph and current-versus-target status.

## Public boundary

The public boundary is intentionally small:

| Module | Ownership |
|---|---|
| `ancestryllm.application.dto` | Strict, immutable, deterministic JSON DTOs, opaque artifact and secret capabilities, and decision/progress records. |
| `ancestryllm.application.operations` | Exact command request/result pairs plus reusable transport-neutral GEDCOM inspection and decision DTOs. |
| `ancestryllm.application.gedcom_jobs` | The bounded asynchronous façade shared by GEDCOM transports. |
| `ancestryllm.application.ports` | Cancellation, progress, decision, identity-resolution, and quality-resolution protocols. |
| `ancestryllm.application.errors` | Complete mapping from pure domain failures to stable coded application errors and transport envelopes. |
| `ancestryllm.domain.errors` | Framework-independent failure categories and bounded safe detail values. |

The private modules `application._artifacts`, `application._compat`,
`application._rootsmagic`, and `application._rootsmagic_export` belong to
application composition. `application._rootsmagic` owns RootsMagic query
runtime orchestration behind the public operation DTOs and the
`rootsmagic.query.RootsMagicQueryService` compatibility façade.
`application._rootsmagic_export` owns export validation, staging, and atomic
publication behind the public export boundary and legacy exporter compatibility
façade. These modules are not alternate service APIs or operation registries.

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
| Deployment | `deployment.modes`, `deployment.status`, `deployment.preview`, `deployment.switch`, `deployment.diagnose`, `deployment.metadata` |
| Database | `database.backup`, `database.diagnose` |

This inventory is the only application-operation registry. Terminal, HTTP, and
desktop adapters may translate their inputs into these requests but must not
create a second UI-specific registry or redefine result semantics.

`GedcomInspectRequest` and `GedcomInspectResult` are reusable façade contracts,
not command routes. Their presence does not create a second operation registry.

`RootsMagicQueryRequest` admits exactly one non-empty direct SQL statement or
natural-language question. Direct SQL is deterministic and provider-free even
when credentials are present. Questions require an explicit non-`none`
provider and model, then reuse the same immutable reader, SQL validator,
authorizer, row bound, timeout, and source-fingerprint checks. The serialized
`RootsMagicQueryResult` contains canonical scalar rows and coded execution
metadata, while progress contains only operation/stage codes and counters.

The reusable RootsMagic consumer surface also exposes sanitized
`RootsMagicSourceSummary`, `RootsMagicQueryDefinition`,
`RootsMagicResultPage`, and `RootsMagicExportArtifact` DTOs. Together with
`RootsMagicQueryRequest`, these are the stable application-owned values for a
future workbench adapter: opaque source references replace host paths, query
definitions carry a finite parameter schema, pages are explicitly bounded,
and exports return artifact references rather than destinations. The current
CLI/REPL direct-SQL behavior remains an application-service compatibility
contract. A future renderer must expose only allowlisted query definitions and
must translate schema-validated parameters to that trusted service request at
the adapter/application composition boundary; it must not expose raw SQL or
interpret file grants in the reusable RootsMagic core.

## GEDCOM operation façade

The public GEDCOM boundary exposes `GedcomInspectRequest` and
`GedcomInspectResult`, `MergeRequest` and `MergeResult`, `SubtreeRequest` and
`SubtreeResult`, `QualityRequest` and `QualityResult`, `SyncRequest` and
`SyncResult`, and `MergeDecisionRequest`. Requests carry only purpose-bound
`ArtifactGrantRef` values. Results, progress, and coded failures are bounded,
serializable, and path-free; they never contain whole genealogy trees or
arbitrary callbacks.

GEDCOM 5.5.5 is the default output format. Callers may deliberately request
5.5.1 compatibility, and publication never overwrites an input artifact. The
merge decision contract declares `retain-both` as its conservative default, so
a missing or cancelled decision preserves conflicting evidence. Optional AI
adjudication requires an explicit `ProviderSelection`, the modular
`LLMService`, and the existing policy and consent checks. `provider=none`
remains deterministic and network-free even when ambient credentials exist.

`GedcomJobFacade` submits all five operations through the shared bounded job
lifecycle. Purpose-grant IDs become resource-lock keys; progress and
cooperative cancellation pass through the application ports; domain failures
retain their stable public codes; and cancellation becomes the lifecycle's
cancelled state. A typed operation result is available only after completion.

The authenticated FastAPI adapter exposes only the fixed
`POST /api/v1/gedcom/inspect`, `/merge`, `/subtree`, `/quality`, and `/sync`
routes plus `GET /api/v1/gedcom/jobs/{job_id}/result`. It translates strict
transport payloads into the same application requests used by the CLI and REPL.
It does not expose private GEDCOM engines, a generic command registry, renderer
paths, or record trees.

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

Command results preserve that rule: tabular artifact listings contain only
`ArtifactRef` fields, and file-producing commands return a primary artifact
plus any related artifacts as opaque references. Terminal adapters render
those references but do not recover or expose their adapter-owned paths.

Output publication is staged, claimed, cancellation-checked, and atomically
published through the hardened publication helpers. Cancellation before
publication removes the staged artifact and preserves any previous
destination. Publication failures map to a sanitized code; partial external
outputs and raw exception details are not returned.

`MediatedOperationRequest` and `MediatedOperationResult` extend this capability
model without adding an operation registry. A request binds one unpredictable
operation ID, one allowlisted operation code, `local-container` or
`remote-service` transport, 1-16 unique read grants, and 1-8 unique write
grants. Every grant must name that same operation and exact access mode. The
result returns only ready `ArtifactRef` values for the same operation ID.
Both DTOs are strict, immutable, deterministic, serializable, and path-free.

Electron Main uses the corresponding shared desktop shape to select one of two
trusted adapters. The local adapter may receive only private staged paths,
fixed container paths, and an exact mount plan; those implementation objects do
not enter the application DTO. The remote adapter may receive only bounded
single-use streams with verified byte counts and digests, never host paths.
This is adapter composition around the existing application inventory, not a
second desktop or transport-specific API. The private artifact registry also
rejects non-canonical paths, symbolic links, hard-link aliases, and identity
changes before resolving a capability.

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
- scoped, revocable, opaque artifact grants, including link and replacement
  rejection at resolution;
- strict, deterministic, path-free mediated-operation request/result round
  trips with local/remote transport selection and access-bound grant checks;
- atomic publication, cancellation preservation, and absence of partial
  external output;
- write-only secret capability use.

The GEDCOM façade evidence adds:

- `tests/modular/test_gedcom_service_contracts.py` for purpose-grant execution,
  version selection, non-overwrite rules, conservative merge decisions,
  explicit provider policy, offline socket denial, cancellation, and atomic
  publication;
- `tests/modular/test_gedcom_job_facade.py` for bounded lifecycle submission,
  resource exclusion, progress, stable coded failures, cancellation state, and
  completed-only typed results;
- `tests/api/test_gedcom_operations.py` for authenticated fixed-route DTO
  translation and path-free job/result envelopes; and
- the CLI/REPL boundary suites for parity with the same typed service requests
  and stable coded errors.

The core-contract characterization suite remains the compatibility authority
for shipped CLI/REPL behavior, JSON, errors, consent, network-free `none`,
RootsMagic immutability, rooted/loss-minimal GEDCOM behavior, and existing
artifact/report behavior.
