# AncestryLLM desktop scaffold

This directory contains the UI-only Electron adapter and the packaged control-sidecar supervisor. It intentionally implements no genealogy domain behavior and does not access files, databases, provider credentials, the network, or operating-system services from the renderer. The versioned `window.ancestry` bridge continues to serve deterministic fictional fixtures only.

## Reproducible setup and gates

Use Node `26.5.0` (see `.node-version`), Corepack, and the repository-pinned pnpm `11.9.0`:

```sh
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --dir desktop install --frozen-lockfile
make desktop-check
make desktop-e2e
make desktop-security
```

`desktop-check` runs lint, separated main/preload/renderer type checks, unit tests, and a source-map-free build inspection. `desktop-e2e` launches the actual built Electron app. `desktop-security` runs the high-severity dependency audit, source secret scan, produces `desktop/sbom.cdx.json` (ignored by Git), builds an unpacked directory package after verifying the native sidecar resource, and inspects the resulting `app.asar`, eight packaged Electron fuses, and supported ASAR-integrity metadata. No updater or signing credentials are configured in this scaffold.

## Architecture

The renderer has browser-only TypeScript types and imports. The sandboxed preload exposes the frozen, bounded, async `window.ancestry` API after runtime validation. Main validates IPC senders, serves a fixed `app://bundle` asset/MIME manifest under a restrictive CSP, and globally denies permissions, downloads, child windows, webviews, unexpected navigation, and packaged developer tools. In packaged builds, main privately starts and verifies the control-only native sidecar. Startup failure leaves a sanitized degraded diagnostics state; authenticated session details and bounded manual retry remain main-only and are never added to IPC or the preload bridge. See [the lifecycle and diagnostics guide](../docs/DESKTOP_SIDECAR.md). A later domain transport adapter must consume the application-service contract; do not place domain logic in Electron.

The allowlisted external-link helper is main-process-internal and testable: it accepts only exact `https://github.com` destinations without credentials or a custom port, displays the normalized destination, defaults to cancel, and opens only after explicit confirmation. It is deliberately not part of `window.ancestry`; a renderer-facing external-link workflow requires its own separately reviewed contract.

## Security evidence

| Control | Evidence | Gate |
|---|---|---|
| `TM-R01` | Secure window defaults and weakened-future-window regression cases in `src/main/security-policy.test.ts`; global web-content and session denials in `src/main/index.ts` and `src/main/session-policy.test.ts`; production runtime isolation assertions in `e2e/shell.spec.ts`; actual fuse and ASAR inspection in `scripts/inspect-package-fuses.mjs`. | `make desktop-check`, `make desktop-e2e`, `make desktop-security` |
| `TM-R02` | Exact route/MIME/CSP and traversal-denial tests in `src/main/security-policy.test.ts`; production runtime fetch, WebSocket, service-worker, and child-window denials in `e2e/shell.spec.ts`. | `make desktop-check`, `make desktop-e2e` |
| `TM-I01` | The existing frozen preload surface remains bounded; main-frame sender/origin checks stay in `src/main/index.ts`; E2E asserts that no security-reporting or external-link method leaked into the renderer bridge. Richer IPC proxy schemas, limits, and adversarial sender/race coverage remain owned by issue #101 and the cross-platform suite in #131. | `make desktop-check`, `make desktop-e2e` |
| `TM-U01` | Static package-policy tests in `scripts/package-security.test.mjs`; packaged `app.asar`, fuse, and supported integrity inspection in `scripts/inspect-package-fuses.mjs`. Signing, notarization, updates, provenance, and rollback remain distribution work rather than claims of this scaffold. | `make desktop-security` |
