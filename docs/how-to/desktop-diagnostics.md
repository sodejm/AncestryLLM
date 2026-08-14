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
