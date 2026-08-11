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
| `DEPLOYMENT_HOST_SETUP_REQUIRED` | Host Remote lacks its reviewed headless setup authority. | Keep Local Desktop until Issues #348 and #363 ship. |
