# Browse and export a RootsMagic source

The v0.7 source-level **RootsMagic** workspace uses the native local sidecar.
Container and external connections do not receive file authority for this
workflow. Supported packaged status requires target-matched acceptance evidence;
source tests alone do not establish that status.

Select a `.rmtree` file through the native chooser. The workspace shows its
friendly filename, fingerprint, detected version, and grant status. A version
of **unknown** means the file did not supply reliable version metadata. Schema
capabilities, rather than a guessed RootsMagic version, determine which queries
can run. Inspection, queries, and exports appear in Task Center.
Sources up to 8 GiB are accepted. If discarding a source fails, its access
remains visible so you can retry.

Choose **People** to browse names or apply a literal name filter. Select a person
before using **Family links** or **Events**. Pages contain at most 100 rows;
names and event fields are plain text. A missing total count is intentional:
the workbench does not perform an unbounded count merely to display pagination.
It accepts fixed presets, not SQL or arbitrary record requests.
IDs above 9,007,199,254,740,991 remain visible as exact decimal text but cannot
be selected for related queries or exports in the desktop workbench.

An export requires a selected root person. Review the connected, ancestors, or
descendants scope and any generation limit before exporting. Defaults are
portable GEDCOM 5.5.5, generic destination, connected scope, and living people
excluded. The living-person setting can instead include or anonymize them.
Anonymize applies the exporter's living-person redaction policy.
The immutable-source notice applies to every operation: the original database
and validated SQLite companions are checked without modifying them.

Choose a destination for **one new folder**. Existing destinations are refused.
The completed folder contains:

- `tree.ged`: the rooted GEDCOM export.
- `report.md`: conversion losses and provenance with friendly source metadata.
- `manifest.json`: source identity, export selections, and SHA-256 digests and
  byte sizes of the GEDCOM and report.

The complete staged folder is validated and published by an exclusive rename
on the same filesystem. Cancellation before publication produces no export
folder. Once publication succeeds, Task Center reports the committed result
even if cancellation arrives concurrently. Reveal opens the authorized output
location through Main; the renderer does not receive a host path.
After a failed or cancelled export, choose a new destination before retrying;
the previous destination grant has been consumed.

Discard a source when finished. Discard, window closure, and sidecar restart
revoke its session; select the file again to authorize later access. Changed
source bytes require a new selection. Recovery also requires a fresh authorized
destination and never revives an old desktop grant. Preserve files and the
coordination journal if a recovery error occurs; see
[mutation recovery](../reference/MUTATION_RECOVERY.md).

Exports contain genealogy data and are not encrypted backups. Store and share
them according to your privacy requirements, and retain the original source
and independent backups. See [backup procedures](../ENCRYPTED_BACKUPS.md).
