# Recover with desktop diagnostics

Use the desktop Diagnostics workspace when startup is **Degraded**, a local
prerequisite is blocked, or the sidecar stops responding. Diagnostics is a
sanitized recovery surface: it does not expose genealogy values, credentials,
host details, environment values, absolute paths, process output, response
bodies, raw exceptions, or stack traces.

> The v0.6 source labels below extend the bounded desktop control surface. A
> released 0.5 installer does not include later domain work merely because the
> source contract is documented here.

## Inspect a startup report

1. Open **Diagnostics**. If first run is blocked, choose
   **Open read-only diagnostics** from the welcome screen.
2. Under **Desktop service**, note whether startup is **Starting**, **Ready**,
   **Degraded**, or **Stopped**.
3. Under **Startup checks**, inspect each component:
   **Configuration**, **Encrypted database support**, **Credential storage**,
   and **Local workspace**.
4. Record only the displayed stable code, reviewed message, and sanitized
   remediation. Do not copy local system details into an issue or chat.

Component status is displayed as text, not color alone. A report may use
**Missing**, **Present**, **Unavailable**, **Ready**, **Warning**, or
**Blocked**, together with whether restart is required and whether mutations
are blocked.

## Apply bounded recovery

Correct only the prerequisite named by the report:

- For configuration, restore a valid reviewed configuration. Do not overwrite
  unknown state.
- For encrypted database support, install or repair the supported SQLCipher
  build. Never switch to plaintext SQLite.
- For credential storage, unlock or repair the OS keyring. Do not place a
  secret in a document, log, environment dump, or plaintext file.
- For the local workspace, repair the application-owned directory and its
  owner-only permissions. Do not create a replacement genealogy database.

Choose **Retry desktop service** once. Concurrent retries share the same bounded
launch attempt. If recovery still fails, quit and reopen the application. A
version mismatch or incomplete application installation requires reinstalling
the same supported, target-matched build rather than bypassing verification.

While startup is degraded, Settings remains read-only. Mutations fail closed
with `STARTUP_MUTATION_BLOCKED`; preference failures use
`PREFERENCES_UNAVAILABLE` or `INTERNAL_ERROR` rather than exposing storage
details. If the diagnostics request itself is unavailable, the shell says
**Desktop diagnostics are temporarily unavailable.**

## Interpret common codes

The exact code is more useful than a screenshot of private local state:

- `CONFIGURATION_READY`, `SQLCIPHER_READY`, `KEYRING_READY`, and
  `DATABASE_DIRECTORY_READY` identify healthy checks.
- `CONFIG_INVALID` identifies reviewed configuration failure.
- `startup_failed`, `startup_timeout`, `incompatible_build`, and `crash_loop`
  identify supervisor lifecycle outcomes.
- An invalid or unknown bridge failure is normalized to
  `UNEXPECTED_ERROR` or `INTERNAL_ERROR` and remains sanitized.

See the [Desktop reference](../reference/DESKTOP.md) for the broader code and
state lookup. When reporting a failure, provide the AncestryLLM version,
normalized operating-system and architecture labels, and stable code only.

## Inspect and clear local diagnostic records

The Diagnostics workspace also exposes two fixed native actions:

1. Choose **Open diagnostics folder** to open the application-owned diagnostic
   directory in the operating system's file browser.
2. Inspect only the dedicated `electron-main.jsonl`, `python-core.jsonl`, and
   `desktop-sidecar.jsonl` records and their bounded rotations. Each line is a
   validated version-1 event with a stable code and, when needed, numeric or
   boolean metadata.
3. Choose **Clear diagnostics** to remove only those allowlisted diagnostic
   files. Clearing refuses symbolic-link targets and never accepts a path from
   the renderer.

One random UUID correlates the three component streams for a single desktop
launch. It is not an account, device, person, authentication, or telemetry
identifier. The application does not place it in a URL, command-line argument,
environment variable, readiness response, authentication material, or process
output.

There is no diagnostic export or upload action in this release. Records remain
local, are not sent over the network, and are not collected as CI or release
artifacts. If confidential support work requires a copy, inspect the files
locally first and transfer only through the approved confidential channel.
Never attach unreviewed records to a public issue. Clear the records after the
support need ends.

A failure to create, rotate, append, open, or clear diagnostics is deliberately
non-blocking. The app continues its authoritative startup, security, recovery,
and shutdown behavior and presents only a stable, generic action failure.
