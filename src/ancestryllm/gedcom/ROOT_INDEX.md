# Private GEDCOM root index

The root index is a bounded SQLite projection used to select a person before a
GEDCOM workflow starts. It stores only the seven fields already present in
`RootCandidate`: `person_ref`, `reason_code`, `display_name`,
`source_identifier`, `birth_date`, `death_date`, and `relationship_summary`.
The source identifier is the GEDCOM cross-reference identifier (xref).

The index does not contain GEDCOM records, family graphs, provider payloads, or
prompt and response content. Callers remain responsible for the lifecycle of
the source GEDCOM and for deleting or rebuilding an index when it is no longer
needed.

## API

`build_root_index(path, candidates, *, max_bytes, cancellation=None)` consumes
an iterable of `RootCandidate` values and returns the number written. It never
replaces an existing destination. A repeated `person_ref` fails the build.

`root_index_count(path)` validates the index and returns its recorded candidate
count. `read_root_page(path, *, query="", offset=0, limit=50)` returns a frozen
`RootIndexPage` containing a tuple of candidates, an optional known total, and
an optional resume offset. Limits must be from 1 through 100, and offsets must
be non-negative integers.

Public failures use `RootIndexError`. Its messages describe the failure class
without including the database path, a candidate value, or a query. The chained
exception may retain internal diagnostic detail, so adapters must not expose
tracebacks or chained exceptions to users.

## Private, bounded publication

The destination's existing immediate parent directory must be a directory with
no group or other permission bits. The staged and published database files use
mode `0600`. The builder creates a uniquely named database in that same private
directory, configures a 4 KiB page size, DELETE journaling, full synchronous
writes, file-backed temporary storage, and an untrusted schema, then streams the
input in batches of 128 candidates. It never materializes the complete input.

The byte budget includes the staged database and any sibling `-journal`, `-wal`,
and `-shm` files. The builder checks the combined size around every bounded
commit. Memory used by caller-owned candidates and SQLite's internal process
state is outside that on-disk budget.

After the final batch, the builder records the candidate count and marks the
schema complete in one committed database. It closes and syncs that file, then
uses a same-directory hard link to publish without overwriting an existing
path. This requires a filesystem that supports hard links. On platforms that
expose directory descriptors, publication also syncs the directory. Any staged
database, journal, WAL, or shared-memory file is removed after success,
cancellation, or failure.

Cancellation is checked while consuming the iterable, before completion, and
immediately before publication. A cancellation or byte-budget failure leaves no
published partial index. Once publication succeeds, the complete destination
is usable even if a later directory-sync failure is reported.

## Validation and reads

Readers open SQLite with `mode=ro`, `immutable=1`, query-only mode, and an
untrusted schema. Before returning data they require a regular `0600` file in a
private immediate parent, a successful SQLite quick check, the exact table and
column schema, both required lookup indexes, the supported schema version, a
complete marker, and agreement between the recorded and physical row counts.
Validation fails closed for a corrupt, partial, modified, or unsupported index.

Empty-query pages use this deterministic order:

1. `display_name` with SQLite `NOCASE` collation
2. `display_name` with its binary tie-breaker
3. `source_identifier`
4. `person_ref`

An exact `person_ref` or `source_identifier` query uses indexed equality and
returns only exact matches. Its total is known. An empty query also has a known
total. All values are bound as SQLite parameters.

When there is no exact identifier match, substring search compares the literal
case-folded query with all seven projected fields in Python. SQL wildcard
characters such as `%` and `_` therefore have no special meaning. A call scans
at most 4,096 deterministically ordered rows and stops earlier when the requested
number of matches is found.

For substring searches, `next_offset` is the position after the last scanned
row, not the number of matches. A page with no matches can still return a resume
offset. Passing that offset continues the bounded scan without repeating rows.
`total_count` is `None` until a scan that began at offset zero reaches the end;
later resumed calls keep it unknown. `next_offset` is `None` only after the end
of the ordered index is reached.

The immutable read URI relies on the publication contract: published index
files are never modified in place. Rebuilds must use a new destination after the
old lifecycle owner has removed or retired the previous index.
