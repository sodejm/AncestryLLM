# GEDCOM Merge Quickstart

This walkthrough merges the repository's small GEDCOM fixtures, creates a
rooted tree for Maren Hollow, writes a Markdown quality report, and proves that
malformed input cannot produce a partial GEDCOM. Genealogy processing is
offline and deterministic: every merge command uses `--ai-backend none`.

For the complete CLI, identity-scoring, provider, privacy, and importer notes,
see the [GEDCOM Merge Tool reference](README.md).

## Run the fixture demo

From any directory:

```bash
/path/to/AncestryLLM/scripts/gedcom_merge_quickstart.sh
```

The script locates the repository from its own path, so it does not depend on
the current working directory. It creates a private, timestamped directory
under `${TMPDIR:-/tmp}` and prints the exact path when it finishes. No input is
modified and no API key is read. On the first run, `pip` may contact its
configured package index to install `requirements.txt`; use `--skip-install`
after dependencies are available when the host must remain network-isolated.

Useful options:

```bash
# Put the timestamped run directory beneath a chosen parent directory.
./scripts/gedcom_merge_quickstart.sh --output-dir ./demo-results

# Use the current python3 directly instead of creating an isolated environment.
./scripts/gedcom_merge_quickstart.sh --skip-install

./scripts/gedcom_merge_quickstart.sh --help
```

The demo reads only `quality-source-a.ged`, `quality-source-b.ged`, and
`malformed-rejected.ged` under `tests/fixtures/gedcom_merge`.

The successful run uses Maren Hollow as `--root-person`. It produces:

- `maren-hollow.ged`, containing Maren's connected family tree; and
- `maren-hollow.quality.md`, the default quality report for that output stem.

The second run intentionally includes malformed input. Success means the CLI
returns nonzero, writes `malformed.quality.md` with diagnostics, and does not
write `malformed.ged`. The shell script treats any other result as a failed
demo.

## Merge your own files offline

Python 3.11 or newer is required for this tool. From the repository root:

```bash
python3 tools/gedcom_merge.py /path/to/tree-a.ged /path/to/tree-b.ged \
  --ai-backend none \
  --auto \
  --quality-root-person 'Maren Hollow' \
  --output ./master.ged
```

This keeps the full merged GEDCOM and roots only the quality analysis. The
default report is `./master.quality.md`. A name must resolve uniquely; use a
GEDCOM pointer such as `@I42@` when duplicate names exist.

To export only one connected family tree and use the same person as the quality
root:

```bash
python3 tools/gedcom_merge.py /path/to/tree-a.ged /path/to/tree-b.ged \
  --ai-backend none \
  --auto \
  --root-person '@I42@' \
  --output ./rooted-master.ged
```

To choose another report path or intentionally omit the report:

```bash
python3 tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto \
  --quality-root-person '@I42@' \
  --quality-report ./tree-review.md \
  --output ./master.ged

python3 tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto \
  --no-quality-report \
  --output ./master-without-report.ged
```

When quality reporting is enabled, `--quality-root-person` or `--root-person`
is required. The report lists potential duplicates at score 90 or higher and
makes conservative maiden/married-name review recommendations; recommendations
do not rewrite names in the GEDCOM.

## Keep a master current with website exports

The incremental workflow is for repeated updates after a master GEDCOM has
already been generated. It defaults to `--ai-backend none`, so API keys in
`.env` do not cause network requests. Keep every release directory: the master
and manifest inside it are a checksum-matched pair.

### First incremental release

Initialize a manifest for an existing master and import the first tracked
Ancestry snapshot:

```bash
python3 tools/gedcom_merge.py update \
  --master ./master.ged \
  --initialize-manifest \
  --snapshot ancestry-main:ancestry=./exports/ancestry-2026-07.ged \
  --exported-at ancestry-main=2026-07-17 \
  --quality-root-person '@I42@' \
  --release-root ./releases
```

The source ID `ancestry-main` must remain stable for later replacements of
that export stream. The `ancestry` portion is vendor metadata. Existing master
content becomes protected baseline data; it is not attributed retroactively to
the website snapshot. The general argument form is
`--snapshot SOURCE_ID:VENDOR=PATH`.

### Add or replace Ancestry, Geni, and MyHeritage snapshots

For the next generation, use the master and manifest from the preceding
release. A supplied source ID replaces only its own previous snapshot. A
source ID omitted from the command remains active:

```bash
python3 tools/gedcom_merge.py update \
  --master ./releases/g0001-20260717T153000Z/master.ged \
  --manifest ./releases/g0001-20260717T153000Z/manifest.json \
  --snapshot ancestry-main:ancestry=./exports/ancestry-2026-08.ged \
  --snapshot geni-main:geni=./exports/geni-2026-08.ged \
  --snapshot myheritage-main:myheritage=./exports/myheritage-2026-08.ged \
  --exported-at ancestry-main=2026-08-12 \
  --exported-at geni-main=2026-08-11 \
  --exported-at myheritage-main=2026-08-10 \
  --quality-root-person '@I42@' \
  --release-root ./releases
```

Preview the same reconciliation without writing a generation by adding
`--dry-run`. If ancestry analysis is not wanted, replace
`--quality-root-person '@I42@'` with `--no-quality-report`.

A changed run atomically creates a directory named like
`g0002-20260812T153000Z/` containing:

- `master.ged`: the new portable master;
- `manifest.json`: private synchronization state and provenance;
- `update.md`: added, mapped, consolidated, conflicting, removed, retained,
  and unresolved details;
- `quality.md`: advisory rooted tree findings; and
- `rollback.json`: the exact parent release, checksums, and restoration steps.

An already-active snapshot checksum is a successful no-op and creates no new
release. Existing generations are immutable and are never overwritten.

### What happens to repeated details and sources

Safe semantic duplicates become one GEDCOM detail, not one copy per website.
If Ancestry and MyHeritage both provide the same birth event, the master keeps
one birth event and attaches every distinct compatible GEDCOM citation to it.
Exact duplicate citations collapse, while different source pages, quoted text,
event roles, quality values, notes, and media remain. Identical whole-source
records may share one canonical xref after citations are remapped; their prior
xrefs and website observations remain recoverable in the private manifest.

Normalization never hides meaningful differences. Approximate and exact
dates, different date precision, changed place hierarchy, differently typed
names, conflicting citation fields, and nonidentical custom structures remain
separate for review.

When a replacement export no longer contains a detail, only that snapshot's
observation disappears. The tool may remove an uncited event or attribute only
when it has no baseline, manual, or other active origin. People, names, sex,
relationships, family records, cited facts, and source records are never
automatically deleted. `update.md` clearly distinguishes actual removals from
information retained despite disappearing from the newest export.

### Rebase a master edited outside the tool

An ordinary update refuses a master whose checksum no longer matches its
manifest. This prevents an external edit from being mistaken for website data.
Review and record the edit explicitly:

```bash
python3 tools/gedcom_merge.py rebase \
  --master ./edited-master.ged \
  --manifest ./releases/g0002-20260812T153000Z/manifest.json \
  --quality-root-person '@I42@' \
  --release-root ./releases \
  --dry-run
```

Remove `--dry-run` after reviewing the comparison. Rebase additions and
changes become protected manual provenance. If the external edit deleted
protected content, the real rebase requires `--accept-manual-deletions` as an
explicit confirmation; never add that option before reviewing the deletion
list.

Every failure explains **What happened**, **Why it matters**, and **How to fix
it** in plain English, includes relevant paths, source IDs, pointers, and
expected/actual values when safe, and confirms that no release files changed.
When possible, inspect the timestamped failure report under `--release-root`.
Do not replace the prior release while resolving an error.

To roll back, follow `rollback.json` and select its checksum-verified parent
master and manifest as the inputs to the next operation. Rollback does not
modify or delete any generation.

## Optional AI review

`--quality-ai` is opt-in. It reuses the backend and provider settings already
selected for merge adjudication and reviews at most the top 25 quality
findings. It does not have a separate model or backend configuration.

```bash
python3 tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend ollama \
  --quality-ai \
  --quality-root-person '@I42@' \
  --auto \
  --output ./master.ged
```

`--ai-backend none` is the strongest offline guarantee. A local Ollama URL
keeps model processing local, while OpenAI, Gemini, OpenRouter, or a remote
Ollama URL can receive person summaries. The Markdown report also contains
genealogy data; store and share it with the same care as the GEDCOM.

## Importer fallback

GEDCOM 5.5.5 is the default. If a destination rejects it, retry with:

```bash
python3 tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto \
  --quality-root-person '@I42@' \
  --gedcom-version 5.5.1 \
  --output ./master-5.5.1.ged
```

This produces output declaring version 5.5.1 and applies the writer's supported
compatibility behavior. It is not a complete dialect conversion or a
conformance certificate. Always import a copy, inspect representative people,
families, names, facts, and sources, and retain the original GEDCOM files.
