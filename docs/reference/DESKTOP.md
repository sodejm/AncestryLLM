# Desktop reference

This page is the lookup reference for the v0.6 desktop source contracts. It
does not turn a development build into a supported release or expand the
released 0.5 installer. The desktop remains a sandboxed presentation over fixed
typed bridges and a private authenticated loopback sidecar; it is not a public
or LAN API.

## Destinations and navigation

| Destination | Route | Shortcut | Purpose |
|---|---|---:|---|
| **Home** | `#/` | <kbd>H</kbd> | Application, offline posture, startup state, and sanitized capabilities |
| **Chat** | `#/chat` | <kbd>C</kbd> | Transient provider conversation with no domain or tool authority |
| **Tasks** | `#/tasks` | <kbd>T</kbd> | Presentation of backend-owned job lifecycle state |
| **Diagnostics** | `#/diagnostics` | <kbd>D</kbd> | Sanitized startup state and bounded recovery |
| **Settings** | `#/settings` | <kbd>S</kbd> | Reviewed preferences, profiles, consent, deployment, runtime, and write-only credentials |

The navigation region is labeled **Workspaces**, and the current route uses
current-page semantics. <kbd>Ctrl+K</kbd> on Windows and Linux or
<kbd>Command+K</kbd> on macOS opens **Go to a workspace**. The
**Filter destinations** field receives focus. <kbd>Escape</kbd> dismisses the
dialog and restores its trigger; choosing a destination focuses its heading.
**Skip to workspace** moves focus to the current workspace.

## Startup and diagnostic states

| State | Meaning | Available recovery |
|---|---|---|
| **Starting** | The bounded sidecar launch is still in progress. | Wait for the terminal startup report. |
| **Ready** | Required local checks passed. | Continue to Home or use the normal destinations. |
| **Degraded** | A required check failed or mutation is blocked. | Open read-only Diagnostics and follow the code-specific remediation. |
| **Stopped** | The sidecar is not available. | Retry once, relaunch, or reinstall the same verified build. |

The startup report contains exactly **Configuration**, **Encrypted database
support**, **Credential storage**, and **Local workspace**. Component statuses
are **Missing**, **Present**, **Unavailable**, **Ready**, **Warning**, or
**Blocked**. Reports contain reviewed messages, stable codes, sanitized
remediation, restart requirements, and mutation-blocking facts only.

Supervisor outcomes include `startup_failed`, `startup_timeout`,
`incompatible_build`, and `crash_loop`. Healthy fixture codes include
`CONFIGURATION_READY`, `SQLCIPHER_READY`, `KEYRING_READY`, and
`DATABASE_DIRECTORY_READY`; configuration failure may use `CONFIG_INVALID`.

The Diagnostics destination also offers fixed **Open diagnostics folder** and
**Clear diagnostics** actions. Electron Main resolves the dedicated local
directory; the renderer supplies no path and receives no path. Three bounded
JSON Lines streams correlate Electron Main, Python core, and sidecar lifecycle
events with one random per-launch UUID. Records contain stable codes and small
numeric or boolean metadata only. They are never telemetry, are not uploaded or
collected as CI artifacts, and do not replace the exact shutdown receipt. This
release intentionally provides no export action. See the
[diagnostic event contract](DESKTOP_DIAGNOSTICS.md) for the complete catalog,
retention, and privacy rules.

## Task states

| State | Meaning |
|---|---|
| **Queued** | Accepted by the backend but not yet running. |
| **Running** | Work is active. Progress may be determinate or report **Progress total unknown.** |
| **Cancelling** | The backend accepted a cooperative cancellation request. |
| **Waiting for a safe point** | Atomic work must reach its declared boundary before stopping. |
| **Completed** | One terminal successful result was recorded. |
| **Failed** | One terminal coded failure and sanitized remediation was recorded. |
| **Cancelled** | One terminal cancellation result was recorded. |

Only Queued and Running tasks expose **Cancel task**. Artifacts use
**Pending**, **Ready**, **Failed**, or **Revoked** and contain type, media type,
byte size, and status only. Direct paths and open authority are excluded; a
Ready artifact requires a separate grant-mediated product action.

## Settings catalog

The Settings workspace groups these reviewed areas:

- **General** for color scheme and reduced motion.
- **Storage** for non-secret bounded settings.
- **Provider activation** and **Limits** for the reviewed application catalog.
- **Provider configuration** for tested profiles.
- **Consent** and **Privacy** for exact disclosure grants.
- **Deployment mode** for Local Desktop and unavailable advanced intents.
- **Local container runtime** for the separately bounded macOS arm64 tool
  substrate.
- **Secrets/Credentials** for write-only keyring operations.

Mutations require **Ready** startup state and a current optimistic revision.
Secret status is limited to **Present**, **Missing**, or **Unavailable**. The
renderer never receives a secret value; the Python secret store and OS keyring
remain authoritative.

## Provider identities and endpoints

| Provider ID | Display name | Reviewed default endpoint |
|---|---|---|
| `ollama` | Ollama | `http://127.0.0.1:11434` |
| `openai` | OpenAI | `https://api.openai.com/v1` |
| `anthropic` | Anthropic | `https://api.anthropic.com` |
| `gemini` | Gemini | `https://generativelanguage.googleapis.com` |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` |

Ollama is local only at an explicitly tested loopback endpoint. Cloud profiles
use the reviewed built-in HTTPS destination. All profiles must pass **Test
endpoint** before save; redirects, proxy inheritance, custom cloud endpoints,
and stale destination identities fail closed.

The reviewed consent purpose is bound to an exact profile, provider, model,
module, and selected data classes. Available classes are **Public genealogy**,
**Deceased person**, **Living person**, **Possibly living person**,
**Free-text note**, **Source transcription**, and **Government identifier**.
Optional cost and provider-retention choices are part of the exact preview.

`provider=none` is the network-free offline contract. It selects no provider,
imports no provider SDK for execution, and cannot run desktop chat. A profile,
credential, or renderer selection cannot override it.

## File-grant contract

| Purpose | Access | Format |
|---|---|---|
| `gedcom-read` | Read | GEDCOM |
| `rootsmagic-read` | Read | RootsMagic |
| `gedcom-write` | Write | GEDCOM |
| `json-write` | Write | JSON |
| `markdown-write` | Write | Markdown |

Input validation is `validated-input`; output validation is `new-output` or
`replacement-confirmed`. Every opaque grant is bound to
`requesting-window`, `app-session`, and `single-use` scope. Safe display
metadata is limited to filename, format, byte size, and intent; a native path,
grant-to-path map, unrestricted filesystem method, or user-typed path is never
exposed to the renderer.

The file-grant card is a reusable contract, not a standalone destination. A
supported product action must request the native chooser. RootsMagic inputs
remain immutable and GEDCOM processing remains loss-minimal.

### Mediated file operations

Issue #352 extends opaque grants into one transport-neutral mediated-operation
request and result contract. The allowlist contains `rootsmagic.export`,
`gedcom.merge`, `gedcom.subtree`, and `gedcom.quality`; it does not introduce a
renderer filesystem API, a second command registry, or a path-bearing DTO.

For local execution, Electron Main copies each selected input into a private
0700 operation directory, makes staged inputs immutable to the worker, and
constructs only the exact read-only input and read-write output mounts required
for that operation. The complete realized mount set must match the approved
plan before work begins. For remote execution, the trusted adapter receives
one-use bounded streams and opaque metadata only; it never receives or
interprets a local path. Progress, results, and stable errors remain path-free
for both transports.

Each operation is single-use and bounded to two concurrent operations, five
minutes, 8 GiB of aggregate selected input, the per-purpose file-size and count
limits in [file ingress](FILE_INGRESS.md), and zero archive expansion or nested
archive depth. Every declared output must validate before any output is
published. Publication then uses a same-directory temporary file and atomic
replace for each user-selected destination. Cancellation, expiry, failure, and
startup cleanup remove only exact private operation directories and fail closed
if unexpected entries prevent safe cleanup.

This is a source-level Main-process foundation for future genealogy adapters.
No renderer route or supported RootsMagic/GEDCOM product workflow is claimed by
this contract alone.

## Chat limits and states

Chat accepts at most 32 concurrent sessions, 32 stored messages per session,
16,384 characters per message, 65,536 characters of context, 4,096 output
tokens, a temperature ceiling of 1, a 120-second timeout, and one safe retry
before output begins. Tools are disabled, content is transient, payload
retention defaults to false, and output is advisory—not evidence.

Conversation status is **Not started**, **Local**, or **Remote**. Run status is
**Streaming**, **Stopping**, **Completed**, **Interrupted**, or **Failed**.
Cloud runs require compatible active consent; local loopback runs report
**Not required for local provider**.

## Stable error codes

The renderer displays a reviewed message and sanitized remediation for a known
code. Unknown bridge failures normalize to `UNEXPECTED_ERROR` or
`INTERNAL_ERROR` and do not expose raw content.

| Area | Codes |
|---|---|
| Bridge and startup | `INVALID_REQUEST`, `UNAUTHORIZED_SENDER`, `INVALID_RESPONSE`, `BRIDGE_OVERLOADED`, `REQUEST_CANCELLED`, `REQUEST_TIMEOUT`, `SIDECAR_UNAVAILABLE`, `SIDECAR_REQUEST_FAILED`, `STARTUP_MUTATION_BLOCKED`, `INTERNAL_ERROR` |
| Preferences | `PREFERENCES_UNAVAILABLE`, `PREFERENCES_CONFLICT`, `PREFERENCES_INVALID` |
| File grants | `FILE_SELECTION_INVALID`, `FILE_TOO_LARGE`, `FILE_GRANT_FORBIDDEN`, `FILE_GRANT_REVOKED`, `FILE_GRANT_STALE`, `FILE_GRANT_CONFLICT`, `FILE_DIALOG_FAILED`, `FILE_OPERATION_CANCELLED` |
| File mediation | `INVALID_REQUEST`, `OPERATION_REPLAYED`, `OPERATION_CONFLICT`, `LIMIT_EXCEEDED`, `CANCELLED`, `TIMED_OUT`, `GRANT_REJECTED`, `ADAPTER_FAILED`, `OUTPUT_INVALID`, `MOUNT_MISMATCH`, `CLEANUP_FAILED` |
| Tasks | `JOB_ID_INVALID`, `JOB_NOT_FOUND`, `JOB_EVENT_CURSOR_INVALID`, `JOB_EVENT_REPLAY_EXPIRED`, `JOB_SERVICE_UNAVAILABLE`, `JOB_SUBSCRIBER_LIMIT`, `JOB_SUBSCRIPTION_CLOSED`, `JOB_SUBSCRIPTION_CONFLICT`, `JOB_EVENT_STREAM_FAILED` |
| Chat sessions | `CHAT_SESSION_INVALID`, `CHAT_SESSION_NOT_FOUND`, `CHAT_SESSION_LIMIT`, `CHAT_SESSION_BUSY`, `CHAT_SERVICE_UNAVAILABLE` |
| Chat streams | `CHAT_STREAM_NOT_FOUND`, `CHAT_STREAM_CURSOR_INVALID`, `CHAT_STREAM_REPLAY_EXPIRED`, `CHAT_STREAM_SERVICE_UNAVAILABLE`, `CHAT_STREAM_LIMIT`, `CHAT_STREAM_BACKPRESSURE_TIMEOUT`, `CHAT_STREAM_STALLED`, `CHAT_STREAM_EVENT_INVALID` |

## Accessibility contract

- Keyboard focus enters each destination at its heading and never depends on
  visual position.
- The palette has a named dialog, initial filter focus, Escape dismissal,
  trigger restoration, and route-heading focus after selection.
- **Skip to workspace** is available from route-heading focus.
- Task changes and chat status use bounded polite announcements. The chat
  transcript itself has live announcements disabled.
- State, warning, and current-route meaning use text and semantics as well as
  color. Focus indicators work in light, dark, forced-color, and reduced-motion
  modes.
- The minimum 720-by-560 layout remains usable at 200% zoom. Automated checks
  supplement, but do not replace, target-platform screen reader review.

## Platform behavior

- **macOS:** use <kbd>Command+K</kbd>. A supported local-runtime
  manager exists only for macOS arm64 and remains a tool substrate, not an
  application container. VoiceOver is the manual screen reader target.
- **Windows:** use <kbd>Ctrl+K</kbd>. Windows 11 ARM is a native
  target; do not substitute x64 emulation for release evidence. NVDA or Narrator
  provides manual screen reader coverage.
- **Linux:** use <kbd>Ctrl+K</kbd>. The supported desktop target is
  Ubuntu 24.04 x64, and no local-runtime manager is claimed. Use the available
  platform screen reader for manual review.

Native chooser appearance, installation prompts, keyring UI, and path syntax
differ by operating system. The boundary does not: no renderer path, secret,
sidecar bearer, arbitrary native command, direct provider client, or public API
is introduced.

## Shipped-contract provenance

The learning path is grounded in the delivered desktop contracts. These issue
links identify the implementation record; the documentation links identify
where each user-visible contract is taught and referenced.

| Product dependency | Verified documentation |
|---|---|
| [#106 accessible shell and navigation](https://github.com/sodejm/AncestryLLM/issues/106) | [First run](../tutorials/desktop-first-run.md) and [accessibility contract](#accessibility-contract) |
| [#107 onboarding and diagnostics](https://github.com/sodejm/AncestryLLM/issues/107) | [First run](../tutorials/desktop-first-run.md) and [diagnostic recovery](../how-to/desktop-diagnostics.md) |
| [#108 settings, providers, consent, and secrets](https://github.com/sodejm/AncestryLLM/issues/108) | [Provider and consent setup](../how-to/desktop-provider-consent.md) and [settings catalog](#settings-catalog) |
| [#109 tasks, cancellation, and coded errors](https://github.com/sodejm/AncestryLLM/issues/109) | [Task monitoring](../how-to/desktop-tasks.md) and [stable error codes](#stable-error-codes) |
| [#112 transient chat](https://github.com/sodejm/AncestryLLM/issues/112) | [Desktop chat](../how-to/desktop-chat.md) and [chat limits and states](#chat-limits-and-states) |
| [#103 opaque file grants](https://github.com/sodejm/AncestryLLM/issues/103) and [#352 mediated operations](https://github.com/sodejm/AncestryLLM/issues/352) | [Desktop file access](../how-to/desktop-file-access.md), [file ingress](FILE_INGRESS.md), and [mediated file operations](#mediated-file-operations) |

## Related explanations and procedures

- [Desktop first run](../tutorials/desktop-first-run.md)
- [Desktop shell](../explanation/DESKTOP_SHELL.md)
- [Privacy and consent](../explanation/PRIVACY_AND_CONSENT.md)
- [Electron and FastAPI desktop ADR](../ADR-0025-electron-fastapi-desktop.md)
- [Data-flow threat model](../THREAT_MODEL.md)
- [Desktop verification](../DESKTOP_VERIFICATION.md)
