# Internal API contract

Issue #11 establishes the source-level control-plane contract for the first
Electron release planned as `0.5.0`. It is a private, authenticated,
IPv4-loopback FastAPI adapter over the existing application contracts. It is
not a public, LAN, browser, or multi-user API.

The foundation exposes exactly two read-only routes:

- `GET /api/v1/health` verifies the private bearer, API contract, paired app
  build, and token-derived readiness proof.
- `GET /api/v1/capabilities` projects only enabled `ModuleDescriptor` actions
  that also have a registered `CommandExecutor` handler.

There is no generic command-dispatch route and no genealogy, GEDCOM,
RootsMagic, provider, secret, storage, file, job, or other domain route in this
slice. Those routes remain separately owned follow-on work and must adapt the
same transport-neutral application services.

## Security boundary

Every request is authenticated before route or body processing. The adapter
requires an exact API version and paired app build, accepts only
`127.0.0.1[:port]`, rejects browser, cookie, origin, and proxy metadata, and
returns strict, sanitized error envelopes. Uvicorn is configured for loopback
port `0`, a bounded graceful shutdown, disabled access logging, and no trusted
proxy headers. Runtime OpenAPI and interactive documentation routes are
disabled.

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

This source-level work may be developed in an isolated `0.5.0` worktree, but it
must not modify or displace the `0.4.0` release work. Version `0.5.0` cannot be
released until Issue #193 is complete and `0.4.0` has been released.
