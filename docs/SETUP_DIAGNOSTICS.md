# Setup diagnostics

## Repository environment setup

A source checkout requires a system-supplied Python 3.12 through 3.14. The
checked-in `.python-version` selects 3.12 by default, and repository policy
requires exactly `uv` 0.12.1 with Python downloads disabled. Run:

```console
make setup
```

On Windows, Make looks for `python`; on macOS and Linux it looks for `python3`.
Set `PYTHON` to another system executable when necessary, for example
`make setup PYTHON=python3.13`. Do not recover by installing `uv` with `pip`,
using an executable from `PATH`, enabling Python downloads, using `uvx`, or
adding `uv run --with` dependencies.

| Failure | Meaning | Required action |
|---|---|---|
| `UVENV_PYTHON_NOT_FOUND` | The selected system Python executable is absent. | Install a supported system Python or set `PYTHON` to an existing supported executable, then retry. |
| `UVENV_PYTHON_VERSION_UNSUPPORTED` | The selected interpreter is outside Python 3.12-3.14 or its version cannot be read. | Select a supported system interpreter; do not let `uv` download one. |
| Bootstrap receipt reports a stable failure category | The cached or downloaded `uv`, verifier, policy, identity, or provenance failed closed. | Follow the [verified uv bootstrap recovery procedure](https://github.com/sodejm/AncestryLLM/blob/main/docs/security/verified-uv-bootstrap.md); never bypass verification or substitute another `uv`. |

Successful setup verifies the repository-local executable, then runs
`uv sync --locked --all-extras --all-groups`. A wrong `uv` version or failed
bootstrap never reaches an environment command.

## Packaged desktop first-run diagnostics

The packaged desktop opens with a local-only startup review before it exposes
the rest of the shell. **Local Desktop (Recommended)** is the only available
choice in 0.6. **Connect Remote** and **Host Remote** remain visible as advanced
future choices, but they cannot be selected. First run never discovers a
service, binds a public or LAN listener, starts a container, requests an
account, or enables a cloud provider.

The startup report is a closed schema-v1 response with exactly four
components: configuration, SQLCipher, OS keyring, and workspace. Each component
has a stable code, reviewed remediation text, restart guidance, and an explicit
mutation-blocking flag. The report includes only a normalized operating-system
and architecture label. It excludes credential values, environment contents,
usernames, hostnames, absolute or temporary paths, genealogy records, prompts,
provider payloads, raw exceptions, response bodies, and process output.

When any component blocks startup, the desktop offers read-only diagnostics
instead of silently continuing. Capabilities are not queried and preference,
settings, and credential mutations fail with `STARTUP_MUTATION_BLOCKED` until a
fresh report is healthy. The diagnostics view permits one bounded retry; if the
problem persists, follow the remediation and relaunch. Retry never initializes
a database, generates or replaces an existing workspace key, rewrites a
damaged configuration, changes permissions, or falls back to plaintext SQLite.

Packaged desktop startup uses the OS keyring exclusively. It deliberately
ignores environment-injected application secrets, even when they are present.
The documented read-only environment fallback remains available only to the
CLI and headless/CI operation described below.

| Code | Required recovery |
|---|---|
| `CONFIG_INVALID`, `CONFIGURATION_UNAVAILABLE` | Restore a reviewed configuration or repair access; the desktop does not overwrite it. |
| `SQLCIPHER_UNAVAILABLE` | Install or repair the supported SQLCipher runtime; never substitute plaintext SQLite. |
| `KEYRING_READ_FAILED` | Unlock or repair the OS credential store and grant the application access; never copy a key into configuration or an environment variable for packaged use. |
| `DATABASE_DIRECTORY_UNWRITABLE`, `DATABASE_PERMISSIONS_WEAK` | Repair ownership and owner-only permissions without replacing the workspace. |
| `DATABASE_DIRECTORY_MISSING` | Create an owner-only local data directory, then retry. This warning does not itself authorize a database write. |

`CONFIGURATION_READY`, `SQLCIPHER_READY`, `KEYRING_READY`, and
`DATABASE_DIRECTORY_READY` identify a healthy component. A sidecar protocol,
version, or build mismatch is reported by the same fail-closed lifecycle and
must be repaired by reinstalling the exact supported application package.

## First-run storage diagnostics

Run a read-only local health check before creating or opening a workspace:

```console
ancestry --json database diagnose
```

The command never creates a database, writes a credential, or reports a secret
value.  It checks SQLCipher availability, the configured credential-store read
path, workspace-directory access, and existing workspace permissions.

### Diagnostic codes

| Code | Meaning | Required action |
|---|---|---|
| `SQLCIPHER_READY` | The SQLCipher driver imports and reports a cipher version. | Continue. |
| `SQLCIPHER_UNAVAILABLE` | SQLCipher is missing, failed to initialize, or the driver does not report encryption support. | Install a supported SQLCipher-enabled build; never use plaintext SQLite. |
| `KEYRING_READY` | The configured credential backend can be queried without writing. | Continue. |
| `KEYRING_READ_FAILED` | The credential backend cannot be queried. | Repair/unlock the OS credential store and rerun diagnostics. |
| `DATABASE_DIRECTORY_MISSING` | The workspace parent does not exist yet. | Create an owner-only data directory before first use. |
| `DATABASE_DIRECTORY_UNWRITABLE` | The workspace parent cannot be written or traversed. | Select a writable directory owned by the current user. |
| `DATABASE_DIRECTORY_READY` | The workspace parent is writable. | Continue. |
| `DATABASE_PERMISSIONS_WEAK` | An existing workspace grants group/other permissions. | Restrict the file to owner-only permissions. |

Diagnostics are advisory until the database is opened. Database initialization
and opening remain fail-closed for plaintext files, missing keys, wrong keys,
and failed integrity checks.

### Platform recovery

- macOS: unlock the login keychain, then ensure the application can access it
  in Keychain Access.  Reinstall the supported SQLCipher wheel if the command
  reports `SQLCIPHER_UNAVAILABLE`.
- Windows: unlock or repair Credential Manager and use a user-writable data
  directory.  Do not replace an existing workspace key when `DATABASE_KEY_MISSING`
  is reported; restore the matching key from secure backup instead.
- Linux desktop: install and unlock a supported Secret Service/keyring backend
  for the desktop session.  Ensure the workspace directory is owned by the
  current user.
- Headless CI: use the documented environment-injection fallback only for
  ephemeral test secrets. Inject `ANCESTRYLLM_DATABASE_KEY` through the CI
  platform's protected secret mechanism. The fallback is read-only: it does not
  copy the value into a keyring, configuration file, or log. AncestryLLM never
  loads `.env`; do not place the value in configuration, command arguments,
  shell history, logs, or repository artifacts.

`PLAINTEXT_DATABASE_REJECTED`, `DATABASE_INTEGRITY_FAILED`, and
`DATABASE_KEY_MISSING` are fail-closed protections.  Stop using the affected
file and follow the encrypted-backup recovery process; never force a plaintext
fallback or generate a replacement key for an existing workspace.

## Deployment-profile diagnostics and recovery

Inspect the stored non-secret profile and compare it with the current native
runtime before troubleshooting another command:

```console
ancestry --json deployment status
ancestry --json deployment diagnose
```

Local Desktop is the default when the `[deployment]` table is absent. Unknown
fields, malformed values, unsupported schema versions, invalid mode/topology
pairs, and incomplete remote identities reject configuration loading. Preserve
a copy of the configuration before repair. Restore a known-good file or review
and remove only the invalid `[deployment]` table to recover the safe Local
Desktop default; do not rewrite provider, storage, or secret state as part of
profile recovery.

A valid non-local stored profile blocks ordinary commands while its remote or
host runtime is unavailable. Profile status, diagnostics, previews, redacted
metadata, and recovery remain accessible. Recover to Local Desktop by using
the schema and revision from `status`, then bind the switch to a fresh preview:

```console
ancestry --json deployment preview \
  --mode local-desktop \
  --schema-version <schema-version> \
  --expected-revision <revision>

ancestry --json deployment switch \
  --mode local-desktop \
  --schema-version <schema-version> \
  --expected-revision <revision> \
  --confirm <confirmation-from-preview> \
  --unattended
```

The command-line switch is deliberately unattended-only: omitting
`--unattended`, changing the target, or using a stale revision or confirmation
fails without mutation. An interrupted atomic save preserves the prior file.
No profile operation starts a listener or container, discovers a service, or
moves genealogy data.

| Code | Meaning | Required action |
|---|---|---|
| `DEPLOYMENT_PROFILE_INVALID` | Stored or requested profile structure is invalid. | Restore reviewed schema-v1 structure or recover to an absent `[deployment]` table. |
| `DEPLOYMENT_SCHEMA_UNSUPPORTED` | The requested command schema is not exactly v1. | Reload status and use its exact schema version; do not downgrade stored state. |
| `DEPLOYMENT_REVISION_CONFLICT` | Configuration changed after it was read. | Reload status and preview the exact target again. |
| `DEPLOYMENT_CONFIRMATION_INVALID` | Confirmation does not bind to the exact target and revision. | Discard it and obtain a fresh preview. |
| `DEPLOYMENT_PERSISTENCE_FAILED` | The atomic configuration update could not be published. | Leave the original configuration in place, repair filesystem access, and retry from status. |
| `DEPLOYMENT_RUNTIME_MISMATCH` | Stored intent has no active reviewed runtime. | Diagnose the mismatch or explicitly recover to Local Desktop. |
| `DEPLOYMENT_PROVIDER_CONFLICT` | `provider=none` is paired with a non-local profile. | Recover to Local Desktop; provider and consent changes remain separate. |
| `DEPLOYMENT_ENROLLMENT_REQUIRED` | Connect Remote lacks its reviewed authenticated enrollment. | Keep Local Desktop until Issue #357 ships. |
| `DEPLOYMENT_HOST_SETUP_REQUIRED` | Host Remote lacks its reviewed headless setup authority. | Keep or recover Local Desktop; neither the #363 host-control foundation nor the #348 runtime-tool manager activates hosting. |

## Container-control failures

The #363 host-control foundation reports the following stable, redacted codes.
They are not a Host Remote runbook or an end-user troubleshooting surface, and
no code permits a PATH, ambient-context, remote, or unverified fallback.

| Codes | Meaning |
|---|---|
| `INVALID_POLICY`, `INVALID_PLAN` | A closed schema-v1 policy or generated plan is not exact or safe. |
| `ENDPOINT_UNTRUSTED`, `ENDPOINT_CHANGED` | The app-owned Unix socket is untrusted or changed across verification. |
| `ENGINE_UNTRUSTED`, `RESOURCE_CONFLICT` | Engine identity/compatibility or exact owned-resource identity failed. |
| `AUTHORIZATION_REQUIRED`, `CONTROL_FAILED` | Exact operation authorization is absent or the verified lifecycle action failed. |
| `PROCESS_REQUEST_INVALID`, `PROCESS_INPUT_LIMIT`, `PROCESS_OUTPUT_LIMIT` | A fixed subprocess request or one of its byte bounds failed. |
| `PROCESS_TIMEOUT`, `PROCESS_EXIT`, `PROCESS_RESPONSE_INVALID` | A bounded process timed out, failed, or returned nonconforming output. |

## Local-runtime management failures

The #348 manager supports only native macOS arm64 and returns sanitized stable
codes through packaged Settings and the noninteractive executable. Retry from
status and obtain a fresh review after any repair; never bypass a digest,
ownership, confirmation, or host check.

| Codes | Meaning and required action |
|---|---|
| `RUNTIME_POLICY_INVALID`, `RUNTIME_POLICY_SCHEMA_UNSUPPORTED` | The closed policy is missing, malformed, or unsupported. Reinstall the exact reviewed application package; do not edit or substitute policy fields. |
| `RUNTIME_REQUEST_INVALID`, `RUNTIME_PLAN_STALE`, `RUNTIME_CONFIRMATION_REQUIRED` | The operation, revision, or exact confirmation is invalid. Reload status, review the operation again, and apply that exact fresh plan. |
| `RUNTIME_HOST_UNSUPPORTED` | The host is not Apple silicon on macOS 13 or later, hardware virtualization is unavailable, or less than 24 GiB is free. Use a supported host or restore the required host capacity. |
| `RUNTIME_OFFLINE_UNAVAILABLE` | Offline mode lacks a complete verified cache. Retry online when approved, or restore the exact reviewed cached artifacts. |
| `RUNTIME_DOWNLOAD_FAILED` | A bounded upstream transfer failed. Retry; the manager resumes a valid partial transfer and re-verifies the completed artifact before use. |
| `RUNTIME_ARTIFACT_INTEGRITY`, `RUNTIME_COMPONENT_INTEGRITY` | An archive, license, VM image, or extracted component differs from reviewed size or digest. Leave it unexecuted and reinstall from the exact policy source. |
| `RUNTIME_STORAGE_UNSAFE`, `RUNTIME_OWNERSHIP_INVALID` | App-owned storage or runtime ownership cannot be proven. Repair owner-only storage or use the explicit reviewed removal path; never adopt another profile or context. |
| `RUNTIME_NOT_INSTALLED` | The requested lifecycle action needs the app-owned runtime tools. Review and apply setup first. |
| `RUNTIME_PROCESS_FAILED`, `RUNTIME_HEALTH_FAILED` | A bounded lifecycle process or the isolated runtime health check failed. Review repair, retain the sanitized code for support, and do not substitute an ambient Docker endpoint. |

The full implementation boundary and the native macOS arm64 evidence limits
are documented in the
[published deployment operations guide](https://sodejm.github.io/AncestryLLM/DEPLOYMENT.html#host-control-and-macos-arm64-runtime-tools).
