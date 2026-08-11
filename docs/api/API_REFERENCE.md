# Internal API contract

Issue #11 established the source-level control-plane contract released with the
first Electron shell in `0.5.0`. Issue #105 adds the unreleased 0.6 contract for
atomic non-secret settings and write-only credential management. This remains a
private, authenticated, IPv4-loopback FastAPI adapter over transport-neutral
application contracts. It is not a public, LAN, browser, or multi-user API.

The released foundation exposes two read-only routes:

- `GET /api/v1/health` verifies the private bearer, API contract, paired app
  build, and token-derived readiness proof.
- `GET /api/v1/capabilities` projects only enabled `ModuleDescriptor` actions
  that also have a registered `CommandExecutor` handler.

The unreleased #105 source adds four fixed path shapes and five operations:

- `GET /api/v1/settings` returns the complete versioned five-setting catalog
  and current optimistic revision.
- `PATCH /api/v1/settings` applies an exact-revision patch atomically.
- `GET /api/v1/secrets/{reference}/status` returns only `present`, `missing`,
  or `unavailable`.
- `POST /api/v1/secrets/{reference}/set` accepts one write-only value.
- `POST /api/v1/secrets/{reference}/delete` deletes and verifies absence.

Together, the API has six exact path templates. There is no generic command or
route dispatcher and no genealogy, GEDCOM, RootsMagic, provider execution,
storage, file, job, or other domain route. The credential routes cannot read a
secret value. Separately owned follow-on work must adapt the same
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
fallback.

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

The committed contract is [`openapi-v1.json`](openapi-v1.json). It explicitly
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
shell. The settings and credential-management operations are source-level work
for `0.6.0`; they are not a released user surface until the applicable desktop
packaging, security, and exact-head verification gates pass. Their presence in
the committed OpenAPI artifact does not enable a public API, provider call,
cloud consent, or genealogy workflow.
