# AncestryLLM desktop shell

This directory contains the UI-only Electron adapter and the packaged
control-sidecar supervisor. It intentionally implements no genealogy domain
behavior and does not access files, databases, provider credentials, the
network, or operating-system services from the renderer. The versioned
`window.ancestry` bridge retains the six 0.5 control methods for application
information, startup diagnostics, capabilities, bounded sidecar retry,
preference reads, and optimistic-concurrency preference updates. Unreleased
Issue #103 adds three path-free file-grant methods, Issue #105 adds five fixed
settings and credential methods, and Issue #108 adds six fixed provider-profile,
endpoint-test, and consent methods. Development uses deterministic fictional
fixtures; packaged main is the sole authenticated client for the fixed sidecar
routes. Packaged main stores the bounded local-preference schema in
`preferences.json` beneath Electron's OS app-data directory. The renderer never
receives that path and has no storage access.

The supported 0.5.0 product surface is a one-time local welcome on Home, a temporary Home-based welcome review, Diagnostics, a sanitized capability summary, and local visual Settings only. It has no genealogy, files, jobs, chat, providers, cloud accounts, or updater controls. See the [desktop shell guide](../docs/DESKTOP_SHELL.md) for first-run behavior, supported targets, manual installation, unsigned-artifact limits, and recovery guidance.

Unreleased Issue #106 adds the reusable, responsive presentation shell for the
0.6 desktop work. Its `AppRoute`, `NavigationItem`, `CapabilityGate`,
`AsyncState`, `CodedErrorView`, and dialog-focus contracts provide persistent
navigation, a workspace header, context help, deterministic keyboard focus,
and plain-language loading, empty, offline, degraded, error, success, and
permission-denied states. These components do not call the bridge, network,
filesystem, sidecar, or Electron APIs and do not grant capabilities or decide
service policy. Development and review use only the versioned bridge plus
fictional fixtures.

Unreleased Issue #107 makes that first-run surface explicitly local-only and
fail closed. **Local Desktop** is the recommended and only available choice;
Connect Remote and Host Remote remain visibly advanced but unavailable. A
schema-v1 startup report covers configuration, SQLCipher, keyring, and workspace
readiness using stable codes and sanitized remediation. Any blocking component
keeps settings, credentials, and capabilities read-only. The one manual retry
rechecks existing state without initializing a database, overwriting a key,
falling back to plaintext, or widening a listener.

Unreleased Issue #108 adds separate **Local Providers**, **Cloud Providers**,
and **Consent & Privacy** sections without adding provider execution. The
renderer may request an explicit endpoint test, save a profile only against the
tested endpoint identity and current revision, preview the complete consent
scope, create that exact preview, and revoke consent. Secrets remain blank,
write-only fields managed through Issue #105's credential boundary; presence of
a stored key cannot select a provider or grant consent.

Issue #103 is an Unreleased security foundation, not a new 0.5 domain workflow. Its reusable selected-file card displays only a safe basename, byte size, kind, and replacement status. Electron main owns the native open/save dialogs, random opaque grant identifiers, path map, purpose and access checks, lifecycle revocation, input fingerprints, explicit replacement confirmation, and output locks. Only main-process adapters may redeem a grant through `resolveReadGrant` or `resolveWriteGrant`; a future domain adapter must still pass the resolved internal path through the shared bounded Python file-ingress policy.

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
pnpm --dir desktop test:accessibility
pnpm --dir desktop test:visual
```

`desktop-check` runs lint, separated main/preload/renderer type checks, unit
tests, the presentation-boundary contract, and a source-map-free build
inspection. `desktop-e2e` builds the production renderer and launches it in
Electron with a deterministic fictional mock bridge; it is not an installer
or literal packaged-executable test. `test:accessibility` scans every shell
route in light, dark, and high-contrast modes with the exact locked `axe-core`
version in real Chromium. `test:visual` checks the minimum 720-by-560 window at
200% zoom for horizontal clipping. Neither command replaces the manual
screen-reader review in the [desktop shell guide](../docs/DESKTOP_SHELL.md).
`desktop-security` runs the high-severity dependency audit, source secret scan,
produces `desktop/sbom.cdx.json` (ignored by Git), builds an unpacked directory
package after verifying the native sidecar resource, and inspects the resulting
`app.asar`, eight packaged Electron fuses, and supported ASAR-integrity
metadata. Local and ordinary CI builds remain unsigned verification inputs.
Full production/trusted signing is deferred until v1.0.0: project-produced
`0.x` release installers and annotated release tags must be unsigned. The tag
release uses `electron-builder.release.yml` only after exact-head gates pass;
it builds the four manual-installer rows documented in the
[release runbook](../docs/RELEASING.md), then binds their declared signing mode,
checksums, SBOMs, evidence, and provenance before publication. No updater feed,
background update, staged rollout, or automatic rollback is configured.

For a fictional development-only component review, run:

```sh
pnpm --dir desktop dev:gallery
```

The production build verifier rejects gallery copy and fixtures, embedded
remote assets, and remote network endpoints.

## Architecture

The renderer has browser-only TypeScript types and imports. Its design-system
directory is presentation-only: capability gates select already-authorized
content but never create authority, and stable coded-error views render only
reviewed text and React nodes. The sandboxed preload exposes the frozen,
bounded, async `window.ancestry` API after request and response runtime
validation. Main validates IPC senders, serves a fixed `app://bundle`
asset/MIME manifest under a restrictive CSP, and globally denies permissions,
downloads, child windows, webviews, unexpected navigation, and packaged
developer tools. File-grant responses are strict, path-free DTOs bound to the
requesting renderer, exact purpose and access mode, one application session,
and one redemption. Closing or cross-document navigation of the renderer,
explicit revocation, or application restart invalidates them; trusted
same-document application routes retain the existing renderer identity. In
packaged builds, main privately starts and verifies the control-only native
sidecar. Startup failure crosses the bridge only as sanitized diagnostics;
retry is bounded by the main-owned supervisor, and authenticated session
details never enter IPC or the preload bridge. See
[the lifecycle and diagnostics guide](../docs/DESKTOP_SIDECAR.md). A later
domain transport adapter must consume the application-service contract and
shared file-ingress policy; do not place domain logic in Electron.

Unreleased Issue #363 also adds an intentionally unwired, Electron-Main-only
container-control foundation for later deployment work. It validates one
app-owned Unix Docker endpoint and exact hardened Compose plans, ignores ambient
Docker selection, and exposes only typed inspection and bounded lifecycle
operations inside main. The Docker socket, executable, context, generic process
authority, and lifecycle methods are absent from preload, the renderer, and
shared DTOs. This foundation does not provide an application image, listener,
secret broker, genealogy workload, profile activation, or user-facing
container runtime; those remain blocked on their separately reviewed issues and
G5/G7 assurance gates.

The opt-in native evidence test is destructive only inside its explicitly
isolated, app-owned test profile and project:

```sh
ANCESTRYLLM_NATIVE_CONTAINER_EVIDENCE=1 pnpm --dir desktop test:native-container
```

It is not an ordinary developer or CI gate. Run it only on a disposable
supported Docker/Colima profile after reviewing the selected identity. The
checked sanitized macOS arm64 result is recorded in
[`docs/release-evidence/issue-363-macos-arm64-container-supervisor.json`](../docs/release-evidence/issue-363-macos-arm64-container-supervisor.json);
it proves only the narrow #363 lifecycle and isolation subset, not container
runtime availability.

The allowlisted external-link helper is main-process-internal and testable: it accepts only exact `https://github.com` destinations without credentials or a custom port, displays the normalized destination, defaults to cancel, and opens only after explicit confirmation. It is deliberately not part of `window.ancestry`; a renderer-facing external-link workflow requires its own separately reviewed contract.

## Security evidence

| Control | Evidence | Gate |
|---|---|---|
| `TM-R01` | Secure window defaults and weakened-future-window regression cases in `src/main/security-policy.test.ts`; global web-content and session denials in `src/main/index.ts` and `src/main/session-policy.test.ts`; deterministic skip-link, route-heading, and command-dialog focus tests; reduced-motion and forced-color styles; route/theme Chromium accessibility scans; minimum-window 200% zoom assertions; actual fuse and ASAR inspection in `scripts/inspect-package-fuses.mjs`. | `make desktop-check`, `make desktop-e2e`, `pnpm --dir desktop test:accessibility`, `pnpm --dir desktop test:visual`, `make desktop-security` |
| `TM-R02`, `TM-L02` | Exact route/MIME/CSP and traversal-denial tests in `src/main/security-policy.test.ts`; production runtime fetch, WebSocket, service-worker, and child-window denials in `e2e/shell.spec.ts`; the design-system boundary rejects network primitives, raw HTML rendering, direct bridge use, and production fixture imports; the production verifier separately rejects remote assets and endpoints, the development gallery, and fictional copy. | `make desktop-check`, `make desktop-e2e` |
| `TM-I01` | The bridge retains its fixed reviewed methods. Main validates sender, request, response, renderer ownership, purpose, access, session, and revocation. The Issue #106 shell consumes versioned responses only through existing hooks; its routes and `CapabilityGate` neither add bridge methods nor grant authority. E2E asserts that no path, resolver, direct filesystem, security-reporting, external-link, or domain method leaks into the renderer bridge. | `make desktop-check`, `make desktop-e2e` |
| `TM-A02`, `TM-S01`, `TM-D01`, `TM-O01` | Issue #107 validates the fixed startup-diagnostic route and exact four-component schema, requires keyring-only packaged secret selection, blocks affected mutations and capability access while degraded, and exposes one bounded non-repairing retry. Contract and renderer tests reject secrets, host identity, paths, payloads, unknown fields, duplicate initialization, key replacement, plaintext fallback, and misleading recovery. | `make test`, `make desktop-check`, `make desktop-e2e`, `make desktop-security` |
| `TM-F01`, `TM-F02`, `TM-D01`, `TM-C01`, `TM-O01` | Native-dialog selection validates regular-file/link state, bounded size, purpose-specific format, canonical identity, and fingerprints before issuing a random one-use grant. Redemption revalidates identity, replacement confirmation is native and race-checked, aliases and concurrent output grants fail closed, and stable responses omit paths. Focused broker and dialog tests exercise cancellation, replacement races, revocation, alias rejection, and output locks. A dedicated verification-only packaged adapter exercises native open/save mediation, path-free DTOs, explicit replacement confirmation, and revocation across the hosted platform matrix without entering production builds. Full worker and publication evidence remains #114/#118/#131. | `make desktop-check`, exact-head packaged workflow |
| `TM-U01` | Static package-policy tests in `scripts/package-security.test.mjs`; packaged `app.asar`, fuse, and supported integrity inspection in `scripts/inspect-package-fuses.mjs`. Provenance, target execution, and installation remain `0.x` release gates; trusted signing and notarization become mandatory at v1.0.0. Updater behavior is excluded from 0.5.0. | `make desktop-security` |
