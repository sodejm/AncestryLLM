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
4. Let the application publish transactionally. Do not move, edit, or replace
   the source or target during the operation.

Source/output aliases and concurrent output grants fail closed. A path-free
grant is only the desktop half of the boundary: the Python file-ingress adapter
must reopen and revalidate an input, and output is published atomically only
after the domain operation succeeds.

RootsMagic files remain immutable sources. GEDCOM reading and writing remains
loss-minimal; granting access does not authorize destructive normalization or
make a copied tree authoritative. Follow the
[bounded file-ingress reference](../reference/FILE_INGRESS.md) for byte,
record, race, and publication limits.

## Recover from a rejected selection

Cancel and select again when the interface reports `FILE_SELECTION_INVALID`,
`FILE_TOO_LARGE`, `FILE_GRANT_REVOKED`, `FILE_GRANT_STALE`, or
`FILE_GRANT_CONFLICT`. `FILE_GRANT_FORBIDDEN` means the caller, purpose, or
access does not match; do not work around it. `FILE_DIALOG_FAILED` means the
native chooser failed without returning a private path.

Native file choosers and path presentation differ across macOS, Windows, and
Linux. The security contract does not: Electron Main owns the chooser and path,
the renderer receives only sanitized display metadata, and no operating system
permits an ambient or user-typed path fallback.
