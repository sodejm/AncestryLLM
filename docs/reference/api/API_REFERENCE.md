# Internal API contract

Issue #11 established the source-level control-plane contract released with the
first Electron shell in `0.5.0`. The `0.6.0` release tree adds source-level
gated contracts for atomic non-secret settings and write-only credential
management (#105), read-only startup diagnostics and fail-closed mutation
gating (#107), provider profiles, endpoint tests, and consent administration
(#108), UI-neutral job lifecycle (#104), bounded transient chat (#110), and its
owner-scoped audited streaming lifecycle and bounded replay transport (#111).
The `0.7.0` release tree adds source-level, capability-scoped GEDCOM operation
and result contracts (#114).
This remains a private, authenticated, IPv4-loopback FastAPI adapter over
transport-neutral application contracts. It is not a public, LAN, browser, or
multi-user API.

The supported packaged foundation exposes two read-only routes:

- `GET /api/v1/health` verifies the private bearer, API contract, paired app
  build, and token-derived readiness proof.
- `GET /api/v1/capabilities` projects only enabled `ModuleDescriptor` actions
  that also have a registered `CommandExecutor` handler.

The source-level gated #107 code adds one read-only path:

- `GET /api/v1/startup-diagnostics` returns schema-v1 configuration,
  SQLCipher, keyring, and workspace status with stable codes, reviewed
  remediation, restart and mutation-blocking flags, and normalized platform
  labels.

The source-level gated #105 code adds four fixed path shapes and five operations:

- `GET /api/v1/settings` returns the complete versioned five-setting catalog
  and current optimistic revision.
- `PATCH /api/v1/settings` applies an exact-revision patch atomically.
- `GET /api/v1/secrets/{reference}/status` returns only `present`, `missing`,
  or `unavailable`.
- `POST /api/v1/secrets/{reference}/set` accepts one write-only value.
- `POST /api/v1/secrets/{reference}/delete` deletes and verifies absence.

The `0.6.0` source-level gated #108 code adds six fixed path shapes and
operations:

- `GET /api/v1/provider-configuration` returns provider metadata, safe endpoint
  defaults, profile summaries, consent summaries, secret presence, and both
  optimistic revisions.
- `POST /api/v1/provider-endpoints/validate` explicitly tests one policy-bound
  endpoint and returns a redacted destination identity.
- `POST /api/v1/provider-profiles` creates one profile against the current
  profile revision and a matching endpoint-test identity.
- `POST /api/v1/consents/preview` returns the complete requested consent scope,
  warnings, and budget without mutating state.
- `POST /api/v1/consents` creates only the exact current preview against the
  current consent revision.
- `POST /api/v1/consents/{name}/revoke` explicitly revokes a named grant.

The `0.6.0` source-level gated #104 code adds five fixed job-lifecycle path
templates:

- `GET /api/v1/jobs` returns bounded schema-v1 snapshots.
- `GET /api/v1/jobs/{job_id}` returns one current snapshot.
- `POST /api/v1/jobs/{job_id}/cancel` idempotently requests cooperative
  cancellation and distinguishes a pending safe point from terminal state.
- `GET /api/v1/jobs/{job_id}/events` streams bounded schema-v1 events over SSE
  and supports `Last-Event-ID` replay.
- `POST /api/v1/jobs/shutdown` performs the main-process safe-quit preflight.

The `0.6.0` source-level gated #110 code adds four fixed transient-chat path
templates and five operations:

- `GET /api/v1/chat/capability` returns the exact schema-v1 limits and disabled
  capabilities.
- `POST /api/v1/chat/sessions` preflights one exact named provider profile,
  model, purpose, data-class set, and optional named consent grant.
- `GET /api/v1/chat/sessions/{session_id}` returns one path-free session
  summary without message content.
- `DELETE /api/v1/chat/sessions/{session_id}` discards the session and every
  in-memory message it owns.
- `POST /api/v1/chat/sessions/{session_id}/runs` performs one bounded,
  synchronous policy-enforced generation and commits history only after a
  successful provider result.

The `0.6.0` source-level gated #111 code adds three fixed transient-chat stream
path templates and operations:

- `POST /api/v1/chat/sessions/{session_id}/streams` starts one owner-scoped
  streaming run after the same profile, policy, credential, and consent
  preflight.
- `GET /api/v1/chat/sessions/{session_id}/streams/{run_id}/events` streams
  strict schema-v1 lifecycle events over SSE and resumes only through
  `Last-Event-ID`.
- `POST /api/v1/chat/sessions/{session_id}/streams/{run_id}/cancel`
  idempotently requests cancellation of that exact session-owned run.

The `0.7.0` source-level gated #114 code adds six fixed, capability-scoped
GEDCOM path templates:

- `POST /api/v1/gedcom/inspect` submits a deterministic source inspection.
- `POST /api/v1/gedcom/merge` submits a loss-minimal merge into new artifacts.
- `POST /api/v1/gedcom/subtree` submits a rooted subtree extraction.
- `POST /api/v1/gedcom/quality` submits rooted quality analysis.
- `POST /api/v1/gedcom/sync` submits a typed incremental synchronization.
- `GET /api/v1/gedcom/jobs/{job_id}/result` returns one completed, bounded,
  path-free structured result while it remains in process memory.

The default packaged sidecar has twenty-five exact path templates. The
committed contract composition explicitly supplies the scoped GEDCOM artifact
registry and has thirty-one. A composition without that registry omits all six
GEDCOM routes instead of advertising operations that cannot resolve grants.
There is no generic command or route dispatcher and no genealogy, RootsMagic,
storage, host-file, direct-provider, tool-capable, or other domain-operation
route. The fixed chat and explicitly composed GEDCOM routes are the only
provider-execution surfaces. Credential and provider-configuration routes
cannot read a secret value or execute a provider. General job routes expose
lifecycle metadata only; they do not admit work. Separately owned follow-on
work must adapt the same transport-neutral application services.

## Security boundary

Every request is authenticated before route or body processing. The adapter
requires an exact API version and paired app build, accepts only
`127.0.0.1[:port]`, rejects browser, cookie, origin, and proxy metadata, and
returns strict, sanitized error envelopes. Uvicorn is configured for loopback
port `0`, a bounded graceful shutdown, disabled access logging, and no trusted
proxy headers. Runtime OpenAPI and interactive documentation routes are
disabled.

Settings reads expose only reviewed metadata and non-secret current values.
Patch requests require schema version 1, the last visible revision, and
allowlisted keys; unknown, sensitive, invalid, or stale changes fail before an
atomic `AppConfig` replacement. Credential references are exact allowlisted
identifiers. Secret values are marked `writeOnly` in OpenAPI, excluded from all
responses and examples, and never placed in error details, correlation data,
logs, or generated fixtures. The OS keyring is the only writable credential
authority. Environment-managed credentials are read-only; an unavailable or
locked keyring fails closed with a stable sanitized code and no plaintext
fallback. The packaged sidecar selects keyring-only mode and cannot resolve an
environment-managed credential; the read-only environment fallback remains an
explicit CLI/headless facility.

The startup report is side-effect-free and path-free. Unknown schemas,
components, fields, statuses, or codes fail response validation. A component
with `blocks_mutations: true` prevents settings and credential changes with
`STARTUP_MUTATION_BLOCKED`; it never triggers configuration repair, database
initialization, key creation or replacement, or a plaintext storage fallback.
The report excludes tokens, environment values, usernames, hostnames, absolute
or temporary paths, records, prompts, payloads, response bodies, raw
exceptions, and stacks.

Provider metadata and profile summaries contain no key value. Local endpoint
tests permit only explicit loopback addresses; cloud endpoints are fixed to the
reviewed provider URL. The validator rejects credentials in URLs, fragments,
parameters, query strings, redirects, proxy inheritance, private or link-local
remote destinations, resolution changes, and TLS hostname or certificate
failures. It connects directly to the resolved numeric address, repeats DNS
resolution, and returns only a SHA-256 destination identity. Profile save,
consent creation, and execution recheck that identity. Missing bindings,
revision conflicts, or stale previews fail with stable sanitized codes.

Consent preview and creation expose the exact provider, profile, model,
modules, purposes, data classes, retention, living-person and remote-retention
warnings, and optional budget. Creation accepts only the complete current
preview; revocation is explicit. Neither a stored key nor a saved profile
selects a provider, grants consent, or executes a request.

Transient chat accepts only a named provider profile and its exact model. It
rejects `provider=none`, direct provider identifiers, missing or stale profiles,
incompatible capabilities, missing cloud consent, revoked consent, and changed
endpoint identity before remote generation. Every run re-fetches consent and
re-enters the central provider preflight. At most 32 sessions are active; each
session has at most 32 user/assistant messages, each message has at most 16,384
characters, and total context has at most 65,536 characters. Output tokens,
temperature, timeout, and safe retries are independently bounded. The fixed
system instruction treats input as untrusted data, disables tools, files,
databases, shells, plugins, and external-service autonomy, and labels generated
text as advisory rather than evidence. Sessions and messages exist only in
process memory, are cleared on explicit teardown and sidecar shutdown, and are
excluded from audit payloads. Provider audit retains only privacy-minimal
identity, outcome, timing, and digest metadata. Degraded startup blocks session
creation and runs before provider access.

Streaming runs retain a bounded 256 KiB process-memory replay and publish only
strict owner-scoped schema-v1 `active`, `first-token`, `delta`, `cancelling`,
`completed`, `interrupted`, and `failed` events with monotonically increasing
sequences. The SSE route rejects an absent or wrong session owner, an unknown
run, malformed cursors, and replay gaps; it never accepts a query-string cursor
or silently skips an event. Cancellation, sidecar shutdown, and startup
reconciliation each converge on exactly one payload-free terminal audit
outcome. A stream never retries provider execution after output begins, and
receipts and audit records exclude prompt and response content.

GEDCOM operations accept opaque, operation-scoped artifact grants rather than
host paths. Offline selection rejects provider authority; local Ollama requires
an explicit profile and model but no remote-data consent, while cloud providers
also require an explicit consent identifier. Execution selects the named
profile and re-enters the central provider preflight before any cloud call.
Inputs remain immutable, outputs are new artifacts, supported GEDCOM versions
are checked at the boundary, and structured responses omit record content and
paths. Completed results are retained only by the process that executed the
job; after restart, a durably known completed job returns the stable
`GEDCOM_JOB_RESULT_UNAVAILABLE` code rather than being misreported as unknown.

Job snapshots and events use strict schema version 1 and expose only bounded,
sanitized status, progress, artifact-reference, and error fields. Sequence
numbers increase monotonically per job. Each job retains at most 256 events;
the service admits at most 32 subscribers per job, gives each subscriber a
64-event queue, and returns at most 1,000 snapshots. Payload and string bounds
are validated at each boundary. `Last-Event-ID` resumes from retained history;
an expired gap returns `JOB_EVENT_REPLAY_EXPIRED` with HTTP 410 so the client
can fetch a fresh snapshot rather than silently miss state. Slow subscribers
overflow independently and cannot block workers or manager locks. Cancellation
is cooperative, and persistence plus startup reconciliation guarantee exactly
one terminal outcome without replaying side effects.

The shutdown preflight is process control, not a configuration mutation. It
waits for active work within a bounded interval and fails closed while work is
still active, including work pending a cancellation-safe point. It remains
available during degraded startup because that composition admits no
process-local jobs; all other degraded-state mutation gates remain unchanged.

The bearer and paired build identities are immutable constructor inputs for a
private supervisor channel. Issue #225 implements that packaged channel: the
Electron main process generates a fresh URL-safe 256-bit bearer for every
launch and delivers it through bounded private stdin, not arguments,
environment, files, or renderer-visible state. The sidecar binds an ephemeral
IPv4 loopback port and emits token-free readiness metadata; Electron verifies
the paired build and a token-derived health proof before opening the packaged
window. See [Packaged desktop sidecar](../DESKTOP_SIDECAR.md) for lifecycle,
diagnostics, native targets, and remaining release gates.

## Deterministic OpenAPI

The committed contract is [`openapi-v1.json`](../../api/openapi-v1.json). It explicitly
pins OpenAPI 3.1.0, so FastAPI default-version changes cannot silently alter
generated internal clients. Regenerate it from authoritative Pydantic models
and FastAPI routes with:

```bash
.venv/bin/python -m ancestryllm.api.openapi --write
```

Verify that the committed artifact is exact with:

```bash
.venv/bin/python -m ancestryllm.api.openapi --check
```

The artifact is for generated, paired internal clients. The running application
does not expose `/openapi.json`, `/docs`, or `/redoc`.

## Release boundary

The health and capability contract remains in the bounded control shell. The
settings, credential-management, startup-diagnostic, provider-profile,
endpoint-test, consent-administration, job-lifecycle, and transient-chat
operations are source-level gated work in `0.6.0`. Issue #114 adds the
capability-scoped GEDCOM submission and result composition in `0.7.0`. These
gated surfaces are not part of the supported packaged surface without the
applicable desktop packaging, security, and exact-head verification evidence.
The general job contract has no producer or submission route; only the
explicitly composed GEDCOM adapter submits GEDCOM work. Issue #111 gives the
chat contract fixed streaming routes and a source-level Electron Main/preload
bridge. Issue #112 consumes that bridge in a bounded renderer conversation
with explicit provider/privacy state and safe Markdown presentation, while
adding no tool surface or genealogy integration. Issue #131 owns
target-matched packaged evidence.
Presence in the committed OpenAPI artifact does not enable a public API,
generic provider call, or genealogy workflow. Consent administration never
replaces the execution-time policy check.
