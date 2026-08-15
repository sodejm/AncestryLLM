# AncestryLLM desktop shell

This directory contains the UI-only Electron adapter and the packaged
control-sidecar supervisor. It intentionally implements no genealogy domain
behavior and does not access files, databases, provider credentials, the
network, or operating-system services from the renderer. The versioned
`window.ancestry` bridge retains the six 0.5 control methods for application
information, startup diagnostics, capabilities, bounded sidecar retry,
preference reads, and optimistic-concurrency preference updates. Source-level
gated Issue #103 adds three path-free file-grant methods, Issue #105 adds five fixed
settings and credential methods, Issue #108 adds six fixed provider-profile,
endpoint-test, and consent methods, Issue #109 adds five fixed task-lifecycle
request methods and one validated job-event listener, Issue #348 adds three fixed
local-runtime status/preview/apply methods, Issue #111 adds three fixed chat-stream
methods and one validated chat-event listener, and Issue #112 adds three fixed
chat-lifecycle methods plus two fixed native-action methods. Development uses
deterministic fictional fixtures; packaged main is the sole authenticated client
for the fixed sidecar routes. Packaged main stores the bounded local-preference
schema in `preferences.json` beneath Electron's OS app-data directory. The
renderer never receives that path and has no storage access.

Source-level gated Issue #110 adds the fixed synchronous transient-chat service, Issue
#111 adds the Main-owned bounded stream transport, and Issue #112 adds the
renderer **Chat** destination over those fixed contracts. The combined surface
adds no generic request channel, renderer network access, tool surface, file or
database authority, genealogy operation, or persistent conversation store.

The supported 0.6.0 product surface is a one-time local welcome on Home, a temporary Home-based welcome review, Diagnostics, a sanitized capability summary, and local visual Settings only. It has no genealogy, files, jobs, chat, providers, cloud accounts, or updater controls. See the [desktop shell guide](../docs/explanation/DESKTOP_SHELL.md) for first-run behavior, supported targets, manual installation, unsigned-artifact limits, and recovery guidance.

Source-level gated Issue #106 adds the reusable, responsive presentation shell for the
0.6 desktop work. Its `AppRoute`, `NavigationItem`, `CapabilityGate`,
`AsyncState`, `CodedErrorView`, and dialog-focus contracts provide persistent
navigation, a workspace header, context help, deterministic keyboard focus,
and plain-language loading, empty, offline, degraded, error, success, and
permission-denied states. These components do not call the bridge, network,
filesystem, sidecar, or Electron APIs and do not grant capabilities or decide
service policy. Development and review use only the versioned bridge plus
fictional fixtures.

Source-level gated Issue #107 makes that first-run surface explicitly local-only and
fail closed. **Local Desktop** is the recommended and only available choice;
Connect Remote and Host Remote remain visibly advanced but unavailable. A
schema-v1 startup report covers configuration, SQLCipher, keyring, and workspace
readiness using stable codes and sanitized remediation. Any blocking component
keeps settings, credentials, and capabilities read-only. The one manual retry
rechecks existing state without initializing a database, overwriting a key,
falling back to plaintext, or widening a listener.

Source-level gated Issue #108 adds separate **Local Providers**, **Cloud Providers**,
and **Consent & Privacy** sections without adding provider execution. The
renderer may request an explicit endpoint test, save a profile only against the
tested endpoint identity and current revision, preview the complete consent
scope, create that exact preview, and revoke consent. Secrets remain blank,
write-only fields managed through Issue #105's credential boundary; presence of
a stored key cannot select a provider or grant consent.

Source-level gated Issue #104 adds a main-process-only safe-shutdown preflight and a
Python-owned, UI-neutral job lifecycle. It adds no renderer bridge method,
event listener, supported job screen, or job-submission surface. During an
application quit, Electron main can present the native choices **Wait**,
**Request cancellation**, and **Stay open** without exposing the authenticated
sidecar session or job event stream to the renderer.

Source-level gated Issue #109 adds a **Tasks** destination over that lifecycle. Five
fixed requests list, inspect, cancel, subscribe, and unsubscribe, while one
validated event listener receives only the main-owned subscription's events.
The renderer rebuilds state from backend snapshots after reload, resynchronizes
on event gaps, and closes a subscription after one terminal state. It stores no
job state, admits no work, receives no sidecar session or artifact path, and
adds no provider or genealogy operation. Artifact cards display only safe
metadata; any future open or save action must use Issue #103's grant mediation.

Issues #110-#112 keep authenticated chat routes private to Electron Main. Every
short-lived session is bound to an exact stored profile and model; provider
policy and fresh consent are rechecked before each run, and fixed message,
context, output, retry, timeout, and session limits apply before provider access.
The renderer reaches only six fixed chat methods and one validated event
listener through preload; it receives no bearer token, HTTP client, endpoint, or
generic route authority. Messages remain bounded process memory, the fixed
system instruction exposes no tools, and audit data contains only reviewed
identifiers, counters, and content hashes.

Issue #103 is a source-level gated security foundation, not a supported 0.6 domain workflow. Its reusable selected-file card displays only a safe basename, byte size, kind, and replacement status. Electron main owns the native open/save dialogs, random opaque grant identifiers, path map, purpose and access checks, lifecycle revocation, input fingerprints, explicit replacement confirmation, and output locks. Only main-process adapters may redeem a grant through `resolveReadGrant` or `resolveWriteGrant`; a future domain adapter must still pass the resolved internal path through the shared bounded Python file-ingress policy.

The persisted schema contains only color scheme, reduced-motion choice, onboarding completion, schema version, and optimistic revision. `onboardingCompleted` is internal workflow state, not a Settings control. Continue persists that flag through the existing bridge, and a new application process skips the welcome only after a fresh valid snapshot reports completion. Conflicts, unavailable or malformed responses, and corrupt or unsupported storage fail closed and do not silently unlock or overwrite the file. Writes are validated, serialized, and atomically replace the file. Missing or supported legacy data receives safe defaults. Provider configuration, accounts, file grants, genealogy data, prompts, payloads, and secrets are never preference fields.

## Reproducible setup and gates

Use Node `26.5.0` (see `.node-version`), Corepack, and the repository-pinned pnpm `11.9.0`:

```sh
corepack enable
corepack prepare pnpm@11.9.0 --activate
make desktop-install
make desktop-check
make desktop-e2e
make desktop-security
pnpm --dir desktop test:accessibility
pnpm --dir desktop test:visual
```

`desktop-install` performs the frozen-lockfile install, explicitly rebuilds the
locked Electron package, and fails with a stable error if its platform runtime
is still absent. The explicit rebuild makes setup fail closed even when a
shared pnpm store contains stale build-script state. Electron 39.8.10 remains
exactly pinned; its installer resolves `extract-zip` to the Electron-maintained
`@electron-internal/extract-zip` 1.0.5 package through the locked override and
reviewed compatibility patch.

`desktop-check` runs lint, separated main/preload/renderer type checks, unit
tests, the presentation-boundary contract, and a source-map-free build
inspection. `desktop-e2e` builds the production renderer and launches it in
Electron with a deterministic fictional mock bridge; it is not an installer
or literal packaged-executable test. `test:accessibility` scans every shell
route in light, dark, and high-contrast modes with the exact locked `axe-core`
version in real Chromium. `test:visual` checks the minimum 720-by-560 window at
200% zoom for horizontal clipping. Neither command replaces the manual
screen-reader review in the [desktop shell guide](../docs/explanation/DESKTOP_SHELL.md).
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
packaged builds, main privately starts and verifies the fixed-route native
sidecar. Startup failure crosses the bridge only as sanitized diagnostics;
retry is bounded by the main-owned supervisor, and authenticated session
details never enter IPC or the preload bridge. The source-level gated Issue #104
shutdown preflight is likewise main-process-only: it asks the sidecar whether
active jobs are safe to drain, wait for, or cancel before the IPC boundary is
disposed. See
[the lifecycle and diagnostics guide](../docs/reference/DESKTOP_SIDECAR.md). A later
domain transport adapter must consume the application-service contract and
shared file-ingress policy; do not place domain logic in Electron.

Issue #109 keeps authenticated SSE in main and binds at most 32 opaque job
subscriptions to each authorized renderer. Cross-document navigation,
renderer exit, sidecar replacement, terminal delivery, and application
shutdown clean them up. Preload exposes one validated event listener rather
than generic listen or channel authority.

Issues #110-#112 cross this boundary only through the six fixed chat methods and
one validated event listener. Electron Main owns the authenticated stream,
renderer ownership, cancellation, acknowledgement, bounded reconnect, and
teardown. The renderer owns bounded transient presentation state and safe model
text rendering; it receives no provider, network, sidecar-session, tool, file,
database, or genealogy authority.

Source-level gated Issue #363 adds the Electron-Main-only container-control foundation,
and Issue #348 wires only its macOS arm64 runtime-acquisition and lifecycle
surface through three fixed methods. Settings can inspect a sanitized status,
review an exact policy-bound plan, and explicitly install, start, stop, repair,
or remove the app-owned Colima/Lima and Docker tool substrate. The Docker
socket, executable path, context, environment, arbitrary arguments, and generic
process authority remain absent from preload and the renderer. This surface
does not provide an application image, listener, secret broker, genealogy
workload, or profile activation; those remain blocked on their separately
reviewed issues and G5/G7 assurance gates.

The packaged `resources/macos-arm64-runtime-policy-v1.json` binds every tool to
one repository, version, asset, source URL, byte length, SHA-256, license
identity, and license digest. The manager accepts only Apple silicon on macOS
13 or later with hardware virtualization and 24 GiB free, never requests
administrator privileges, and ignores ambient Docker selection. Docker Desktop
is optional and is neither installed nor modified. See the
[desktop shell guide](../docs/explanation/DESKTOP_SHELL.md#macos-arm64-local-runtime-management)
for review/apply commands, interruption recovery, offline behavior, and the
separate preserve-data and delete-data removal choices.

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

The fixed external-link action accepts only normalized HTTPS destinations without credentials, control characters, or a custom port. The renderer displays the exact destination, Electron Main prompts with that same destination, defaults to cancel, and opens it only after explicit confirmation. The fixed copy action writes plain text only; neither action exposes generic shell or clipboard authority.

## Security evidence

| Control | Evidence | Gate |
|---|---|---|
| `TM-R01` | Secure window defaults and weakened-future-window regression cases in `src/main/security-policy.test.ts`; global web-content and session denials in `src/main/index.ts` and `src/main/session-policy.test.ts`; deterministic skip-link, route-heading, and command-dialog focus tests; reduced-motion and forced-color styles; route/theme Chromium accessibility scans; minimum-window 200% zoom assertions; actual fuse and ASAR inspection in `scripts/inspect-package-fuses.mjs`. | `make desktop-check`, `make desktop-e2e`, `pnpm --dir desktop test:accessibility`, `pnpm --dir desktop test:visual`, `make desktop-security` |
| `TM-R02`, `TM-L02` | Exact route/MIME/CSP and traversal-denial tests in `src/main/security-policy.test.ts`; production runtime fetch, WebSocket, service-worker, and child-window denials in `e2e/shell.spec.ts`; the design-system boundary rejects network primitives, raw HTML rendering, direct bridge use, and production fixture imports. Issue #112 renders model text through a closed CommonMark/GFM component allowlist with raw HTML, images, embeds, implicit autolinks, and executable actions disabled; external destinations remain visible and copy emits plain text only. | `make desktop-check`, `make desktop-e2e` |
| `TM-I01` | The bridge retains fixed reviewed methods. Main validates sender, request, response, renderer ownership, purpose, access, session, and revocation. The #112 Chat destination consumes only six fixed chat methods, one validated event listener, and two fixed native actions; it adds no generic route, direct network, path, resolver, filesystem, sidecar-session, provider, or domain authority. | `make desktop-check`, `make desktop-e2e` |
| `TM-A02`, `TM-S01`, `TM-D01`, `TM-O01` | Issue #107 validates the fixed startup-diagnostic route and exact four-component schema, requires keyring-only packaged secret selection, blocks affected mutations and capability access while degraded, and exposes one bounded non-repairing retry. Contract and renderer tests reject secrets, host identity, paths, payloads, unknown fields, duplicate initialization, key replacement, plaintext fallback, and misleading recovery. | `make test`, `make desktop-check`, `make desktop-e2e`, `make desktop-security` |
| `TM-F01`, `TM-F02`, `TM-D01`, `TM-C01`, `TM-O01` | Native-dialog selection validates regular-file/link state, bounded size, purpose-specific format, canonical identity, and fingerprints before issuing a random one-use grant. Redemption revalidates identity, replacement confirmation is native and race-checked, aliases and concurrent output grants fail closed, and stable responses omit paths. Focused broker and dialog tests exercise cancellation, replacement races, revocation, alias rejection, and output locks. A dedicated verification-only packaged adapter exercises native open/save mediation, path-free DTOs, explicit replacement confirmation, and revocation across the hosted platform matrix without entering production builds. Full worker and publication evidence remains #114/#118/#131. | `make desktop-check`, exact-head packaged workflow |
| `TM-E01`, `TM-D01`, `TM-C01`, `TM-O01` | Issue #104 persists bounded schema-v1 job snapshots and events in SQLCipher, reconciles interrupted non-terminal jobs to one terminal outcome on startup, isolates slow subscribers with coded replay resynchronization, and keeps cancellation cooperative at declared safe points. Electron main obtains a sanitized shutdown assessment and offers native **Wait**, **Request cancellation**, and **Stay open** choices; the renderer receives no job or sidecar authority. | `make test`, `make desktop-check`, `pnpm --dir desktop test` |
| `TM-I01`, `TM-D01`, `TM-O01`, `TM-F01`, `TM-F02` | Issue #109 validates five fixed task requests and one fixed event listener. Main owns authenticated bounded SSE and at most 32 sender-owned subscriptions. The renderer ignores stale or duplicate events, refreshes gaps, reloads backend snapshots, closes terminal streams, announces meaningful state changes once, renders coded redacted failures, and displays path-free artifact metadata. It adds no job admission or direct artifact action. | `make desktop-check`, `make desktop-e2e`, `make desktop-security` |
| `TM-L01`, `TM-L02`, `TM-S01`, `TM-D01`, `TM-O01` | Issues #110-#112 keep fixed chat routes behind Electron Main. Python tests prove exact named-profile/model binding, fresh consent, pre-provider bounds, network-free denial paths, no-tool requests, transient session teardown, and payload-free audit records. Renderer tests prove bounded ordered/replay-safe state, explicit profile/model/consent selection, interruption handling, safe Markdown, plain-text copy, and confirmed HTTPS links. | `make test`, `make typecheck`, `make desktop-check`, `make desktop-e2e` |
| `TM-U01` | Static package-policy tests in `scripts/package-security.test.mjs`; packaged `app.asar`, fuse, and supported integrity inspection in `scripts/inspect-package-fuses.mjs`. Provenance, target execution, and installation remain `0.x` release gates; trusted signing and notarization become mandatory at v1.0.0. Updater behavior is excluded from 0.6.0. | `make desktop-security` |
