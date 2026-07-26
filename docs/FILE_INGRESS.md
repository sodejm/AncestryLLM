# Bounded file ingress

Every user-selected input is checked by one typed policy before decoding,
opening SQLite, contacting a provider, or creating an output. The same policy is
used by one-shot commands, the interactive console, and application services.
Failures use stable coded errors, exit with status `2`, disclose neither the
input path nor its contents, and leave existing outputs unchanged.

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
to the same stable nesting-limit failure.

Multi-pass GEDCOM synchronization and RootsMagic query/export bind every parse,
hash, database read, provider preflight, and copy to the identity first
verified for that operation. The identity includes device, inode, size,
modification time, and filesystem change time; a SHA-256 fingerprint is carried
through publication. Replacing a file with a same-size file, restoring its
modification time, or changing it between validation and a later pass is
therefore rejected.

## Configuration

Overrides belong only in the normal non-secret `config.toml` boundary. No
environment variable is consulted for file-ingress limits. Omitted fields keep
their defaults. Values must be positive integers.

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
cannot raise its own read budget.

## Safe failure behavior

The policy rejects missing or unreadable inputs, directories, symbolic links,
FIFOs, devices, known compressed/archive containers, invalid Unicode, invalid
JSON, and any exceeded byte, line, record, row, nesting, or collection limit.
It captures device, inode, size, modification time, and filesystem change time
before consumption and checks them again after the read, so replacement,
growth, truncation, or in-place modification fails as `FILE_INPUT_CHANGED`.

Every report destination is checked against immutable inputs and its primary
output by canonical path and filesystem identity. Symlink, hard-link, and
alternate-spelling aliases are rejected before publication. Related primary
and report artifacts are staged together; existing targets remain recoverable
until every rename and final source-fingerprint check succeeds. Any second
artifact or late validation failure restores both prior targets and removes
publication temporaries.

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
- `FILE_INPUT_EMPTY`
- `FILE_FORMAT_INVALID`
- `FILE_JSON_INVALID`
- `FILE_JSON_TYPE_INVALID`

Compressed and archive inputs are not expanded by AncestryLLM. Extract them
with a trusted local tool, inspect the resulting regular file, and pass that
file directly. Rejection occurs before provider selection is evaluated, so it
cannot make a network request even when provider credentials are present.
