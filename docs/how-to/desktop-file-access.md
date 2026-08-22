# Grant desktop file access

The v0.6 desktop source mediates a supported product action through a native
file chooser and an opaque grant. It does not expose a standalone file-grant
workspace, and selecting a file does not itself import, merge, export, or open
genealogy data.

Use fictional files and placeholder locations such as
`<choose-a-fictional-file>`. Never publish a real path, family tree, database,
backup, report, log, prompt, response, or credential in documentation or an
issue.

## Select an input safely

1. Begin from a supported product action that requests a file.
2. Review its declared purpose before opening the native chooser:
   `gedcom-read`, `rootsmagic-read`, `gedcom-write`, `json-write`, or
   `markdown-write`.
3. For a read request, select only the expected fictional GEDCOM or RootsMagic
   input. Cancel if the requested purpose or format is unexpected.
4. Review the selected-file card. It may show only a sanitized basename,
   format, byte size, access intent, and validation state. It must not show the
   native path.

Electron Main validates that the selection is a regular file, is not a link,
matches the expected extension and content signature, stays within the size
budget, and has not raced or changed. The renderer receives an opaque random
grant identifier rather than a filesystem path.

## Understand the grant scope

Every grant is bound to all of these facts:

- the exact read or write purpose and file format;
- the requesting renderer and its **requesting-window** scope;
- the current **app-session**;
- **single-use** redemption by a trusted main-process adapter.

Closing or navigating the requesting document, revoking the grant, restarting
the application, changing the file, or attempting a second redemption makes
the grant invalid. A trusted same-document hash-route change can retain the
renderer identity, but it does not broaden the grant.

## Select an output safely

1. Start the supported save action and choose a new fictional destination.
2. If an existing output would be replaced, review the native replacement
   confirmation. A renderer checkbox cannot authorize replacement.
3. Confirm the selected card reports `new-output` or
   `replacement-confirmed` as appropriate.
4. Let the application validate every staged output, then publish each one by
   same-directory atomic replacement. Do not move, edit, or replace the source
   or target during the operation.

Source/output aliases and concurrent output grants fail closed. A path-free
grant is only the desktop half of the boundary: Electron Main's
mediated-operation broker reopens and revalidates the input, stages an
immutable private copy, and publishes output atomically only after every
declared result passes validation.

RootsMagic files remain immutable sources. GEDCOM reading and writing remains
loss-minimal; granting access does not authorize destructive normalization or
make a copied tree authoritative. Follow the
[bounded file-ingress reference](../reference/FILE_INGRESS.md) for byte,
record, race, and publication limits.

## Understand an allowlisted mediated operation

Selecting files creates grants; it does not start an operation. When a future
supported genealogy action submits an allowlisted mediated request, Main:

1. consumes the exact single-use input and output grants;
2. revalidates each source and copies it into an owner-only private operation
   directory without opening or modifying the original;
3. gives a trusted local adapter only private staged paths and an exact
   read-only-input/read-write-output mount plan, or gives a trusted remote
   adapter bounded single-use byte streams and no path;
4. reports only path-free phases and counts;
5. validates all declared outputs before publishing any destination; and
6. revokes grants and removes the exact transient operation directory on every
   terminal outcome.

Remote execution is a separate disclosure decision. File selection cannot
select a provider, activate a deployment profile, or create consent. The
existing exact profile, endpoint, purpose, data-class, living-person,
retention, and active-consent checks must complete before a future remote
adapter receives bytes. `provider=none` remains network-free.

Issue #352 supplies this source-level Main-process foundation but adds no new
renderer operation route. Until a concrete genealogy adapter and its
target-matched evidence are separately accepted, do not describe the
allowlisted operation names as supported desktop actions.

## Recover from a rejected selection

Cancel and select again when the interface reports `FILE_SELECTION_INVALID`,
`FILE_TOO_LARGE`, `FILE_GRANT_REVOKED`, `FILE_GRANT_STALE`, or
`FILE_GRANT_CONFLICT`. A cancelled mediated operation may report
`FILE_OPERATION_CANCELLED` internally and `CANCELLED` at its path-free
operation boundary. `FILE_GRANT_FORBIDDEN` means the caller, purpose, or access
does not match; do not work around it. `FILE_DIALOG_FAILED` means the native
chooser failed without returning a private path.

For `TIMED_OUT`, `GRANT_REJECTED`, `OUTPUT_INVALID`, `MOUNT_MISMATCH`, or
`CLEANUP_FAILED`, leave the original source and prior output in place, restart
the application if directed, and retry from a fresh selection. Never open,
publish, or attempt to salvage files from the private staging area. Startup
recovery removes only recognized operation directories; an unexpected entry
causes a fail-closed diagnostic so it can be investigated without broad
deletion. Support evidence must contain only the stable code and operation
phase, never a source, destination, staging, or mount path.

Native file choosers and path presentation differ across macOS, Windows, and
Linux. The security contract does not: Electron Main owns the chooser and path,
the renderer receives only sanitized display metadata, and no operating system
permits an ambient or user-typed path fallback.
