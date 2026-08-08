# Packaged desktop sidecar

Issue #225 adds the control-only native sidecar used by the packaged Electron
main process. Issue #226 adds the narrow typed bridge that lets the renderer
read sanitized control state without becoming a sidecar client. Neither change
exposes genealogy, job, chat, provider, cloud-account, updater, or generic
command routes; the sidecar is not a domain-data transport.
The [desktop shell guide](DESKTOP_SHELL.md) defines the supported 0.5.0 user
surface, installation model, and sanitized recovery contract.

## Native targets and release evidence

The native build workflow creates a self-contained PyInstaller directory for
each target and embeds it under `Resources/sidecar/<target>/` (macOS) or the
equivalent Electron resources directory on Windows and Linux.

| Supported operating system | Architecture | Resource target | Native CI runner |
| --- | --- | --- | --- |
| macOS 15 | arm64, x64 | `darwin-arm64`, `darwin-x64` | `macos-15`, `macos-15-intel` |
| macOS 26 | arm64, x64 | `darwin-arm64`, `darwin-x64` | `macos-26`, `macos-26-intel` |
| Windows 11 | x64 | `win32-x64` | built and executed under x64 emulation on `windows-11-arm` |
| Ubuntu 24.04 | x64 | `linux-x64` | `ubuntu-24.04` |

The workflow smoke-tests the native executable before packaging and verifies
the exact packaged resource afterwards. A system Python installation is not
used at runtime. CI output is an unsigned, unpacked verification artifact, not
a supported release. Supported distribution requires a manually installed
installer plus provenance, installation, target-execution, and packaged
assurance gates. Full production/trusted binary signing and applicable
notarization begin at v1.0.0; official `0.x` installers default to unsigned and
local/manual `0.x` installers may be self-signed. Version 0.5.0 has no updater,
update feed, background update channel, or staged rollout.

## Private lifecycle

1. Electron main resolves only the current native resource target and creates a
   fresh 32-byte (256-bit) bearer with the operating-system random source.
2. It starts the executable with no arguments, no shell, a private temporary
   working directory, and an allowlisted environment. Provider credentials,
   `PATH`, and home-directory values are not inherited.
3. Electron writes one bounded JSON line to stdin containing the exact API
   contract, application build, and bearer. The bearer is never placed in
   command-line arguments, environment variables, renderer state, readiness
   output, or diagnostics.
4. The sidecar forces `provider=none`, binds IPv4 `127.0.0.1` on port `0`, and
   exposes only authenticated `/api/v1/health` and `/api/v1/capabilities`.
5. It emits one bounded readiness line containing only the contract, sidecar
   build, and assigned port. Electron validates all three fields and verifies a
   token-derived HMAC health proof before marking the private session ready.

Contract or build mismatch fails closed without restart. Startup and probe
work are bounded to 10 seconds. Other launch failures and unexpected crashes
receive at most two restart attempts for the application lifetime. Application
quit sends one termination request, waits up to three seconds, then force-kills
and performs one final bounded wait. No sidecar is started by the development
mock shell.

The supervisor exposes a main-process-only control interface. A fixed-route
internal client may acquire the authenticated session only while its lifecycle
is `ready`; the session is cleared before restart, failure, or shutdown. The
interface also exposes sanitized lifecycle diagnostics and one application-
lifetime manual retry. Concurrent retry requests share a single launch attempt,
and an exhausted retry is a deterministic no-op. Electron main uses the session
only for authenticated `/api/v1/capabilities` requests. The bridge exposes the
result, sanitized diagnostics, and retry outcome, but never the session, bearer,
port, or raw HTTP data.

The frozen `window.ancestry` surface contains exactly `getAppInfo`,
`getStartupDiagnostics`, `getCapabilities`, `retrySidecar`, `getPreferences`,
and `updatePreferences`. Main validates the sender, argument count, preference
update schema, and runtime response schema before a value crosses IPC; preload
validates again before exposing the result to the renderer. Preference updates
require the last renderer-visible non-negative revision and return a coded
conflict when it is stale. Packaged main persists the exact bounded preference
schema in `preferences.json` beneath Electron's OS app-data directory. Writes
are validated, serialized, and atomically replace the file without following a
preference-file symlink. Missing and supported legacy data use safe defaults;
corrupt and unsupported data produce stable path-free diagnostics and are not
silently overwritten. The renderer receives neither the storage path nor any
additional storage capability.

## Diagnostics and recovery

User-facing failures are deliberately generic. Stderr is drained but not
forwarded into Electron logs; structural sidecar diagnostics contain no bearer,
port, URL, request, response, genealogy, provider, filesystem payload, raw
exception, or stack. Diagnostics contain only lifecycle state, a generic
failure class, and remaining automatic/manual retry counts. Do not add the raw
launch frame, environment, executable path, temporary directory, or stderr to
support reports.

For a startup or compatibility failure:

1. observe the degraded `unavailable` state; the window remains open for safe
   diagnostics even though no private session is granted;
2. use the bounded main-process retry at most once, or quit the application so
   any supervised process is terminated;
3. reinstall the same complete application build to restore a matched
   Electron/sidecar pair;
4. run the native smoke and packaged-resource checks for that target;
5. if the problem persists, record only the application version, target, gate
   name, and generic failure class.

Local native verification uses:

```console
uv run python scripts/build_sidecar.py --expected-target darwin-arm64
uv run python scripts/smoke_sidecar.py \
  desktop/build/sidecar/darwin-arm64/ancestryllm-sidecar/ancestryllm-sidecar
pnpm --dir desktop build
pnpm --dir desktop exec electron-builder --config electron-builder.yml --dir --mac --arm64
node desktop/scripts/verify-sidecar.mjs darwin-arm64 desktop/release
```

Choose the exact native target; cross-built sidecars are rejected. A desktop
support or `0.5.0` release claim also requires the release tracker, declared
binary-signing mode, platform execution, installation, and packaged assurance
gates to pass. Unpacked CI artifacts do not satisfy those gates.
