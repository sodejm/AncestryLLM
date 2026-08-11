# Desktop shell

AncestryLLM 0.5.0 is a bounded, offline Electron control shell. It does not
move the genealogy-capable CLI or console into the desktop application and it
does not introduce a second command or domain layer.

## Supported surface

The supported desktop destinations are deliberately small:

- **Home** identifies the application, its offline posture, and sanitized
  startup and capability state. The capability summary reports only whether
  the bundled local runtime is ready; it does not expose
  accounts, providers, credentials, genealogy data, or cloud consent.
- **Diagnostics** shows stable, sanitized lifecycle state and offers bounded
  retry or restart recovery.
- **Settings** stores local visual preferences only: color scheme and reduced
  motion. The internal onboarding flag is not a user-facing setting.

These destinations must remain usable with keyboard navigation and assistive
technology, and in loading, empty, degraded, failure, narrow-window, and
zoomed layouts.

## First run and revisit

The first supported launch opens a bounded welcome on **Home**. It explains
that AncestryLLM runs locally, that this release is the offline control shell,
that updates are installed manually, and that **Diagnostics** contains the
sanitized runtime status. It asks for no account, provider, API key, genealogy
data, or cloud consent.

**Continue** records only the main-process-owned `onboardingCompleted`
preference. A new application process skips the welcome after a valid refreshed
preference snapshot reports completion; the flag is not exposed in **Settings**.
Conflict, unavailable, corrupt, unsupported, or invalid preference responses
fail closed and keep the welcome gated with a stable sanitized code and bounded
**Try again**, **Diagnostics**, or restart recovery. The renderer never repairs
or overwrites invalid preference storage.

Completed users can select **Review welcome** on **Home**. Review is temporary
renderer state: **Back to Home** neither creates a route nor changes a
preference. Keyboard focus begins on the welcome heading, reduced-motion
preferences are respected, and degraded runtime state does not block navigation
to **Diagnostics** or its bounded retry.

The 0.5.0 shell has no genealogy, file or folder, GEDCOM or RootsMagic, job,
chat, provider or credential, cloud or account, domain-dispatch, updater, or
background-channel surface. Those exclusions apply to navigation and hidden
controls as well as visible content.

## Offline and process boundary

Electron starts the packaged sidecar on a private, ephemeral loopback endpoint
with provider `none`; the Electron main process is the sole authenticated
client. The preload bridge exposes exactly these six methods:

- `getAppInfo`
- `getStartupDiagnostics`
- `getCapabilities`
- `retrySidecar`
- `getPreferences`
- `updatePreferences`

The renderer receives no Node.js, Electron, network, filesystem, keyring,
provider, database, shell, or arbitrary-path access. It must never receive the
sidecar port, bearer token, endpoint, executable path, preference-file path,
stderr, raw sidecar or bridge errors, or stack traces. See the
[packaged sidecar contract](DESKTOP_SIDECAR.md) and
[desktop ADR](ADR-0025-electron-fastapi-desktop.md) for the underlying process
and architecture controls.

## Unreleased opaque file-mediation foundation

Issue #103 adds a security boundary for later genealogy workflows without
expanding the supported 0.5.0 domain surface. The bridge gains only three
strict, asynchronous methods:

- `requestOpenFileGrant`
- `requestSaveFileGrant`
- `revokeFileGrant`

The renderer requests an exact purpose and receives either a cancelled result
or a path-free grant containing a random opaque identifier and safe display
metadata: basename, kind, byte size, and replacement status. It cannot provide,
receive, reconstruct, persist, or redeem a pathname, and it has no direct
filesystem method. The reusable selected-file card does not initiate a domain
operation.

Electron main owns the native open/save dialogs and the grant-to-path map. It
checks the selected object's regular-file and link state, purpose-specific
extension and content signature, bounded size, canonical identity, and
filesystem fingerprint. Grants are bound to the requesting renderer, exact
purpose and access mode, current application session, and one redemption.
Explicit revocation, renderer close or cross-document navigation, and
application restart invalidate them. Trusted same-document routes such as the
application's hash-based Home, Diagnostics, and Settings transitions preserve
the renderer identity and its grants; each bridge request still rechecks the
exact main frame and trusted application URL. Existing-output replacement
requires a native confirmation and identity revalidation; source/output aliases
and concurrent output grants fail closed under main-owned locks.

Only a trusted main-process adapter may redeem a grant through
`resolveReadGrant` or `resolveWriteGrant`. A future genealogy integration must
then pass the internal path to the shared Python file-ingress adapter, which
reopens and revalidates the source under its own bounded policy before parsing
or publication. Until that adapter ships, the grant broker provides no GEDCOM,
RootsMagic, import, export, or report workflow.

## Unreleased settings and credential-management foundation

Issue #105 adds the source-level settings and credential-management boundary
planned for 0.6.0. It does not expand the released 0.5.0 shell and does not
enable provider execution, cloud consent, genealogy operations, or arbitrary
sidecar access.

The renderer can read the complete versioned settings catalog and submit one
exact optimistic-revision patch. The catalog exposes only five reviewed,
non-secret settings: the default provider choice and four bounded query/output
limits. Each entry supplies its label, help text, type, safe default, allowed
values or numeric bounds, restart requirement, sensitivity marker, and current
value. The Python `SettingsService` validates the whole update before
`AppConfig` atomically replaces the repository-owned configuration; unknown,
sensitive, invalid, or stale-revision changes fail closed.

Credential controls are intentionally write-only. The bridge adds only these
five fixed operations:

- `getSettings`
- `updateSettings`
- `getSecretStatus`
- `setSecret`
- `deleteSecret`

Secret references are selected from a fixed Python-owned allowlist. Reads
return only `present`, `missing`, or `unavailable`; no response can return a
credential value. Save and delete require separate explicit actions, and a
successful delete is reported only after the OS-keyring-backed store proves
the credential is absent. Credentials supplied through the process
environment remain usable by headless workflows but are read-only through this
interface. An unavailable or locked keyring, an environment-managed
credential, or any attempted plaintext fallback produces a stable redacted
failure.

The renderer's password field is uncontrolled and is cleared before the
asynchronous request begins and again after every success or failure. Secret
values are never retained in React state, query caches, bridge fixtures,
responses, logs, local storage, IndexedDB, Electron `safeStorage`, or plaintext
configuration. The renderer still has no direct keyring or network access.
Together with the three unreleased file-grant methods, the current development
bridge therefore contains fourteen fixed methods: the six released control
methods, three opaque file-grant methods, and five settings/credential methods.
There is still no generic send, listen, route-selection, or command operation.

The unreleased source implements the non-secret, versioned deployment-profile
control plane accepted by the
[deployment-profile ADR](ADR-0026-local-first-container-remote-deployment.md).
Local Desktop is preselected and recommended. The shared Python service owns
profile validation, exact preview and confirmation, atomic persistence,
diagnostics, redacted evidence, and recovery to Local Desktop. Issues #107 and
#108 own the later first-run and settings presentation. Connect Remote and Host
Remote remain visible advanced intents, but neither can be activated until its
enrollment or host-runtime dependency is implemented and independently gated.

Selecting or inspecting a profile does not open a listener, start a container,
discover a service, move genealogy data, select a provider, or grant cloud
consent. The released 0.5 shell still has no supported container, remote, LAN,
browser, or public-service surface. Future presentation keeps the renderer
sandbox and fixed typed bridge, while authority remains in the shared service
contracts and Electron Main's narrow adapter.

## Installation and updates

The supported 0.5.0 targets are macOS 15 and 26 on arm64 and x64, Windows 11
on arm64, and Ubuntu 24.04 on x64. A supported release is a manually installed
installer that has passed the target-specific release and packaged assurance
gates in the [release runbook](RELEASING.md). Full production/trusted binary
signing is explicitly deferred until the first full version release, v1.0.0.
Project-produced `0.x` release installers and annotated release tags must be
unsigned.

Unpacked CI artifacts and development builds are verification inputs, not
supported releases or evidence of installation. For an install or upgrade,
quit AncestryLLM; download the
target-matched full installer and `SHA256SUMS` from the same immutable release;
verify its digest and declared `binarySigningMode`; install it over the current
application; relaunch; and confirm the version and healthy Diagnostics. A
`0.x` binary may produce an unknown-publisher or equivalent operating-system
prompt. At v1.0.0 and later, also verify the trusted platform signature;
Ubuntu then requires the adjacent `.deb.asc` detached GPG signature.
Application files are replaced while OS-managed AncestryLLM data and
configuration directories are retained.

Version 0.5.0 has no updater feed, no background update, no staged rollout, and
no automatic rollback. It publishes no `latest*.yml` or blockmap. Updating and
rolling back mean manually installing an appropriate complete installer whose
checksum and version-required platform signature still verify.

## Sanitized diagnostics and recovery

When the bundled runtime is unavailable, keep recovery bounded and generic:

1. Open **Diagnostics** and request one bounded retry.
2. If the failure remains, close and reopen the application.
3. Reinstall the same supported, target-matched build when local files
   may be incomplete.
4. When reporting a problem, include only the application version,
   operating-system target, and stable diagnostic code shown by the shell. Do
   not include local paths, environment values, process details, genealogy
   data, or raw error output.

Generic recovery text is part of the security boundary: the capability summary
and diagnostics must not turn private runtime state into renderer-visible
details.

## Verification boundary

`make desktop-e2e` builds the production renderer and launches it in Electron
with a deterministic fictional mock bridge. The flow proves welcome completion,
renderer reload, revisit, degraded startup, retry, and destination access. A
separate `FilePreferencesStore` unit test proves that completion survives a
fresh store instance, which models a new application process.

The exact-head [desktop verification gate](DESKTOP_VERIFICATION.md) separately
assembles and launches the literal unpublished unpacked executable on six
hosted runner rows, exercises healthy first run, durable settings, corrupt
preferences, accessibility and hardening controls, and inspects the packaged
fuses. It does not launch an installer. Manual installation and actual Windows
11 execution remain separate release gates; trusted signing becomes a release
gate at v1.0.0.

Issue #105's source suites additionally verify revision conflicts, strict
settings metadata, write-only secret schemas, keyring failure behavior,
credential deletion, bridge redaction, and password-field lifetime. Packaged
settings and credential-management evidence remains owned by the 0.6 desktop
verification work; source tests alone do not make the feature released.
