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

## Installation and updates

The supported 0.5.0 targets are macOS 15 and 26 on arm64 and x64, Windows 11
on x64, and Ubuntu 24.04 on x64. A supported release is a manually installed,
signed installer that has passed the target-specific release and packaged
assurance gates in the [release runbook](RELEASING.md).

Unsigned CI artifacts and unpacked development builds are verification inputs,
not supported releases or evidence of successful signing, notarization, or
installation. Version 0.5.0 has no updater, update feed, background update
channel, or staged rollout. Updating and rolling back mean manually installing
the appropriate complete, signed installer.

## Sanitized diagnostics and recovery

When the bundled runtime is unavailable, keep recovery bounded and generic:

1. Open **Diagnostics** and request one bounded retry.
2. If the failure remains, close and reopen the application.
3. Reinstall the same supported, target-matched signed build when local files
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
fuses. It does not launch a signed installer. Signing, manual installation, and
actual Windows 11 execution remain separate release gates.
