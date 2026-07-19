# First-run storage diagnostics

Run a read-only local health check before creating or opening a workspace:

```console
ancestry --json database diagnose
```

The command never creates a database, writes a credential, or reports a secret
value.  It checks SQLCipher availability, the configured credential-store read
path, workspace-directory access, and existing workspace permissions.

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
