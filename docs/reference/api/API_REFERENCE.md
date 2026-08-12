# Internal API contract

Issue #11 established the source-level control-plane contract released with the
first Electron shell in `0.5.0`. Issue #105 adds the unreleased 0.6 contract for
atomic non-secret settings and write-only credential management. Issue #107
adds the read-only startup-diagnostics contract and fail-closed mutation gate.
Issue #108 adds the provider-profile, endpoint-test, and consent-administration
contract. This remains a private, authenticated, IPv4-loopback FastAPI adapter
over transport-neutral application contracts. It is not a public, LAN,
browser, or multi-user API.

The released foundation exposes two read-only routes:

- `GET /api/v1/health` verifies the private bearer, API contract, paired app
  build, and token-derived readiness proof.
- `GET /api/v1/capabilities` projects only enabled `ModuleDescriptor` actions
  that also have a registered `CommandExecutor` handler.

The unreleased #107 source adds one read-only path:

- `GET /api/v1/startup-diagnostics` returns schema-v1 configuration,
  SQLCipher, keyring, and workspace status with stable codes, reviewed
  remediation, restart and mutation-blocking flags, and normalized platform
  labels.

The unreleased #105 source adds four fixed path shapes and five operations:

- `GET /api/v1/settings` returns the complete versioned five-setting catalog
  and current optimistic revision.
- `PATCH /api/v1/settings` applies an exact-revision patch atomically.
- `GET /api/v1/secrets/{reference}/status` returns only `present`, `missing`,
  or `unavailable`.
- `POST /api/v1/secrets/{reference}/set` accepts one write-only value.
- `POST /api/v1/secrets/{reference}/delete` deletes and verifies absence.

The unreleased #108 source adds six fixed path shapes and operations:

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

Together, the API has thirteen exact path templates. There is no generic
command or route dispatcher and no genealogy, GEDCOM, RootsMagic, provider
execution, storage, file, job, or other domain route. The credential and
provider-configuration routes cannot read a secret value. Separately owned
follow-on work must adapt the same
transport-neutral application services.

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

The health and capability contract shipped with the bounded `0.5.0` control
shell. The settings, credential-management, startup-diagnostic,
provider-profile, endpoint-test, and consent-administration operations are
source-level work for `0.6.0`; they are not a released user surface until the
applicable desktop packaging, security, and exact-head verification gates pass.
Their presence in the committed OpenAPI artifact does not enable a public API,
provider call, or genealogy workflow. Consent administration never replaces
the execution-time policy check.
