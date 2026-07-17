# Fictional incremental GEDCOM fixtures

Every person, place, archive, citation, identifier, note, and media reference in
this directory is fictional. The GEDCOM files are intended only for automated
tests and offline demonstrations. Media paths are references; no real or
fixture media files are required.

## Inventory and update order

1. `baseline-master.ged` is the protected master used to initialize a manifest.
2. `ancestry-snapshot-v1.ged` is source ID `ancestry-main`, vendor `ancestry`,
   exported `2025-01-15`.
3. `myheritage-snapshot-v1.ged` is source ID `myheritage-main`, vendor
   `myheritage`, exported `2025-02-03`.
4. `ancestry-snapshot-v2.ged` replaces only `ancestry-main`, exported
   `2026-06-20`.
5. `myheritage-snapshot-v2.ged` replaces only `myheritage-main`, exported
   `2026-06-25`.

The exact quality/export root is `Elara Northwood`. Her protected master pointer
is `@I100@` and stable fictional identifier is `NM-ELARA-001`.

## Expected identity and xref outcomes

- Elara, Rowan, and Mira retain master pointers `@I100@`, `@I101@`, and
  `@I102@` despite every snapshot using different xrefs.
- The `_UID` values provide explicit retained identifiers. Identity still must
  be validated against names, dates, and relationships before an old binding
  is trusted.
- Ancestry v2 adds Ilyan Shore, Sable Shore, and their family. They receive new
  deterministic master-controlled pointers; snapshot pointers `@AX-15@`,
  `@AX-16@`, and `@AX-FAM-2@` must not become authoritative master pointers.

## Expected fact and citation outcomes

- Elara's exact `12 MAR 1970` birth is one canonical fact. Country values
  `United States`, `USA`, and `United States of America`, case differences in
  `Mar`, leading-zero differences, and harmless whitespace formatting are safe
  semantic equivalents.
- The canonical birth retains distinct citations from the Lakehaven Civil
  Registry and Northwood Family Bible.
- Repeated Civil Registry page `Birth ledger, p. 14` and Family Bible page
  `Family Bible, leaf 3` citations collapse exactly once per canonical source.
- `Birth index image 42` and `Family Bible, leaf 4` remain separate citations
  because `PAGE` differs.
- Ancestry v2's month-only `MAR 1970` birth remains a separate conflicting fact;
  it must not collapse into the day-precision birth.
- MyHeritage's `Lakehaven, Vermont, USA` residence and Ancestry's
  `Lakehaven, USA` residence remain separate because a jurisdiction component
  is omitted. Reordering or omitting place components is not safe equivalence.
- `School librarian` occurs only as an uncited Ancestry v1 fact. It is omitted
  by Ancestry v2 and is eligible for removal after that source observation is
  retired.
- The cited `1 JUN 1998` Cedar Bay residence is also omitted by Ancestry v2,
  but it must be retained because its City Directory citation cannot be
  discarded.
- `Lakehaven College` is observed in Ancestry v1 and both MyHeritage snapshots.
  Its Ancestry observation disappears in v2, but the fact remains active due to
  the MyHeritage origin.

## Expected source, structure, and attachment outcomes

- `@S100@`, `@A-SRC-77@`, `@AZ-SRC-2@`, `@MH-SRC-CIVIL@`, and
  `@M2-SRC-CIVIL@` describe the same complete level-zero Civil Registry source
  under different xrefs. They consolidate to the protected master source
  `@S100@`; all citations are remapped and all aliases remain in the manifest.
- The corresponding repository records also have identical semantic content
  under changed xrefs and should consolidate to protected `@R100@`.
- The distinct Northwood Family Bible and Cedar Bay City Directory sources
  remain separate.
- Snapshot-specific `_APID`, `_MHID`, `_TREE`, `_ORIGIN`, and
  `_FAMILY_FIXTURE` structures exercise conservative custom-tag handling.
  Only exact normalized duplicate custom subtrees may collapse.
- Shared and inline note links plus level-zero `NOTE` and `OBJE` records exercise
  pointer remapping, snapshot replacement, and attachment preservation.
- The `HEAD`, one terminal `TRLR`, hierarchy, and all in-file pointers are
  intentionally line-parseable GEDCOM 5.5.5 structures.

## Idempotency and privacy

Applying an already-active snapshot checksum should be a no-op. Website source
IDs, export dates, xref aliases, and observation history belong only in the
private synchronization manifest; the updater must not invent GEDCOM citations
from this fixture metadata.
