# Desktop diagnostics contract

AncestryLLM desktop diagnostics are local, bounded, and deliberately less
expressive than ordinary application logs. They correlate startup, readiness,
recovery, and verified shutdown without retaining genealogy records or user
activity.

## Correlation and ownership

Electron Main creates one random UUIDv4 for each application launch. The same
identifier is used by the Electron Main, Python core, and packaged sidecar
writers. Main sends it to the sidecar only in the strict private standard-input
launch frame. The value is never placed in command-line arguments, environment
variables, URLs, readiness or health responses, authentication material, or
process output. It is correlation data, not an identity or credential.

The Python parser requires the exact launch-frame fields and a canonical
lowercase UUIDv4. An absent, malformed, extra, or wrong-version field fails the
private launch contract before server startup.

## Version 1 event

Every JSON Lines record conforms to
`schemas/desktop-diagnostic-v1.schema.json` and contains only:

- schema version, UTC timestamp, per-launch UUID, stable event code, severity,
  component, and application version; and
- up to eight metadata values, each a bounded integer, boolean, or null.

An encoded record is limited to 4,096 bytes. Metadata does not accept strings,
arrays, objects, exception text, stack traces, URLs, paths, prompts, names,
database identifiers, provider keys, environment values, or genealogy content.
A distinct stable event code represents each useful state instead of copying
free-form error details.

## Stable event catalog

The version-1 catalog is allowlisted in source. It covers:

- application and renderer lifecycle: `APP_LAUNCH_REQUESTED`,
  `ELECTRON_READY`, `RENDERER_WINDOW_READY`, and `APP_EXIT_AUTHORIZED`;
- verification and launch: `SIDECAR_VERIFICATION_STARTED`,
  `SIDECAR_VERIFICATION_SUCCEEDED`, `SIDECAR_VERIFICATION_REJECTED`,
  `SIDECAR_SPAWN_REQUESTED`, `SIDECAR_SPAWN_SUCCEEDED`, and
  `SIDECAR_SPAWN_FAILED`;
- readiness and health: `SIDECAR_READINESS_ACCEPTED`,
  `SIDECAR_READINESS_REJECTED`, `SIDECAR_HEALTH_SUCCEEDED`,
  `SIDECAR_HEALTH_REJECTED`, `SIDECAR_STARTUP_TIMEOUT`, and
  `SIDECAR_INCOMPATIBLE`;
- recovery: `SIDECAR_RESTART_REQUESTED`, `SIDECAR_RESTART_SUCCEEDED`,
  `SIDECAR_RESTART_FAILED`, `SIDECAR_RESTART_EXHAUSTED`,
  `SIDECAR_MANUAL_RETRY_REQUESTED`, `SIDECAR_MANUAL_RETRY_SUCCEEDED`, and
  `SIDECAR_MANUAL_RETRY_FAILED`;
- session and bridge enforcement: `SIDECAR_SESSION_INVALIDATED`,
  `BRIDGE_SENDER_REJECTED`, `BRIDGE_ROUTE_REJECTED`, and
  `CONFIGURATION_DEGRADED`;
- shutdown: `JOBS_SHUTDOWN_PREP_REQUESTED`,
  `JOBS_SHUTDOWN_PREP_SUCCEEDED`, `JOBS_SHUTDOWN_PREP_FAILED`,
  `SIDECAR_SHUTDOWN_REQUESTED`, `SIDECAR_TERMINATION_REQUESTED`,
  `SIDECAR_TERMINATION_SUCCEEDED`, and `SIDECAR_TERMINATION_FAILED`;
- writer health: `DIAGNOSTIC_WRITER_UNAVAILABLE` and
  `DIAGNOSTIC_WRITER_DEGRADED`; and
- Python and sidecar bootstrap: `PYTHON_CORE_BOOTSTRAP_STARTED`,
  `PYTHON_CORE_READY`, `SIDECAR_BOOTSTRAP_STARTED`, and
  `SIDECAR_SERVER_READY`.

Unknown event codes, components, severities, fields, or metadata are rejected.

## Retention and permissions

Each component writes a separate file (`electron-main.jsonl`,
`python-core.jsonl`, or `desktop-sidecar.jsonl`) to avoid cross-process append
races. The default limit is 512 KiB per file and three files per component,
including the active file. This bounds each stream to 1.5 MiB and all three
streams to 4.5 MiB. Writers request owner-only directory and file permissions
where the platform supports them and refuse symbolic-link directories, active
files, and rotated targets.

All validation, directory creation, rotation, append, open, and clear failures
are non-blocking. When possible, another component records writer degradation;
otherwise the failure remains intentionally silent. Diagnostics never change
application startup, sidecar recovery, security enforcement, or verified
shutdown behavior.

## Native actions and support boundary

The renderer receives only two fixed, zero-argument actions: open the dedicated
diagnostics directory in the operating system file browser and clear the
allowlisted component files. Electron Main resolves the fixed directory beneath
its application data root. No path, URI, filename, or generic filesystem action
crosses the bridge, and the clear operation refuses symbolic links.

There is no export action in this release. Diagnostics are not uploaded,
transmitted as telemetry, written to the console, or retained as CI or release
artifacts. A user who must provide confidential support evidence inspects the
files locally and uses a separately approved transfer channel. Records must not
be committed or attached to a public issue without review.

The sidecar's exact standard-error shutdown receipt remains a separate verified
protocol. Diagnostic records neither replace it nor parse, merge, or trust
arbitrary diagnostic JSON or child-process output as shutdown evidence.

## Verification

Source tests cover shared correlation, the full lifecycle and recovery catalog,
writer failure isolation, bounded retention, malformed and oversized records,
symbolic-link refusal, bridge sender and route rejection, package-verification
failure, and privacy canaries across persisted records and renderer failures.
Desktop package and release checks must preserve those properties and must not
publish diagnostic files as artifacts.
