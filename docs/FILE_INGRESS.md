# Bounded file ingress

Every user-selected input is checked by one typed policy before decoding,
opening SQLite, invoking a provider over the network, or creating an output.
The same policy is used by one-shot commands, the interactive console, and
application services. Failures use stable coded errors, exit with status `2`,
disclose neither the input path nor its contents, and leave existing outputs
unchanged.

## Default limits

All sizes are source bytes, not decoded character counts. A physical-line limit
includes its newline bytes. `records` means level-zero records for GEDCOM, the
aggregate rows across all inspected RootsMagic tables (with the same limit
also applied to each table), and physical lines for other text formats.
`items` is the aggregate number of JSON/TOML collection members, or lines in
one GEDCOM logical record.

| Input class | Total bytes | Physical line bytes | Records/rows | Logical record bytes | Nesting | Collection items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `config` | 1,048,576 | 65,536 | 20,000 | — | 16 | 20,000 |
| `gedcom` | 536,870,912 | 1,048,576 | 5,000,000 | 16,777,216 | 99 | 250,000 |
| `rootsmagic` | 8,589,934,592 | — | 5,000,000 aggregate and per table | — | — | 50,000 |
| `ocr` | 5,000,000 | 1,048,576 | 100,000 | — | — | — |
| `manifest` | 33,554,432 | 1,048,576 | 500,000 | — | 64 | 2,000,000 |
| `json_schema` | 2,097,152 | 262,144 | 50,000 | — | 64 | 100,000 |
| `prompt_body` | 1,048,576 | 262,144 | 50,000 | — | — | — |

GEDCOM remains a streaming input: only one logical record is accumulated by
the parser at a time. RootsMagic is opened read-only only after its regular-file
and byte checks pass; table reads stop at the configured row limit plus one.
JSON and TOML documents are byte- and line-bounded before parsing, then checked
for nesting and collection size. JSON containers and TOML arrays/inline tables
are also scanned before recursive parser work, and parser recursion is mapped
to the same stable nesting-limit failure. GEDCOM accepts UTF-8 and BOM-declared
UTF-16; config, manifest, schema, OCR, and prompt-body text accept UTF-8 only.
An accepted byte-order mark is counted in the total input budget and in the
first physical-line and logical-record budgets; it is never a free prefix
outside the configured limits.

Multi-pass GEDCOM synchronization and RootsMagic query/export bind every parse,
hash, database read, provider preflight, and copy to the identity first verified
for that operation. The identity includes device, inode, size, modification
time, and filesystem change time; a SHA-256 fingerprint is carried through
publication. Replacing a file with a same-size file, restoring its modification
time, or changing it between validation and a later pass is therefore rejected.
The captured identity also binds the filesystem link count. RootsMagic inputs
must have exactly one link because a hard-link alias could hide SQLite
transaction sidecars under a different pathname; create a standalone stable
backup instead of querying an aliased database.
Each configured RootsMagic directory is also bound to its filesystem identity
when the service is created. Source opens and sidecar checks traverse from that
verified directory capability, rather than reopening an unchecked absolute
pathname. Replacing a parent with a symbolic link, junction, or different
directory therefore fails as `FILE_INPUT_CHANGED` before database bytes,
schema, or rows can reach SQLite, an export, or a provider.
RootsMagic operations fail closed when any SQLite `-wal`, `-shm`, or rollback
`-journal` sidecar path exists, including an empty, stale, or symbolic-link
sidecar, instead of reading or copying an active transaction or risking mixed
database generations. Close RootsMagic cleanly and retry only after all three
sidecar paths are gone. Preflight reports `ROOTSMAGIC_WAL_ACTIVE`; a sidecar
that appears after preflight, snapshot copy, or a provider call reports
`FILE_INPUT_CHANGED`.

## Configuration

Overrides belong only in the normal non-secret `config.toml` boundary. No
environment variable is consulted for file-ingress limits. Omitted fields keep
their defaults. Values must be positive integers; physical-line limits must
also fit the platform's safe streaming-read size. Unknown sections, keys, and
wrong semantic types are rejected before configuration creates or changes any
directory.

```toml
[file_ingress.gedcom]
max_bytes = 268435456
max_line_bytes = 524288
max_records = 2500000
max_record_bytes = 8388608
max_nesting = 99
max_collection_items = 125000

[file_ingress.rootsmagic]
max_bytes = 4294967296
max_records = 1000000
max_collection_items = 25000

[file_ingress.prompt_body]
max_bytes = 524288
max_line_bytes = 131072
max_records = 25000
```

The configuration file itself always uses the compiled `config` defaults. It
cannot raise its own read budget. An omitted default config may be absent during
first-run initialization; a path supplied with `--config` must already be a
valid bounded regular file and is never created implicitly. Environment
fallback paths are resolved only when the selected configuration does not
override them, and an explicit `--config` never consults the default
configuration-directory fallback.

## Safe failure behavior

The policy rejects missing or unreadable inputs, directories, symbolic links,
FIFOs, devices, filesystems that do not expose a positive inode identifier,
known compressed/archive containers, invalid Unicode, invalid JSON, and any
exceeded byte, line, record, row, nesting, or collection limit.
User path expansion, absolute-path conversion, and canonical resolution pass
through the same sanitized boundary; a failed user lookup or path resolution
reports `FILE_INPUT_UNREADABLE` without disclosing the supplied spelling.
RootsMagic export and report destinations use that boundary too, so application
services, one-shot commands, and background console jobs preserve the same
typed error instead of leaking a runtime path-expansion failure.
NUL characters are rejected both as literal input bytes and after JSON or TOML
escape decoding, including in mapping keys.
It captures device, inode, size, modification time, and filesystem change time
before consumption and checks them again after the read, so replacement,
growth, truncation, or in-place modification fails as `FILE_INPUT_CHANGED`.
The inode identifier must be positive; a device identifier of zero remains
supported because Windows may legitimately report it. A filesystem without a
reliable inode fails as `FILE_INPUT_UNREADABLE` before any file bytes are
consumed.

Every report destination is checked against immutable inputs and its primary
output by canonical path and filesystem identity. Symlink, hard-link, and
alternate-spelling aliases are rejected before publication. Casefold- or
Unicode-normalization-equivalent destination names are conservatively rejected
on every platform so the same invocation remains safe when moved to a
case-insensitive volume. Related primary and report artifacts are staged
together; existing targets remain recoverable until every rename and final
source-fingerprint check succeeds. Any second artifact or late validation
failure restores both prior targets and removes publication temporaries.
Publication is a rollback-capable logical transaction, not a promise of
continuous old-or-new visibility to concurrent readers. On platforms without
a portable multi-file exchange primitive, replacing an existing target can
briefly make that target absent while its recoverable backup is installed or
restored. A successful return means every published artifact has been
reverified and the destination directories have been flushed where the
platform exposes that durability operation.
Windows does not expose a supported equivalent for flushing a directory
handle after a namespace rename. Candidate file contents are flushed before
publication, but crash-durability of the final Windows directory entry remains
subject to the filesystem and operating system.

Stable file-ingress codes are:

- `FILE_INPUT_UNREADABLE`
- `FILE_INPUT_NOT_REGULAR`
- `FILE_ARCHIVE_UNSUPPORTED`
- `FILE_INPUT_TOO_LARGE`
- `FILE_LINE_TOO_LONG`
- `FILE_RECORD_LIMIT_EXCEEDED`
- `FILE_RECORD_TOO_LARGE`
- `FILE_NESTING_LIMIT_EXCEEDED`
- `FILE_COLLECTION_LIMIT_EXCEEDED`
- `FILE_INPUT_CHANGED`
- `FILE_INPUT_IO`
- `FILE_ENCODING_INVALID`
- `FILE_NUL_BYTE_UNSUPPORTED`
- `FILE_INPUT_EMPTY`
- `FILE_FORMAT_INVALID`
- `FILE_JSON_INVALID`
- `FILE_JSON_TYPE_INVALID`

Compressed and archive inputs are not expanded by AncestryLLM. Extract them
with a trusted local tool, inspect the resulting regular file, and pass that
file directly. Rejection occurs before any provider network invocation, so it
cannot make a network request even when provider credentials are present.
