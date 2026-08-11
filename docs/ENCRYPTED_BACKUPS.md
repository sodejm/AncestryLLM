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

## Accepted future container and remote profiles

[ADR-0026](ADR-0026-local-first-container-remote-deployment.md) accepts future
Local Desktop and advanced remote profiles. Their backup commands and runbooks
are not implemented or supported yet; the current commands above remain the
only documented product workflow.

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
