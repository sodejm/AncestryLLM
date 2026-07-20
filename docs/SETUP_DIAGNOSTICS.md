# First-run storage diagnostics

Run a read-only local health check before creating or opening a workspace:

```console
ancestry --json database diagnose
```

The command never creates a database, writes a credential, or reports a secret
value.  It checks SQLCipher availability, the configured credential-store read
path, workspace-directory access, and existing workspace permissions.

## Diagnostic codes

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

## Platform recovery

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
  ephemeral test secrets.  Do not write secrets into configuration files,
  command arguments, logs, or repository artifacts.

`PLAINTEXT_DATABASE_REJECTED`, `DATABASE_INTEGRITY_FAILED`, and
`DATABASE_KEY_MISSING` are fail-closed protections.  Stop using the affected
file and follow the encrypted-backup recovery process; never force a plaintext
fallback or generate a replacement key for an existing workspace.
