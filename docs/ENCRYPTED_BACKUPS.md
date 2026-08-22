# Encrypted backup and recovery

Run `ancestry database backup DESTINATION`. The destination must not exist and
is written with restrictive permissions through SQLCipher's online backup API.
The backup uses the same key reference held in the OS keyring; copying only the
database without securely backing up that key is not a recovery strategy.

If the destination already exists, the command fails with `BACKUP_EXISTS`
without printing the destination path. Choose a different destination or remove
the existing item only after confirming it is no longer needed. The existing
item remains unchanged, and the failed command does not publish a partial
backup.

Keep database and key backups separate. Test recovery on an offline machine by
restoring the key into an OS credential store, opening a copy, checking the
schema revision, and running integrity checks. Plain SQLite files and wrong keys
are rejected. Never commit a database or backup to Git.

## Transient file-operation recovery

Issue #352's `mediated-runtime` staging area is scratch space, not a backup,
workspace, or retained family-tree copy. Inputs are immutable private copies;
outputs remain staged until every declared artifact validates. The original
RootsMagic or GEDCOM source and the prior destination remain the recovery
authority until a validated output is published through same-directory atomic
replacement.

Success, failure, cancellation, and timeout remove the exact private operation
directory. On startup, Electron Main removes only recognized operation
directories beneath the fixed owner-only staging root. An unexpected file,
link, directory shape, permission, or owner fails closed and is preserved for
investigation instead of triggering broad cleanup. This cleanup does not scan
or delete user-selected source or destination directories.

After interruption, restart the application and retry with fresh opaque
grants. Never salvage, move, or publish staged files, and never treat them as a
backup. If recovery fails, retain the source and prior output and record only
the stable operation code and phase; support material must exclude host,
staging, destination, and container mount paths.

## Deployment-profile metadata and future runtimes

Backup manifests and support bundles may include the redacted structural
profile evidence produced by:

```console
ancestry --json deployment metadata --purpose backup
```

The evidence contains the profile schema and revision, mode, topology, and an
optional endpoint-identity digest. It excludes the raw endpoint origin,
filesystem paths, providers, credentials, environment values, host details,
and genealogy data. Use `--purpose support` for an equivalently redacted
support record.

Profile switching never migrates, copies, uploads, restores, or deletes a tree.
Those data operations remain separately reviewed and confirmed. The current
encrypted local backup commands above remain the only implemented product
backup workflow.

[ADR-0026](ADR-0026-local-first-container-remote-deployment.md) accepts later
container and advanced remote runtimes. Their backup commands and runbooks are
not implemented or supported yet. Issue #349's Compose topology includes an
application-owned named volume only as a read-only persistence placeholder. It
does not initialize a database, run a migration, broker a key, back up, restore,
or write genealogy data. Those operations remain fail-closed until Issue #351
provides the separately reviewed encrypted-volume and recovery contract; the
local CLI workflow above remains authoritative in the meantime.

Before either profile can ship, its release evidence must demonstrate all of
the following:

- SQLCipher data volumes and key material use separate, least-privilege storage
  and backup paths.
- Compose files, images, environment manifests, process arguments, logs,
  container-inspection output, snapshots, and support artifacts contain no
  credentials, encryption keys, or genealogy payloads.
- A backup destination is independent of the live container host and writable
  data volume.
- Restore succeeds in a clean, target-matched container environment, and
  wrong, missing, rotated, or corrupt key material fails closed without
  publishing a partial restore.
- Interrupted backup, restore, upgrade, and rollback operations preserve the
  last known-good encrypted state.
- Uninstall offers explicit preserve-data and erase-data choices and never
  silently deletes the only recoverable copy.

For Local Desktop, the signed-in OS user owns backup scheduling, destination,
key custody, and restore validation while the supervisor owns only bounded
container lifecycle. For Host Remote, the named operator owns off-host backup
scheduling, restore drills, capacity planning, disaster recovery, and key
rotation. Host Remote is self-supported and has no project-operated backup,
recovery, retention, or availability SLA. Support bundles may contain only
structural, redacted diagnostics and never database contents, keys, tokens,
paths, or container-environment values.
