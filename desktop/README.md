# AncestryLLM desktop scaffold

This directory is the UI-only Electron adapter for the future localhost service boundary. It intentionally implements no genealogy domain behavior and does not access files, databases, provider credentials, the network, or operating-system services from the renderer. The versioned `window.ancestry` bridge currently serves deterministic fictional fixtures only.

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

`desktop-check` runs lint, separated main/preload/renderer type checks, unit tests, and a source-map-free build inspection. `desktop-e2e` launches the actual built Electron app. `desktop-security` runs the high-severity dependency audit, source secret scan, and produces `desktop/sbom.cdx.json` (ignored by Git). No updater or signing credentials are configured in this scaffold.

## Architecture

The renderer has browser-only TypeScript types and imports. The sandboxed preload exposes the frozen, bounded, async `window.ancestry` API after runtime validation. Main validates IPC senders and denies permissions, downloads, child windows, webviews, and navigation. Replace the fictional main-process mock with a transport adapter only after the localhost sidecar application-service contract exists; do not place domain logic in Electron.
