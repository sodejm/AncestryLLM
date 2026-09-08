# Inspect GEDCOM sources

The v0.7 source-level **GEDCOM** workspace provides read-only local intake and
an explicit root choice. It is not yet evidence of a supported packaged
genealogy workflow. It does not import, merge, export, change a source, select
a provider, or upload records. Use fictional fixtures when testing or sharing
evidence.

## Add and order sources

1. Wait for startup to report **Ready**, then open **GEDCOM** or press
   <kbd>G</kbd> from workspace navigation.
2. Choose **Add GEDCOM source** and select a GEDCOM file in the native chooser.
   Cancel if the requested file or purpose is unexpected. There is no typed
   path fallback.
3. Wait for its inspection. A summary shows the declared GEDCOM version,
   physical encoding, individual/family/other-record counts, byte size,
   SHA-256 source identity, and coded findings.
4. Add further sources as needed. At most eight slots are available, including
   pending or failed selections. Use **Move up** and **Move down** to set the
   order; source identity and root choices remain attached to their source.

Each source must fit the 512 MiB byte ceiling and the shared
[file-ingress limits](../reference/FILE_INGRESS.md). At most 100 findings are
displayed, with the full finding count shown separately. Inspection checks do
not certify genealogy accuracy, complete GEDCOM conformance, or lossless
conversion by another product. See the
[compatibility reference](../reference/GEDCOM_COMPATIBILITY.md) for accepted,
rejected, and preserved input classes.

## Review a finding

Findings without a person anchor are marked **Source-wide finding**. For an
anchored finding, activate **Review affected person** with the mouse or keyboard
to request a single bounded preview. The preview shows the affected person's
name, source identifier, birth/death dates, and relationship counts, not raw
GEDCOM lines or a complete record. Missing, ambiguous, or unavailable anchors
produce a stable error code instead of showing a guessed match.

Reviewing a finding does not select or clear a root, including an explicit
no-root choice. Person previews are temporary and are discarded with their
source; responses arriving after source removal are ignored.

## Choose a root deliberately

For each completed source, enter a name or source identifier in **Find a root**
and activate **Search candidates**. Queries are limited to 128 characters;
each UI page shows at most 25 candidates. **Next candidates** continues the
same query. An empty query starts from the first page.

Use the source identifier, birth/death dates, and relationship counts to
distinguish duplicate names. Candidate text is bounded and may be shortened;
it is not an authoritative identity match. Use native radio-button keyboard
controls to choose one candidate, or explicitly select **Continue without a
root**. The application never guesses a root. A selection remains visible
when searching, paging, or reordering sources; a failed query clears it.

This selection is preparation for a future operation only. It does not create
a rooted export or mutate records. All sources and choices are temporary, not
saved projects or encrypted workspace records.

## Remove a source or recover

**Remove** or leaving the workspace requests cancellation and disposal of the
inspection. Closing or replacing the document, invalidating the native
runtime, or closing the application revokes its Main-process authority.
Private staged bytes are normally removed when inspection reaches a terminal
state; results remain in process memory only until disposal or shutdown.

If a selection is oversized, invalid, replaced, or unavailable, remove its
slot and choose a stable source again. For a sidecar or disposal failure,
restart the application before retrying. Do not recover data from private
staging or bypass the native chooser. Original source files remain unchanged,
and no output needs to be salvaged.

Report only the stable error code and whether failure occurred during
selection, inspection, finding review, or root search. Do not attach real genealogy records,
search text, names, identifiers, fingerprints, raw parser lines, logs, or local
paths to a public issue.
