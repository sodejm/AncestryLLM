# GEDCOM Merge Tool

`gedcom_merge.py` combines two or more GEDCOM files into one loss-minimizing
master tree. It standardizes dates, preserves conflicting facts and custom
tags, resolves cross-file pointers, optionally exports only the connected tree
around a root person, and can use local or remote AI to adjudicate uncertain
duplicate people.

AI adjudicates uncertain identity and may suggest which source value should be
the canonical summary. It cannot delete a conflicting fact: both source fact
blocks remain in the merged GEDCOM.

For a short, reproducible walkthrough, see the
[GEDCOM Merge Quickstart](GEDCOM_MERGE_QUICKSTART.md) or run the
[offline demo](../scripts/gedcom_merge_quickstart.sh).

## Supported interface and API status

- `gedcom_merge.py` is the supported command-line entry point. The documented
  command-line options, output files, and exit behavior are its user-facing
  contract.
- The module's Python functions and classes are implementation details used by
  this repository and its tests, not a versioned public library API. Even
  non-underscore names may change as GEDCOM importer behavior evolves.
- `bootstrap.py`, `gemini_transcription.py`, and `sql_router.py` support other
  project workflows; they are not part of GEDCOM merging.
- Keep this README, CLI help, implementation contracts, and behavior tests in
  the same change when the command-line contract changes.

## Quick start

Run these commands from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For a completely offline merge with no AI calls:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto --root-person 'Jane Smith' -o master.ged
```

This writes `master.ged` and, by default, `master.quality.md`. A quality report
requires either `--quality-root-person` or `--root-person`; use
`--no-quality-report` when you intentionally want only a GEDCOM and do not have
a report root.

For local AI through Ollama, which is the default and does not need an API key:

```bash
ollama pull llama3.1
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend ollama --auto --no-quality-report -o master.ged
```

Use `python tools/gedcom_merge.py --help` for every option.

## Offline incremental master updates

Use the `update` workflow when a generated master GEDCOM must be refreshed
from newer exports from Ancestry, Geni, MyHeritage, or another website. Unlike
the basic merge command, this workflow carries a private manifest forward so
the tool can distinguish a replacement snapshot from a new source of data.
The prior master remains authoritative, keeps its existing xrefs, and is never
overwritten.

Incremental processing defaults to `--ai-backend none`. It is therefore fully
offline and deterministic unless AI is explicitly enabled. API keys present in
the environment do not enable remote processing by themselves.

### Initialize synchronization history

An existing master without a manifest can enter the workflow once with
`--initialize-manifest`. All existing master content is then protected as
baseline data with unknown historical origin:

```bash
python tools/gedcom_merge.py update \
  --master /trees/master.ged \
  --initialize-manifest \
  --snapshot ancestry-main:ancestry=/exports/ancestry-2026-07.ged \
  --exported-at ancestry-main=2026-07-17 \
  --quality-root-person '@I1@' \
  --release-root /trees/releases
```

`--snapshot SOURCE_ID:VENDOR=PATH` requires a stable source ID chosen by you.
The source ID identifies a continuing export stream, while the vendor records
where the snapshot came from. For example, `ancestry-main` and
`ancestry-cousins` are two independent streams from the same `ancestry`
vendor. A source ID cannot appear twice in one command. `--exported-at
SOURCE_ID=DATE` associates the export date with that source ID.

### Apply later website snapshots

Pass the previous release's master and matching manifest, then supply only the
source streams being replaced. Omitted source IDs remain active:

```bash
python tools/gedcom_merge.py update \
  --master /trees/releases/g0001-20260717T153000Z/master.ged \
  --manifest /trees/releases/g0001-20260717T153000Z/manifest.json \
  --snapshot ancestry-main:ancestry=/exports/ancestry-2026-08.ged \
  --snapshot geni-main:geni=/exports/geni-2026-08.ged \
  --snapshot myheritage-main:myheritage=/exports/myheritage-2026-08.ged \
  --exported-at ancestry-main=2026-08-12 \
  --exported-at geni-main=2026-08-11 \
  --exported-at myheritage-main=2026-08-10 \
  --quality-root-person '@I1@' \
  --release-root /trees/releases
```

Use `--dry-run` to perform the full comparison without creating a release.
Use `--no-quality-report` when a rooted quality report is intentionally not
wanted; otherwise provide `--quality-root-person`. Supplying a snapshot whose
checksum is already active is an idempotent success and creates no redundant
generation.

### Releases, manifest privacy, and rollback

Every changed update is staged and published as one atomic, immutable
generation directory such as `g0003-20260717T153000Z/`. It contains
`master.ged`, `manifest.json`, `update.md`, `quality.md`, and `rollback.json`.
If validation or publication fails, no partial generation replaces or modifies
an existing release.

The manifest records the tree and generation IDs, parent and artifact
checksums, active and historical snapshots, source and person xref aliases,
canonical block hashes, observations, protected baseline/manual content, and
removals. Treat it as private genealogy data. Website provenance stays in the
manifest and is never converted into a synthetic GEDCOM source citation.
Standard GEDCOM `SOUR` records and fact-specific citations remain genealogical
evidence and are preserved independently.

`rollback.json` names the exact parent master and manifest, includes their
checksums, and gives restoration instructions. Rollback means selecting that
immutable parent release for the next operation; it never rewrites release
history.

### Safe semantic deduplication and removal

Incoming people are matched using validated prior bindings, unique retained
identifiers, exact canonical identity fingerprints, and finally conflict-free
deterministic identity evidence at a score of at least 95. A reused website
xref is not trusted when the person's identity evidence conflicts. Ambiguous
records remain separate and are reported for review.

Equivalent names, facts, family structures, links, notes, media references,
and custom structures are deduplicated conservatively. Normalization covers
Unicode and whitespace, safe controlled values, canonical pointers, unchanged
date precision and qualifiers, and country aliases. It does not equate an
approximate date with an exact date, a year with a full date, reordered or
omitted place jurisdictions, `St` with `Saint`, or differently typed names.
Unknown and custom tags are deduplicated only when their normalized subtrees
are exactly identical.

When several snapshots contain the same fact, the output contains one fact
instance with the union of its compatible citations. Exact duplicate citations
collapse; distinct pages, transcriptions, event roles, quality values, notes,
and media remain. Compatible citation details may be combined only when their
single-value fields do not conflict. Identical whole-source records can share
one canonical xref after all citations are remapped, while source aliases stay
recoverable in the manifest. No source evidence is discarded merely because
two providers supplied the same detail.

Replacing a snapshot removes that snapshot's observation from details it no
longer contains. The tool may automatically remove only an uncited individual
event or attribute that has no baseline, manual, or other active source
observation. It never automatically removes people, names, sex, relationships,
families, cited facts, or source records. The update report separates facts
actually removed from facts missing in the latest snapshot but retained for
safety.

### External edits and explicit rebase

The master and manifest are a checksum-matched pair. If the master was edited
outside this workflow, `update` stops rather than guessing how to reconcile
the untracked changes. Preserve the prior release and explicitly rebase the
edited master:

```bash
python tools/gedcom_merge.py rebase \
  --master /trees/edited-master.ged \
  --manifest /trees/releases/g0003-20260717T153000Z/manifest.json \
  --quality-root-person '@I1@' \
  --release-root /trees/releases
```

Rebase additions and changes become protected manual provenance. If the edit
also deletes protected content, rebase refuses it unless
`--accept-manual-deletions` is explicitly supplied after reviewing the change.
Use `--dry-run` first when validating an external edit.

### Transparent failures

Incremental failures identify a stable error code and explain, in plain
English, **What happened**, **Why it matters**, and **How to fix it**. Where
relevant, diagnostics include the source ID, file path, GEDCOM pointer, and
expected and actual values. They also state that no release files were changed.
Configuration errors, malformed GEDCOM, unsupported or corrupt manifests,
master checksum mismatches, ambiguous identity, unsafe removal, and output
failures are distinguished rather than reported as a generic merge failure.
When possible, an atomic timestamped failure report is written beneath the
release root. Secrets and free-form private notes are not included.

See the [incremental quickstart](GEDCOM_MERGE_QUICKSTART.md#keep-a-master-current-with-website-exports)
for a recurring offline workflow.

### Complete CLI argument reference

- `FILE [FILE ...]`: two or more input GEDCOM paths in source-priority order.
- `-o PATH`, `--output PATH`: merged GEDCOM destination (default
  `merged.ged`).
- `--ai-backend {none,ollama,openai,gemini,openrouter,auto}`: uncertain-pair
  adjudicator and, when enabled, quality-refinement provider.
- `--similarity-threshold 0..100`: minimum identity score sent to merge
  adjudication (default 78; the report-only duplicate threshold stays 90).
- `--auto`: disable interactive identity prompts and fail closed on rejected or
  unavailable AI decisions.
- `--root-person PERSON`: connected-component export root and fallback quality
  root.
- `--quality-root-person PERSON`, `--quality-report PATH`,
  `--no-quality-report`, and `--quality-ai`: control advisory reporting as
  described below.
- `--gedcom-version {5.5.5,5.5.1}`: choose the writer/header mode.
- `--ollama-model MODEL` and `--ollama-url URL`: local Ollama model and API
  base URL.
- `--openai-model MODEL`, `--gemini-model MODEL`, and
  `--openrouter-model MODEL`: provider-specific model identifiers. Model names
  are configuration, not hard-coded capability assumptions.
- `--openrouter-allowed-model PATTERN`: repeatable allowlist for OpenRouter's
  Auto Router.
- `--openrouter-cost-quality 0..10`: Auto Router preference from quality (0)
  toward cost savings (10).
- `--openrouter-zdr` / `--no-openrouter-zdr`: require or relax OpenRouter's
  zero-data-retention routing constraint.
- `--reasoning-effort {none,low,medium,high,xhigh}`: OpenAI reasoning setting.
- `--credit-check {required,best-effort,off}` and
  `--minimum-credit-usd AMOUNT`: preflight policy and minimum verified balance
  for a remote request.
- `-v`, `--verbose`: enable diagnostic logging without printing API keys or
  free-form source text.

## Quality report

Unless disabled, every successful merge writes a Markdown quality report next
to the GEDCOM. Its default name is `<output-stem>.quality.md`; for example,
`exports/master.ged` produces `exports/master.quality.md`.

Use the report options as follows:

- `--quality-report PATH` writes the report to an explicit path.
- `--no-quality-report` disables both the normal report and malformed-input
  diagnostic report.
- `--quality-root-person PERSON` chooses the report's root without filtering
  the merged GEDCOM.
- `--root-person PERSON` filters the GEDCOM to that connected family tree and
  also supplies the quality root when `--quality-root-person` is omitted.
- `--quality-ai` asks the already configured `--ai-backend` to review at most
  the top 25 quality findings. It does not select or configure a second
  backend.

When reporting is enabled, one of `--quality-root-person` or `--root-person` is
required. A GEDCOM pointer such as `@I123@` is safest; a full name must be
unique. Use `--quality-root-person` when you want a whole-file merge but a
rooted quality analysis:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend none --auto \
  --quality-root-person '@I123@' \
  -o master.ged
```

Potential duplicates appear in the quality report only at a score of 90 or
higher. This reporting threshold is fixed and is separate from
`--similarity-threshold`, which controls which candidate pairs enter merge
adjudication.

Name findings are deliberately conservative. The report may recommend
reviewing a likely birth/maiden surname or married surname, but it does not
rewrite a person's name from that recommendation. Ambiguous surname changes,
including cases without supporting family relationships, remain manual review
items.

If an input is malformed and reporting is enabled, the command returns a
failure status and writes the quality-report path as a diagnostic Markdown
report. It does not write a partial or replacement GEDCOM. Preserve that report
with the original input while correcting the syntax. With
`--no-quality-report`, malformed input still fails and no GEDCOM is written,
but no diagnostic report is requested.

The report itself contains genealogy data and should be protected like a
GEDCOM. Report generation is local unless `--quality-ai` is passed. With
`--quality-ai`, `none` remains offline, Ollama remains local when configured to
a local server, and a remote backend can receive bounded summaries for up to 25
findings under the same provider, retention, and credit settings used for merge
adjudication. Enabling a remote backend for the merge can transmit person
summaries even when `--quality-ai` is absent.

### Finding catalog and severity policy

Findings sort by severity, direct-ancestor status, nearest generation,
category, and stable evidence-derived ID. `critical` means a relationship or
chronology contradiction can make the lineage structurally impossible;
`high` means strong evidence should be reviewed before publishing; `medium`
means an actionable completeness, consistency, or reciprocity problem; and
`low` means a useful but uncertain or lower-impact research opportunity.

The deterministic v1 catalog is:

- Person completeness: `MISSING_NAME`, `MISSING_BIRTH_DATE`,
  `MISSING_BIRTH_PLACE`, `MISSING_DEATH_DATE`, `MISSING_DEATH_PLACE`,
  `MISSING_CITATION`, `MISSING_RELATIONSHIPS`, `MISSING_PARENT_LINK`,
  `INCOMPLETE_OCCUPATION`, and `INCOMPLETE_RESIDENCE`. A missing death date is
  reported only when the known birth year implies an age of at least 120.
- Vital and family chronology: `INVALID_DATE`, `ALTERNATIVE_VITAL_EVENTS`,
  `BIRTH_AFTER_DEATH`, `IMPLAUSIBLE_LIFESPAN`,
  `PARENT_CHILD_CHRONOLOGY`, `INVALID_MARRIAGE_DATE`,
  `MARRIAGE_BEFORE_MATURITY`, and `MARRIAGE_AFTER_DEATH`.
- Relationship integrity: `ANCESTRY_CYCLE`, `EMPTY_FAMILY`,
  `NONRECIPROCAL_FAMILY_REFERENCE`, and
  `NONRECIPROCAL_PERSON_REFERENCE`. Ancestor traversal is iterative and the
  report labels retained `PEDI` parentage as birth/unspecified, adopted,
  foster, or the source's other explicit value.
- Identity review: `POSSIBLE_DUPLICATE` and
  `POSSIBLE_MARRIED_PRIMARY_NAME`. These are always advisory; neither finding
  changes a merge decision or a `NAME` structure.
- Source/portability checks: `MISSING_HEAD`, `DUPLICATE_HEAD`, `MISSING_TRLR`,
  `DUPLICATE_TRLR`, `MISSING_CHARSET`, `PORTABILITY_CHARSET`,
  `MISSING_VERSION`, `PORTABILITY_VERSION`, `DUPLICATE_XREF`,
  `MALFORMED_XREF`, `LEVEL_SKIP`, `LONG_LINE`, and `DANGLING_REFERENCE`.

The married-name rule first uses explicit case-insensitive `NAME.TYPE`
birth/maiden/married evidence. Inference requires a surname match to a spouse,
known parent surnames that differ, and no retained birth/maiden form. A surname
that matches a parent is not flagged; spouse equality by itself is not enough.
Missing `SEX` plus a `WIFE` role can produce only a low-confidence review. The
tool evaluates all spouses once, never invents a maiden surname, and recommends
separate GEDCOM-compatible `NAME` structures with appropriate `TYPE` values.

## Identity scoring and family safety

Candidate scores use only evidence available on both records. A missing birth
date, death date, residence, or relative is unknown and is omitted from the
score denominator; it is never assigned a low score. Additional facts found in
only one source are retained without counting against that person.

The scorer compares:

- primary and alternate names;
- all birth and death dates, places, and explicit or inferred countries;
- sex when both records provide it;
- occupations and dated/placed residences;
- marriage, engagement, annulment, separation, and divorce facts, compared
  only against the same GEDCOM event tag;
- partner, parent, and child names plus available life dates;
- biological, adopted, foster, and other `PEDI` relationship values; and
- corroborating standard facts such as baptism, burial, census, immigration,
  nationality, education, religion, title, property, and retirement.

Name agreement alone is capped below deterministic-merge confidence. Automatic
merging requires at least three independent fields, sufficient evidence weight,
no hard contradiction, and either a person-level anchor or a matching family
event plus two distinct relative categories. Names plus relatives alone route
to AI or manual review instead of collapsing two common-name people. Sex,
distant life years,
different birth/death countries, incompatible partners/parents, or two
well-populated but disjoint child sets force review or retention.

Family records remain separate root records in the output. When two parents are
merged, every `FAMS`, `FAMC`, `HUSB`, `WIFE`, and `CHIL` edge is rewritten to
the survivor rather than deleted. A sparse aunt therefore is not rejected for
lacking a birth date, and her better-documented spouse, parents, or children can
support identity without putting cousins at risk.

Free-form notes, citations, media, source text, custom tags, and government
identifiers remain in the GEDCOM but are excluded from remote prompts. They are
too sensitive or ambiguous to be safe default identity evidence.

## Environment and API keys

The script loads `.env` automatically through `python-dotenv`. Put `.env` in
the repository root, next to `requirements.txt`; do not put it in `tools/`.
The repository ignores `.env` and `.env.*`, while retaining only the blank
`.env.example` template.

```bash
cp .env.example .env
chmod 600 .env  # macOS/Linux: optional but recommended
```

Set only the providers you intend to use:

```dotenv
# Local Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

# Direct OpenAI
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini

# Direct Google Gemini: set GEMINI_API_KEY or GOOGLE_API_KEY, not both
GEMINI_API_KEY=
GOOGLE_API_KEY=
GEMINI_MODEL=gemini-3.5-flash

# OpenRouter and managed model routing
OPENROUTER_API_KEY=
OPENROUTER_MANAGEMENT_KEY=
OPENROUTER_MODEL=openrouter/auto
OPENROUTER_COST_QUALITY=7
OPENROUTER_ZDR=true

# Merge defaults
GEDCOM_AI_BACKEND=ollama
AI_REASONING_EFFORT=low
REMOTE_CREDIT_CHECK=required
MINIMUM_REMOTE_CREDIT_USD=0.01
```

Create keys only on the providers' official pages:

- OpenAI: <https://platform.openai.com/api-keys>
- Google AI Studio: <https://aistudio.google.com/app/apikey>
- OpenRouter API keys: <https://openrouter.ai/settings/keys>
- OpenRouter management keys:
  <https://openrouter.ai/settings/management-keys>

Never pass API keys as command-line arguments, commit `.env`, paste keys into a
GEDCOM, or store them in shell history. The tool reads keys from the environment
and never writes them to output.

## Recommended remote setup: OpenRouter Auto Router

OpenRouter's Auto Router can choose a current model using a server-side
cost/quality policy. By default, this tool restricts it to OpenAI GPT-5 and
Google Gemini model families, denies providers that collect prompt data, and
requires zero-data-retention endpoints. Use `--no-openrouter-zdr` only after a
deliberate privacy review.

With `--ai-backend auto`, a configured and funded OpenRouter route is preferred.
The decision prompt still passes through OpenRouter before reaching a downstream
provider; ZDR and `data_collection=deny` constrain retention and downstream
selection, but do not make OpenRouter a local processor.

For strict account-credit checking, set both keys:

```dotenv
OPENROUTER_API_KEY=your-inference-key
OPENROUTER_MANAGEMENT_KEY=your-management-key
REMOTE_CREDIT_CHECK=required
OPENROUTER_ZDR=true
```

Then run:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend auto \
  --openrouter-zdr \
  --auto \
  --no-quality-report \
  -o master.ged
```

The default cost/quality value is `7`; `0` favors quality and `10` favors cost
savings. Override it and the permitted model pool when needed:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend openrouter \
  --openrouter-cost-quality 5 \
  --openrouter-allowed-model 'openai/gpt-5*' \
  --openrouter-allowed-model 'google/gemini-*' \
  --openrouter-zdr --auto --no-quality-report -o master.ged
```

The selected provider/model is logged for each AI decision. Model IDs are
configuration, so a model rename or retirement normally requires only an
environment or CLI change rather than a code edit.

## Direct OpenAI or Gemini

Direct APIs are supported with the official `openai` and `google-genai` Python
SDKs. The OpenAI Agents SDK is not required: each adjudication is one bounded,
structured-output request rather than an autonomous multi-agent workflow.

OpenAI:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend openai \
  --openai-model gpt-5.4-mini \
  --reasoning-effort low \
  --credit-check best-effort \
  --auto --no-quality-report -o master.ged
```

Gemini:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend gemini \
  --gemini-model gemini-3.5-flash \
  --credit-check best-effort \
  --auto --no-quality-report -o master.ged
```

### Why direct providers use `best-effort`

OpenRouter documents an [account-credit
endpoint](https://openrouter.ai/docs/api/api-reference/credits/get-credits) for
management keys. Normal OpenAI and Gemini inference keys do not expose a
documented endpoint that this tool can use to verify remaining prepaid balance.
Provider documentation was last checked on 2026-07-16. OpenAI documents
prepaid billing plus organization usage and cost reporting, while Gemini
documents billing setup and monitoring; an authentication or model-list probe
is not proof of available credits. See
[OpenAI prepaid billing](https://help.openai.com/en/articles/8264778-what-is-prepaid-billing),
the [OpenAI Usage and Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage),
and [Gemini billing](https://ai.google.dev/gemini-api/docs/billing).

The default `--credit-check required` therefore blocks direct OpenAI and Gemini
before any person data is sent. `--credit-check best-effort` is an explicit
acknowledgement that the provider may reject the eventual request for quota or
billing reasons. `--credit-check off` is available but is not recommended.

Credit preflights contain credentials and billing metadata only—never names,
dates, relationships, GEDCOM lines, or model prompts. Auto routing may fall
back after a failed preflight, but it does not retry an already-submitted
person prompt through a second remote provider.

Remote decision prompts contain the bounded person summaries listed in
"Identity scoring and family safety," source filenames, and GEDCOM pointers.
They do not contain notes, citations, media, government/external identifiers,
or custom vendor text. GEDCOM pointers and source filenames are internal
identifiers and are included. Use `none` or local Ollama when names, dates,
places, relationships, pointers, or filenames must not leave the computer.

## Rooted tree export

Use an existing GEDCOM pointer or a unique full name as the root. The output
contains that person's connected family graph rather than unrelated branches:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --root-person '@I123@' \
  --ai-backend ollama --auto -o rooted-master.ged
```

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --root-person 'Jane Smith' \
  --ai-backend none --auto -o rooted-master.ged
```

Names must resolve uniquely. A GEDCOM pointer is preferable when two people
share a name.

## GEDCOM versions and website uploads

GEDCOM is a transfer standard, so separate Ancestry, Geni, and MyHeritage
conversion scripts should not be the starting point. These sites still have
different importer behavior for custom tags, media, source citations, and
newer dialect details.

The default output is GEDCOM 5.5.5:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --gedcom-version 5.5.5 --ai-backend none --auto \
  --no-quality-report -o master.ged
```

If a destination rejects that version declaration, create an output that
declares GEDCOM 5.5.1 in its header from the same source inputs:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --gedcom-version 5.5.1 --ai-backend none --auto \
  --no-quality-report \
  -o master-5.5.1.ged
```

`--gedcom-version 5.5.1` changes the declared output version and applies the
writer's supported compatibility behavior. It is not a general 5.5.5-to-5.5.1
converter, does not remove every construct a particular importer may reject,
and does not certify full 5.5.1 conformance. Test the result in the target
importer. For Geni, try this 5.5.1-declared output first: [Geni's GEDCOM
guidance](https://help.geni.com/hc/en-us/articles/229705167-How-can-I-export-my-GEDCOM)
identifies 5.5.1 as its standard export type, and [its
importer](https://help.geni.com/hc/en-us/articles/229705127-Can-I-import-a-GEDCOM-into-Geni)
is designed around a focus profile in the shared World Family Tree. [Ancestry](https://ancestry.my.site.com/FrCa/articles/en_US/Support_Site/Uploading-and-Downloading-Trees)
and [MyHeritage](https://www.myheritage.com/help/en/articles/12852096-how-do-i-upload-import-a-gedcom-file-to-my-family-site-on-myheritage)
also accept GEDCOM uploads, but media binaries are not embedded in GEDCOM text;
preserve the original media separately.

Upload a copy, inspect a sample of people/families/sources in the destination,
and keep the generated master GEDCOM as the portable source of truth. Add a
site-specific compatibility profile only if real importer testing identifies
a reproducible vendor quirk; maintaining three speculative converters would
increase data-loss risk.

## Merge safety and review

- Cross-file candidates are blocked by names, year buckets, and documented
  relatives before fuzzy scoring, reducing unnecessary AI calls and memory use.
- Only independently supported, conflict-free deterministic matches merge
  without AI.
- Uncertain pairs go to the configured adjudicator.
- AI suggestions can choose a canonical displayed value only from source
  values; conflicts remain as alternative event blocks.
- Inference failures and invalid JSON fail closed: both people are retained.
  Credit-preflight failures may try another configured route before any person
  prompt is submitted.
- Output is written atomically; 5.5.5 output receives structural grammar and
  reference validation before replacement. This is not complete specification
  conformance certification.
- Input code is never evaluated and unsafe deserialization is not used.

Omit `--auto` to receive interactive confirmation for lower-confidence AI
decisions. For unattended jobs, keep `--auto` and review verbose routing logs:

```bash
python tools/gedcom_merge.py tree-a.ged tree-b.ged \
  --ai-backend auto --auto --verbose --no-quality-report -o master.ged
```

## Troubleshooting

`OPENROUTER_MANAGEMENT_KEY is not set` or an unverifiable-balance message:

- Create a management key and add it to `.env` for strict checking, or choose
  `--credit-check best-effort` if a per-key check is sufficient for your risk
  policy.

`OPENAI_API_KEY is not set`, `GEMINI_API_KEY ... is not set`, or
`OPENROUTER_API_KEY is not set`:

- Confirm `.env` is in the repository root and that the relevant value is not
  blank. Existing shell environment values override `.env`.

Ollama connection failure:

```bash
ollama serve
ollama pull llama3.1
```

Importer rejects the GEDCOM:

- Retry with `--gedcom-version 5.5.1`.
- Check the logged validation error and destination-specific import report.
- Preserve the original inputs and master output while investigating.

## Dependency installation

Install the tested dependency ranges from the repository root:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` is the single source of truth for dependency ranges.

Run checks with:

```bash
python -m pytest -q tests/test_gedcom_merge.py
python -m py_compile tools/gedcom_merge.py
```

## Documentation conventions

Documentation follows the [MIT Communication Lab coding and comment
guidance](https://mitcommlab.mit.edu/broad/commkit/coding-and-comment-style/)
and [Google documentation best
practices](https://google.github.io/styleguide/docguide/best_practices.html):

- descriptive names and small structured helpers carry the primary explanation;
- comments explain risks, invariants, and design choices rather than restating
  the next line;
- public contracts state inputs, outputs, failures, and important restrictions;
- the simplest working command appears first;
- behavior described in a contract receives a focused test; and
- stale or duplicated prose is removed instead of maintained in two places.
