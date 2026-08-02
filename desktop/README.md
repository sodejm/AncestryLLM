# AncestryLLM desktop shell

This directory contains the UI-only Electron adapter and the packaged control-sidecar supervisor. It intentionally implements no genealogy domain behavior and does not access files, databases, provider credentials, the network, or operating-system services from the renderer. The frozen versioned `window.ancestry` bridge exposes exactly six control methods: application information, startup diagnostics, capabilities, bounded sidecar retry, preference reads, and optimistic-concurrency preference updates. Development uses deterministic fictional fixtures; packaged main is the sole authenticated client for the fixed capabilities route. Packaged main stores the bounded local-preference schema in `preferences.json` beneath Electron's OS app-data directory. The renderer never receives that path and has no storage access.

The supported 0.5.0 product surface is a one-time local welcome on Home, a temporary Home-based welcome review, Diagnostics, a sanitized capability summary, and local visual Settings only. It has no genealogy, files, jobs, chat, providers, cloud accounts, or updater controls. See the [desktop shell guide](../docs/DESKTOP_SHELL.md) for first-run behavior, supported targets, manual installation, unsigned-artifact limits, and recovery guidance.

The persisted schema contains only color scheme, reduced-motion choice, onboarding completion, schema version, and optimistic revision. `onboardingCompleted` is internal workflow state, not a Settings control. Continue persists that flag through the existing bridge, and a new application process skips the welcome only after a fresh valid snapshot reports completion. Conflicts, unavailable or malformed responses, and corrupt or unsupported storage fail closed and do not silently unlock or overwrite the file. Writes are validated, serialized, and atomically replace the file. Missing or supported legacy data receives safe defaults. Provider configuration, accounts, file grants, genealogy data, prompts, payloads, and secrets are never preference fields.

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

`desktop-check` runs lint, separated main/preload/renderer type checks, unit tests, and a source-map-free build inspection. `desktop-e2e` builds the production renderer and launches it in Electron with a deterministic fictional mock bridge; it is not an installer or literal packaged-executable test. `desktop-security` runs the high-severity dependency audit, source secret scan, produces `desktop/sbom.cdx.json` (ignored by Git), builds an unpacked directory package after verifying the native sidecar resource, and inspects the resulting `app.asar`, eight packaged Electron fuses, and supported ASAR-integrity metadata. Local and ordinary CI builds remain unsigned verification inputs. Full production/trusted binary signing is deferred until v1.0.0: official `0.x` installer builds are unsigned, while local or explicitly manual `0.x` builds may be self-signed. The tag release uses `electron-builder.release.yml` only after exact-head gates pass; it builds the four manual-installer rows documented in the [release runbook](../docs/RELEASING.md), then binds their declared signing mode, checksums, SBOMs, evidence, and provenance before publication. No updater feed, background update, staged rollout, or automatic rollback is configured.

## Architecture

The renderer has browser-only TypeScript types and imports. The sandboxed preload exposes the frozen, bounded, async `window.ancestry` API after request and response runtime validation. Main validates IPC senders, serves a fixed `app://bundle` asset/MIME manifest under a restrictive CSP, and globally denies permissions, downloads, child windows, webviews, unexpected navigation, and packaged developer tools. In packaged builds, main privately starts and verifies the control-only native sidecar. Startup failure crosses the bridge only as sanitized diagnostics; retry is bounded by the main-owned supervisor, and authenticated session details never enter IPC or the preload bridge. See [the lifecycle and diagnostics guide](../docs/DESKTOP_SIDECAR.md). A later domain transport adapter must consume the application-service contract; do not place domain logic in Electron.

The allowlisted external-link helper is main-process-internal and testable: it accepts only exact `https://github.com` destinations without credentials or a custom port, displays the normalized destination, defaults to cancel, and opens only after explicit confirmation. It is deliberately not part of `window.ancestry`; a renderer-facing external-link workflow requires its own separately reviewed contract.

## Security evidence

| Control | Evidence | Gate |
|---|---|---|
| `TM-R01` | Secure window defaults and weakened-future-window regression cases in `src/main/security-policy.test.ts`; global web-content and session denials in `src/main/index.ts` and `src/main/session-policy.test.ts`; production runtime isolation assertions in `e2e/shell.spec.ts`; actual fuse and ASAR inspection in `scripts/inspect-package-fuses.mjs`. | `make desktop-check`, `make desktop-e2e`, `make desktop-security` |
| `TM-R02` | Exact route/MIME/CSP and traversal-denial tests in `src/main/security-policy.test.ts`; production runtime fetch, WebSocket, service-worker, and child-window denials in `e2e/shell.spec.ts`. | `make desktop-check`, `make desktop-e2e` |
| `TM-I01` | The frozen preload surface is limited to six typed control methods; main validates sender, request, and response schemas; E2E asserts that no security-reporting, external-link, or domain method leaked into the renderer bridge. Cross-platform assurance remains owned by issue #131. | `make desktop-check`, `make desktop-e2e` |
| `TM-U01` | Static package-policy tests in `scripts/package-security.test.mjs`; packaged `app.asar`, fuse, and supported integrity inspection in `scripts/inspect-package-fuses.mjs`. Provenance, target execution, and installation remain `0.x` release gates; trusted signing and notarization become mandatory at v1.0.0. Updater behavior is excluded from 0.5.0. | `make desktop-security` |
