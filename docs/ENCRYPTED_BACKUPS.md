# Encrypted backup and recovery

Run `ancestry database backup DESTINATION`. The destination must not exist and
is written with restrictive permissions through SQLCipher's online backup API.
The backup uses the same key reference held in the OS keyring; copying only the
database without securely backing up that key is not a recovery strategy.

Keep database and key backups separate. Test recovery on an offline machine by
restoring the key into an OS credential store, opening a copy, checking the
schema revision, and running integrity checks. Plain SQLite files and wrong keys
are rejected. Never commit a database or backup to Git.
