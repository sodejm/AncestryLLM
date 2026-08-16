# Packaged desktop sidecar

Issue #225 adds the control-only native sidecar used by the packaged Electron
main process. Issue #102 hardens its payload verification and process-tree
supervision. Issue #226 adds the narrow typed bridge that lets the renderer
read sanitized control state without becoming a sidecar client. Issue #105 adds
a source-level gated 0.6.0 boundary for atomic non-secret settings and write-only
credential management. Issue #107 adds a sanitized schema-v1 startup report,
keyring-only packaged secret resolution, and fail-closed mutation gating for
local first run. Issue #104 adds a source-level gated UI-neutral job-lifecycle and
safe-shutdown boundary. Issue #109 adds its bounded Tasks presentation through
five fixed request methods and one validated event listener. Issue #110 adds a
fixed, synchronous, transient chat service behind authenticated internal routes.
That service requires an exact named profile and model plus current policy and
consent. Issue #111 adds fixed stream-start, SSE, and cancellation routes plus a
source-only Electron Main/preload bridge. Main owns authenticated SSE, strict
owner and sequence validation, bounded batching, acknowledgement backpressure,
and cancellation; it exposes no bearer or HTTP authority. Issue #112 adds the
bounded renderer Chat presentation, three fixed chat-lifecycle methods, and two
fixed native actions. These chat boundaries add no tools, file or database
access, genealogy operation, job submission, cloud-account, updater, autonomous
action, persistent transcript, or generic command route. The sidecar is not a
general domain-data transport.
The [desktop shell guide](../explanation/DESKTOP_SHELL.md) defines the supported 0.6.0 user
surface, installation model, and sanitized recovery contract.

## Native targets and release evidence

The native build workflow creates a self-contained PyInstaller directory for
each target and embeds it under `Resources/sidecar/<target>/` (macOS) or the
equivalent Electron resources directory on Windows and Linux.

| Supported operating system | Architecture | Resource target | Native CI runner |
| --- | --- | --- | --- |
| macOS 15 | arm64, x64 | `darwin-arm64`, `darwin-x64` | `macos-15`, `macos-15-intel` |
| macOS 26 | arm64, x64 | `darwin-arm64`, `darwin-x64` | `macos-26`, `macos-26-intel` |
| Windows 11 | arm64 | `win32-arm64` | built and executed natively on `windows-11-arm` |
| Ubuntu 24.04 | x64 | `linux-x64` | `ubuntu-24.04` |

The native build writes a deterministic `sidecar-manifest.json` containing the
exact target, application/sidecar build, and sorted full payload inventory. It
binds regular files by size and SHA-256 and records only safe in-tree symbolic
links. The Electron production build embeds the SHA-256 of that adjacent
manifest in main-process code. Before generating a bearer or starting a child,
main verifies the embedded digest, exact target/build, complete inventory,
every file, and every link. An unexpected, missing, substituted, or escaping
entry fails closed with only a structural startup diagnostic.

This manifest binding detects sidecar-payload substitution; it is not a
publisher signature, notarization, or whole-application integrity mechanism.
Project-produced `0.x` release installers and annotated tags remain unsigned by
policy. Manifest binding does not make the payload publisher-signed and cannot
authenticate a wholly rewritten application bundle. Trusted publisher signing
and applicable notarization remain the Issue #132 distribution gate and become
mandatory at v1.0.0. The
verify-to-spawn filesystem interval also remains a residual TOCTOU boundary;
trusted signing does not remove the need to narrow or eliminate that interval.

The workflow smoke-tests the native executable before packaging and verifies
the exact packaged resource afterwards. A system Python installation is not
used at runtime. CI output is an unsigned, unpacked verification artifact, not
a supported release. Supported distribution requires a manually installed
installer plus provenance, installation, target-execution, and packaged
assurance gates. Version 0.6.0 has no updater, update feed, background update
channel, or staged rollout.

On Ubuntu, the packaged-runtime and installer checks exercise the production
keyring integration against the distribution-provided GNOME Secret Service.
The checked-in launcher starts that service on a disposable D-Bus session with
owner-only temporary storage, waits for its well-known name, and proves native
store, read, and delete operations with a non-secret probe. Exact-head packaged
verification reaches that service through a private socket and a separate
unpublished Linux verifier package. Only the verifier package
compiles the adapter that reads an exact command-line switch carrying the
owner-only temporary root. The production package is assembled and scanned
first, its adapter never reads the selector, and its build rejects the selector
literal. In the verifier, Electron Main rejects the root outside Linux or when
it is not an absolute Linux path, then derives the sidecar's home, XDG paths,
and exact `runtime/bus` address from the root rather than inheriting ambient
values. The launcher binds its private D-Bus daemon to that same owner-only
socket. Every Linux sidecar launch pins the native Secret Service backend,
ignores ambient Python keyring selectors and configuration, and excludes
provider credential and `PATH` values. Normal launches also exclude home,
cache, configuration, and data values; they ignore ambient D-Bus and XDG
runtime selectors and bind to `unix:path=/run/user/<uid>/bus` using the
kernel-reported process user ID. Release-installer verification runs the
installed production package against that same production-derived endpoint.
The launcher requires an owner-only runtime directory owned by the current
user. If the endpoint is absent, it creates a private D-Bus daemon, records the
socket identity, and removes only that owned endpoint. If the canonical
endpoint already exists, the launcher requires a current-user-and-group,
non-symlink Unix socket, proves that it is a live session bus with no existing
Secret Service owner, and rechecks its exact metadata before reuse. It never
kills or removes a reused bus. In either path, the verifier starts its own GNOME
Secret Service with only disposable home, configuration, data, and control
storage. The launcher preserves the packaged command's real exit status and
removes the disposable state afterward. No mock or alternate Python
keyring backend is permitted. This is verification infrastructure only; it
does not add a production credential fallback or weaken keyring-only startup.

## Private lifecycle

1. Electron main resolves only the current native resource target and completes
   the manifest verification described above.
2. Only after verification, it creates a fresh 32-byte (256-bit) bearer with
   the operating-system random source.
3. It starts the executable with no arguments, no shell, a private temporary
   working directory, and an allowlisted environment. Provider credentials,
   `PATH`, and home-directory values are not inherited during normal launches.
   Linux ignores ambient D-Bus and XDG runtime selectors and derives the native
   Secret Service bus address from the kernel-reported user ID. The exact
   internal verification adapter instead permits only paths and the bus address
   derived from the disposable verifier's validated absolute root; that adapter
   is absent from ordinary production packages and does not accept ambient home,
   XDG, or D-Bus values.
4. Electron writes one bounded JSON line to stdin containing the exact API
   contract, application build, and bearer. The bearer is never placed in
   command-line arguments, environment variables, renderer state, readiness
   output, or diagnostics.
5. The sidecar starts from the network-free `provider=none` default, binds IPv4
   `127.0.0.1` on port `0`, and exposes authenticated fixed routes only. Ambient
   provider credentials remain excluded from the packaged process. The supported
   0.6.0 composition has
   `/api/v1/health` and `/api/v1/capabilities`; the source-level gated #105 code adds
   `/api/v1/settings` plus fixed status, set, and delete operations beneath
   `/api/v1/secrets/{reference}`. Issue #107 adds the read-only
   `/api/v1/startup-diagnostics` route. Issue #104 adds fixed job list, status,
   cancel, SSE-event, and safe-shutdown routes. Those routes expose lifecycle
   metadata only and admit no work. Issue #110 adds fixed chat capability,
   session-create, session-read, session-delete, and synchronous run routes. A
   run can leave the network-free default only after exact profile/model policy,
   current consent, bounded-input, and provider preflight checks succeed. It
   still has no generic route dispatcher. Issue #111 adds only fixed
   stream-start, session/run-owned SSE-event, and stream-cancel operations for
   the same bounded use case. Packaged composition also adds one hidden,
   bodyless `/api/v1/runtime/shutdown` route that only Electron Main can call
   with the active session credentials; it admits no application work and is
   omitted from generated OpenAPI.
6. It emits one bounded readiness line containing only the contract, sidecar
   build, and assigned port. Electron validates all three fields and verifies a
   token-derived HMAC health proof before marking the private session ready.

Contract or build mismatch fails closed without restart. Startup and probe
work are bounded to 10 seconds. Other launch failures and unexpected crashes
receive at most two restart attempts for the application lifetime. Application
quit first invalidates the public active session, then gives the captured
Main-only session up to three seconds to request authenticated, bodyless
runtime shutdown. Uvicorn begins its configured graceful drain, and Electron
allows up to three more seconds to observe the sidecar leader exit. A `204`
response alone is never termination proof. If the request fails or the leader
remains live, Electron uses the existing bounded forced process-tree path.
POSIX launches use a detached process group and signal the full group even if
its leader already exited. On Windows, the sidecar assigns itself to a
non-inheritable, kill-on-close Job Object before reading bootstrap input, while
Electron uses `taskkill.exe /T /F` for a live tree. Closing the sidecar owner
therefore terminates descendants even when Electron cannot observe the original
leader. Electron gives the no-shell `taskkill.exe` child process four seconds
and independently rejects a nonsettling executor after the same bounded
interval. It then allows up to five seconds for Node to observe the leader exit,
including when `taskkill.exe` times out, fails, or reports that the leader exited
first. The command result alone is never treated as proof: failure to observe
the leader exit still fails closed. Only after process-tree termination is
verified does Electron remove the private working directory. Windows removal
retries transient filesystem errors at most five times with a 100-millisecond
linear delay; a persistent cleanup failure still vetoes application exit. No
sidecar is started by the development mock shell.

Electron bounds the complete supervisor stop to 20 seconds, including the
graceful request and exit observation, any
launch already in flight and all verified process-tree termination. Exceeding
that deadline fails closed and leaves the supervisor unavailable instead of
allowing application shutdown to wait indefinitely.

The sidecar creates its SQLCipher-backed job repository only when startup
diagnostics permit database access. It reconciles any persisted nonterminal
snapshot to exactly one failed `JOB_INTERRUPTED` terminal state at startup; it
never automatically replays side-effecting work. The private runtime shutdown
callback sets Uvicorn's graceful-exit signal only after the authenticated,
bodyless request reaches its exact route. Shutdown drains the Uvicorn
server task and loopback listener, child stdio, the supervised process tree,
Electron's private temporary working directory, and the encrypted provider and
job database sessions. Issue #110 also closes the chat service before its
provider dependencies and discards every process-local session and message.
Issue #111 first cancels and records one payload-free terminal audit outcome for
every active stream. Startup reconciliation records one stable interruption for
an active run left by a prior process without replaying provider work.

Before quitting, Electron main waits for a bounded safe-shutdown assessment. If
work remains active, a native dialog offers **Wait**, **Request cancellation**,
or **Stay open**. Cancellation is cooperative: protected publication can remain
`pending-safe-point`, and Electron never silently abandons it. Process shutdown
continues only after a safe assessment and verified sidecar stop. Electron owns
the supervisor before asynchronous verification or process launch and installs
its named `SIGTERM` handler before runtime startup. It idempotently re-arms the
same handler when the Electron-ready runtime takes ownership, so initialization
cannot leave the signal on its immediate-termination default. A degraded startup
admits no process-local jobs, so its main-only shutdown assessment is explicitly
safe empty only while the supervisor is `idle`, `starting`, or `unavailable`,
has no authenticated session, and has never exposed one. Once an authenticated
session has been exposed, losing it never restores that shortcut. The
explicit-empty case skips the unavailable HTTP assessment but still cancels pre-spawn work,
drains any launch already in flight within the supervisor deadline, and
requires verified process-tree stop.
The first `app.quit()` lifecycle is vetoed during that assessment. After IPC
disposal and verified sidecar stop release every owned resource, the authorized
completion callback uses `app.exit(0)` rather than re-entering
platform-specific window closure.
Packaged release verification retains only exact, newline-framed shutdown phase
and failure codes emitted by Electron Main. It discards decorated, partial,
overlong, and all other process output, and keeps at most the latest 16 accepted
records. A close timeout therefore identifies the last verified lifecycle phase
without exposing bearer values, response bodies, environment values, or local
paths. These records are release-gate diagnostics and do not change the public
application, bridge, or sidecar contracts.
Issue #111's chat stream service is registered for orderly drain before its
routes are enabled. Other future provider streams and database sessions must do
the same.
The native Windows descendant-kill assertion can run only on Windows; the
exact-head hosted `windows-11-arm` receipt is the authoritative native proof.
Non-Windows local runs exercise only the explicit no-op branch and do not
substitute for that evidence.

The supervisor exposes a main-process-only control interface. A fixed-route
internal client may acquire the authenticated session only while its lifecycle
is `ready`; the session is cleared before restart, failure, or shutdown. The
interface also exposes sanitized lifecycle diagnostics and one application-
lifetime manual retry. Concurrent retry requests share a single launch attempt,
and an exhausted retry is a deterministic no-op. Electron main uses the session
only for authenticated requests to its fixed startup-diagnostic, capability,
settings, credential-management, provider-configuration, endpoint-test, and
consent-administration routes, the fixed chat-stream routes, plus the main-only
job shutdown preflight. The
bridge exposes the typed result, sanitized diagnostics, and retry outcome, but
never the session, bearer, port, resolved address, response body, raw HTTP
data, or a credential value.

For Issue #109, Electron main also uses the fixed job list, status, cancel, and
SSE-event routes. Its internal client applies the bearer and `Last-Event-ID`,
requires the exact event-stream content type, bounds a response or event stream
buffer to 1 MiB, and fails a stalled connection after three seconds. The renderer
receives only parsed snapshots, events, and stable coded failures; it receives
no HTTP or sidecar connection authority.

Issues #111 and #112 consume the #110/#56 chat boundaries through fixed
capability, session-lifecycle, stream-start, SSE-event, cancellation, and
acknowledgement clients. Electron Main rejects redirects, wrong content types,
wrong owner/run identities, invalid DTOs, and stale, duplicate, or nonmonotonic
sequences. Neither preload nor the renderer acquires HTTP, bearer, provider,
sidecar-session, or generic route authority.

The supported 0.6.0 `window.ancestry` surface contains exactly `getAppInfo`,
`getStartupDiagnostics`, `getCapabilities`, `retrySidecar`, `getPreferences`,
and `updatePreferences`. The 0.6.0 source-level gated surface adds three opaque
file-grant methods, exactly five settings/credential methods (`getSettings`,
`updateSettings`, `getSecretStatus`, `setSecret`, and `deleteSecret`), and
exactly six provider/consent methods (`getProviderConfiguration`,
`createProviderProfile`, `validateProviderEndpoint`, `previewConsent`,
`createConsent`, and `revokeConsent`). Issue #109 adds exactly five task
request methods (`listJobs`, `getJob`, `cancelJob`, `subscribeJobEvents`, and
`unsubscribeJobEvents`) plus the fixed, validated `onJobEvent` listener. Issue
#111 adds exactly three chat-stream request methods (`startChatStream`,
`cancelChatStream`, and `acknowledgeChatStream`) plus the fixed, validated
`onChatEventBatch` listener. Issue #112 adds exactly three chat-lifecycle methods
(`getChatCapability`, `createChatSession`, and `closeChatSession`) and two native
actions (`openExternalLink` and `copyText`). Issue #348 adds exactly three local-
runtime methods. The resulting source-level gated bridge has 36 fixed request methods;
there is no generic send, listen, route, channel-selection, clipboard, or shell
operation. Issue #104's shutdown client remains Electron-Main-only.
Main accepts a call only from the registered
`WebContents`, its exact current main frame, and the exact trusted
`app://bundle/index.html` URL. It rechecks those facts on every request.
Arguments and responses must also pass strict runtime schemas and a structured-
clone policy that rejects unknown or inherited fields, accessors, symbol keys,
sparse arrays, non-finite numbers, repeated references, cycles, excessive
depth, and excessive UTF-8 bytes or item counts. Preload validates the response
again before exposing it to the renderer.

Main admits at most four non-coalesced operations per renderer and queues at
most eight more. Capability reads share one in-flight operation for up to 32
callers. Every call has an absolute five-second deadline; queue saturation,
timeout, and cancellation return stable redacted codes rather than backend
details. Cross-document or unclassifiable navigation of the main frame,
renderer exit or destruction, bridge replacement, sidecar-session loss or
replacement, and application shutdown cancel and clean up affected work.
Trusted same-document application route changes preserve work; main still
rechecks the exact current frame and trusted URL on every request. Establishing
the first healthy session does not cancel the retry that created it. Timed-out
underlying operations continue to occupy an active slot until they actually
settle, so an uncooperative backend cannot turn repeated renderer timeouts into
unbounded hidden work.

Main owns every task event subscription and binds it to the requesting sender,
current frame, trusted application document, sidecar session, job, opaque
subscription identifier, and last accepted sequence. A sender may hold at most
32 subscriptions. Duplicate identifiers fail, explicit unsubscribe is
idempotent, and a terminal event closes the stream. Cross-document navigation,
renderer exit or destruction, bridge replacement, sidecar-session loss or
replacement, and application shutdown abort and remove affected subscriptions.
Duplicate or stale events are not forwarded. A gap or expired replay cursor is
reported with a stable code so the renderer can refresh the backend snapshot
and create one replacement subscription. No task state is persisted in
renderer storage.

Main also owns every active chat stream and binds it to the requesting sender,
current frame, trusted application document, sidecar session, chat session, and
run. A sender may hold at most four active streams. Main batches events within
16 ms or 4 KiB and measures exact UTF-8 JSON delivery bytes. At 256 KiB of
unacknowledged data it pauses the private sidecar source; a 15-second
acknowledgement stall cancels the exact run and produces a coded terminal
outcome. One interrupted connection may resume the same run from its
acknowledged cursor, but Main never starts or retries provider execution after
output. Terminal delivery stays owned until acknowledged. Cross-document
navigation, renderer exit or destruction, sidecar-session loss or replacement,
bridge disposal, and application shutdown cancel and remove affected streams.
Issue #112's renderer reduces these events into bounded, owner-scoped transient
conversation state. It rejects gaps and ownership mismatches, ignores duplicate
or replayed events, acknowledges accepted batches, exposes explicit stop and
regenerate controls, and closes its session on teardown. Model text passes
through a closed CommonMark/GFM component allowlist with raw HTML, images,
embeds, implicit autolinks, and executable actions disabled.

The two native actions remain narrowly owned by Main. `openExternalLink` accepts
only normalized HTTPS URLs without credentials, control characters, or a custom
port and opens only after a native confirmation displays that exact destination.
`copyText` writes plain text only. The renderer receives no generic shell,
navigation, or clipboard capability.

Preference updates require the last renderer-visible non-negative revision and
return a coded conflict when it is stale. Packaged main persists the exact
bounded preference schema in `preferences.json` beneath Electron's OS app-data
directory. Writes are validated, serialized, and atomically replace the file
without following a preference-file symlink. Missing and supported legacy data
use safe defaults; corrupt and unsupported data produce stable path-free
diagnostics and are not silently overwritten. The renderer receives neither
the storage path nor any additional storage capability.

Application settings use a separate Python-owned schema and revision. A read
returns the complete five-setting catalog with reviewed labels, help, types,
defaults, validation bounds, restart flags, sensitivity flags, and current
values. A patch supplies the exact visible revision and only changed
allowlisted keys. The service rejects stale revisions, unknown keys, sensitive
settings, invalid values, missing schema fields, and unsupported schema
versions before serializing one atomic `AppConfig` replacement. The renderer
cannot select a storage path or submit an arbitrary configuration object.

Credential management is narrower still. Python owns the exact secret-reference
allowlist and exposes only `present`, `missing`, or `unavailable` status plus
explicit set and delete operations. The set request contains one write-only
value and no read route or response can return it. The OS keyring remains the
sole writable authority. Credentials sourced from the environment are
read-only for explicit CLI/headless operation. The packaged sidecar selects
keyring-only mode and never consults those environment variables. Unavailable
or locked keyring behavior fails closed with stable redacted codes instead of
using Electron `safeStorage`, renderer storage, an environment fallback, or a
plaintext file. A successful delete is returned only after an immediate
presence check proves absence. Main, preload, mock fixtures, and renderer
caches retain only status metadata; the renderer clears its password input
before awaiting the bridge and again after every attempt.

## Diagnostics and recovery

User-facing failures are deliberately generic. Stderr is drained but not
forwarded into Electron logs; structural sidecar diagnostics contain no bearer,
port, URL, request, response, genealogy, provider, filesystem payload, raw
exception, or stack. Lifecycle diagnostics contain only state, a generic
failure class, and remaining automatic/manual retry counts.

Once the private sidecar session is ready, Issue #107's fixed read-only route
returns a schema-v1 startup report. It contains an overall `ready` or
`degraded` status, normalized platform/architecture labels, and exactly four
ordered components: configuration, SQLCipher, keyring, and workspace. Each
component has only a reviewed status, stable code, message, remediation,
restart requirement, and `blocks_mutations` value. Unknown schemas, component
names, fields, statuses, or codes fail response validation. Any blocking
component rejects settings and credential mutations with
`STARTUP_MUTATION_BLOCKED`; the renderer also keeps preference changes and
capability loading disabled while still allowing Diagnostics and the one
bounded main-owned retry.

Startup inspection is side-effect-free: it does not write configuration,
initialize a database, create a key, alter keyring contents, or weaken
permissions. Do not add usernames, hostnames, absolute or temporary paths,
environment values, records, prompts, payloads, raw launch frames, response
bodies, executable paths, stderr, exceptions, or stacks to the report or
support evidence.

After the report is ready, writable sidecar startup creates the encrypted
revision `0002` schema only when one bounded inventory query proves the
workspace has no user tables. A complete current schema is reused without DDL,
and only an exact revision `0001` layout receives the reviewed job-table
migration. That packaged migration explicitly begins one native SQLite
transaction before creating either table or index, so any late DDL failure
rolls the entire migration back to revision `0001`. Unversioned partial schemas,
unknown revisions, and missing or unexpected tables fail with
`DATABASE_MIGRATION_REQUIRED`; startup never silently repairs or replaces them.

For a startup or compatibility failure:

1. observe the degraded lifecycle or startup-component state; the window
   remains open for read-only Diagnostics;
2. follow only the reviewed component remediation, such as unlocking the OS
   keyring, restoring valid configuration, installing supported SQLCipher, or
   repairing the app-owned workspace directory and owner-only permissions;
3. use the bounded main-process retry at most once, or quit the application so
   any supervised process is terminated;
4. never initialize a replacement encrypted database, replace an existing key,
   or select plaintext SQLite as a recovery shortcut;
5. reinstall the same complete application build to restore a matched
   Electron/sidecar pair when the failure is structural;
6. run the native smoke and packaged-resource checks for that target;
7. if the problem persists, record only the application version, normalized
   platform labels, gate name, and stable generic failure code.

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
support or `0.6.0` release claim also requires the release tracker, declared
binary-signing mode, platform execution, installation, and packaged assurance
gates to pass. Unpacked CI artifacts do not satisfy those gates.
