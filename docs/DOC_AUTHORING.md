# Documentation authoring rules

This document defines the information architecture, prose standards, and
validation rules for the AncestryLLM documentation corpus. It applies to all
Markdown files maintained under `docs/`.

## Diátaxis information architecture

AncestryLLM documentation is organized by reader purpose using the
[Diátaxis](https://diataxis.fr/) framework. Every maintained page has one
primary reader need. Pages that currently mix modes should be split only where
that materially improves use.

| Mode | Reader need | Lives under |
|------|-------------|-------------|
| Tutorial | Learning through guided experience | `docs/tutorials/` (future) |
| How-to guide | Accomplishing a specific goal | `docs/how-to/` (future) |
| Reference | Looking up factual contracts, commands, schemas, settings, codes, or compatibility tables | `docs/reference/` (future) |
| Explanation | Understanding concepts, rationale, boundaries, or design context | `docs/explanation/` (future) |

The subdirectories above are reserved for the future content migration tracked
in sodejm/AncestryLLM#258. The current corpus uses flat basenames under
`docs/`; no file is moved as part of this architecture decision. Publishing and
control files (`Home.md`, `_Sidebar.md`, `_config.yml`, `_layouts/`),
release-notes, release-evidence, and ADRs remain outside the four mode
directories because Diátaxis does not require forcing every artifact into one
of the four categories.

### Authoring criteria

**Tutorials** are learning-oriented guided experiences. A tutorial:

- Leads a learner through a complete, meaningful task from start to finish.
- Prioritizes learning over coverage; omits options and edge cases that would interrupt the flow.
- Uses present-tense imperative steps written from the learner's perspective.
- Ends with a concrete, verifiable result.

**How-to guides** are goal-oriented task guides. A how-to guide:

- Describes how to accomplish a specific reader goal that the reader already knows they want.
- Assumes the reader is competent and does not need explaining why things work.
- Uses numbered steps or a clear sequence.
- Focuses on one task; uses links to reference or explanation pages rather than embedding them inline.

**Reference** pages are factual contracts. A reference page:

- Documents commands, flags, environment variables, schemas, settings, error codes, or compatibility tables.
- Is accurate to the installed version; marks planned or incomplete behavior explicitly (see [Implemented versus planned behavior](#implemented-versus-planned-behavior) below).
- Uses tables, definition lists, and code blocks rather than narrative prose.
- Does not explain why things work; links to explanation pages instead.

**Explanation** pages provide design context. An explanation page:

- Answers why, not how.
- Discusses concepts, rationale, boundaries, trade-offs, and design decisions.
- Is not a step-by-step guide and does not duplicate reference output.
- Uses narrative prose and may include diagrams.

### Resolving mixed-mode pages

When a single page currently mixes modes:

1. Identify which mode covers the dominant reader need.
2. Reclassify the whole page to that mode if the other content is minor.
3. Split only if both parts are substantial and serve materially different audiences
   or use cases. Apply the [history and link preservation policy](#history-and-link-preservation-policy)
   when splitting.

## Prose baseline

The [GitHub Docs style guide](https://docs.github.com/en/contributing/style-guide-and-content-model/style-guide)
is the default editorial baseline for all maintained user-facing and
maintainer-facing prose. Diátaxis determines the purpose and organization of a
page; the style guide governs how that page is written.

### AncestryLLM-specific exceptions

The following narrow exceptions override the GitHub Docs baseline. Each
exception applies only to the listed context.

| Exception | Context | Reason |
|-----------|---------|--------|
| Exact command names such as `ancestry`, `ancestry database`, `ancestry ask` | Code spans and command output | Must reproduce the CLI parser output exactly |
| API and schema identifiers such as `CommandSpec`, `ModuleDescriptor`, `CommandInvocation` | Code spans in reference and architecture content | Must match the Python source |
| GEDCOM and genealogy terms such as GEDCOM, RootsMagic, INDI, FAM | First use on a page; code spans in examples | Established domain or product terminology |
| Quoted UI labels such as **Home**, **Diagnostics**, **Settings** | Desktop shell documentation | Must match the rendered UI |
| Authoritative contract language from accepted ADRs | ADR pages and pages that cite an ADR decision | Must preserve the legally accurate decision record |

All other prose follows the GitHub Docs baseline. Exceptions may be added only
by updating this table.

## Machine-checkable requirements

The following requirements can be validated deterministically in local
development or CI. The delivery issue responsible for each automated check is
noted; checks not yet automated are marked as planned.

| Requirement | Check | Delivery |
|-------------|-------|----------|
| `Home.md` must exist at the root of `docs/` | `validate_wiki_docs.py` | sodejm/AncestryLLM#257 |
| Sidebar targets must resolve to existing pages | `validate_wiki_docs.py` | sodejm/AncestryLLM#257 |
| No case-insensitive filename collisions under `docs/` | `validate_wiki_docs.py` | sodejm/AncestryLLM#257 |
| No duplicate wiki page basenames across `docs/` | `validate_wiki_docs.py` | sodejm/AncestryLLM#257 |
| No symlinked sources | `validate_wiki_docs.py` | sodejm/AncestryLLM#257 |
| Pages metadata sidecar covers all public pages | `prepare_pages_source.py` | sodejm/AncestryLLM#257 |
| Sentence-case headings (H2 and below) | Planned linter rule | sodejm/AncestryLLM#263 |
| Descriptive link text (no bare URLs or "click here") | Planned linter rule | sodejm/AncestryLLM#263 |
| Accessible image alt text (non-empty on meaningful images) | Planned linter rule | sodejm/AncestryLLM#263 |
| Unique page titles and descriptions across public pages | Planned metadata check | sodejm/AncestryLLM#263 |
| Prohibition on "planned" or "future" behavior in reference pages without an explicit status marker | Planned linter rule | sodejm/AncestryLLM#263 |
| `DOC_INVENTORY.md` exists and passes structural checks | `test_diataxis_doc_architecture.py` | sodejm/AncestryLLM#259 |
| `DOC_AUTHORING.md` exists and contains the GitHub Docs baseline reference | `test_diataxis_doc_architecture.py` | sodejm/AncestryLLM#259 |

## Human editorial review

The following requirements cannot be judged reliably by a linter and require
human review at pull request time.

- **Clarity and concision.** Sentences are direct and at the level appropriate
  for the target audience. Passive voice and nominalizations are avoided where
  they obscure agency.
- **Audience fit.** Each page uses the vocabulary and assumed knowledge level of
  its primary reader. A how-to guide written for an end user does not assume
  contributor-level understanding of the codebase.
- **Accurate current-versus-planned status.** Pages distinguish between behavior
  that is implemented and shipping from behavior that is planned or
  incomplete. No planned page is linked as if it already exists.
- **Natural keyword placement.** Titles, H1 headings, and opening summaries use
  the reader's likely search terminology naturally, without repetitive or
  misleading keyword stuffing.
- **Cross-link quality.** Internal links from landing and navigation pages
  prevent orphaned content. Link text describes the destination clearly without
  over-promising functionality.
- **Inclusive language.** Follows the GitHub Docs baseline on inclusive and
  accessible language; for example, avoids idioms with non-neutral connotations
  and uses gender-neutral pronouns.
- **Diátaxis purpose.** The page fulfills the primary reader need for its
  declared Diátaxis mode and does not drift into another mode.

## Implemented versus planned behavior

Every page that describes a product surface must clearly separate what is
implemented and shipping from what is planned or incomplete.

- In reference pages, mark planned flags, commands, or settings with a note:
  `<!-- status: planned -->` inline or a visible "Planned" badge.
- In how-to guides, do not include steps that require unimplemented features
  without a visible warning block.
- In tutorials, use only currently working paths.
- In explanation pages, explicitly state when described architecture or behavior
  is future-targeted rather than currently implemented.
- The desktop surfaces (Electron shell, FastAPI domain adapters) are planned or
  partially implemented. Pages covering them must include an explicit
  implementation status note at the top.

The implemented surfaces as of 0.5.0 are the one-shot CLI and the
prompt-toolkit/Rich REPL. The bounded 0.5.0 Electron shell covers Home,
Diagnostics, a sanitized capability summary, and local visual Settings only;
it has no genealogy, files, jobs, chat, providers, cloud accounts, or updater
surface.

## Path and basename rules

These rules satisfy both the nested Pages output and the flat GitHub Wiki namespace.

1. **Globally unique, case-insensitive basenames.** Two Markdown files under
   `docs/` must not share a basename when compared case-insensitively, even if
   they are in different subdirectories. This is enforced by `validate_wiki_docs.py`.
2. **Retain existing basenames when moving pages.** Renaming a basename changes
   the Wiki page name and breaks existing links and bookmarks. Move the file
   with `git mv`; do not rename unless there is a compelling reason.
3. **Source-relative links.** Internal Markdown links use relative paths from the
   canonical `docs/` source (for example, `[CLI reference](CLI.md)`). The Pages
   build and wiki-sync scripts rewrite these links in the generated output; the
   canonical source is never modified.
4. **Anchors.** Fragment identifiers in links (for example, `CLI.md#rootsmagic-and-gedcom`)
   must match a heading in the target file. Renamed headings break anchor links;
   prefer adding a `<!-- anchor: old-id -->` comment to preserve stability.
5. **Assets.** Images and other non-Markdown assets go in `docs/assets/`. Use
   relative paths from `docs/` (for example, `assets/diagram.png`).
6. **Landing pages.** `Home.md` is the required root landing page for the wiki.
   Each future `docs/tutorials/`, `docs/how-to/`, `docs/reference/`, and
   `docs/explanation/` subdirectory should have its own `README.md` or a
   landing page listed in the sidebar.
7. **Compatibility links.** When a discoverable URL changes (because a file is
   renamed or moved and the basename changes), add a redirect or alias entry in
   the Pages sidecar contract owned by sodejm/AncestryLLM#257.

## History and link preservation policy

- **File moves.** Use `git mv` for all moves so that Git retains rename history.
  Do not delete and recreate.
- **When a split is justified.** Split a page only when the resulting pages serve
  materially different audiences or use cases, and both parts are substantial
  (more than a few short sections). Use `git mv` for the primary destination and
  create the secondary file fresh to preserve history on the most important part.
- **Changed discoverable links.** When a basename changes or a page is retired,
  record the old path and new path in the metadata sidecar contract managed by
  sodejm/AncestryLLM#257 so that the Pages build can emit a redirect page.
  Update all internal links in the same pull request.
- **Packaging and workflow references.** Before moving a file, check and update:
  - `MANIFEST.in`
  - `scripts/build_release.py` and related release-building scripts
  - Release-contract tests in `tests/test_release_contract.py`
  - Any `.github/workflows/` YAML that references the path

## Search and discoverability rules

These rules apply to every public page in the corpus. They follow the
[GitHub Docs making content findable in search](https://docs.github.com/en/contributing/writing-for-github-docs/making-content-findable-in-search)
guidance applied to reader intent and page architecture.

Each public page must define:

- **Audience.** The specific reader the page is written for.
- **Primary search intent.** The task or question a reader is trying to resolve.
- **Likely query terms.** The words and phrases a reader would use when searching.
  These should appear naturally in the title, H1, and opening summary without
  repetitive stuffing.
- **Unique search-facing title.** Unique across all public pages. Matches the H1
  heading. Accurate to implemented versus planned behavior.
- **Description.** A concise one- or two-sentence summary for the Pages metadata
  sidecar contract (sodejm/AncestryLLM#257). Unique across all public pages.
  Does not duplicate the title verbatim.
- **Discoverable-URL disposition.** Whether the page's URL is stable, redirected
  from a previous URL, or retired.

Because the same canonical Markdown is synchronized to the GitHub Wiki, page
descriptions and metadata are recorded in the Pages sidecar contract rather
than added as front matter to the canonical content pages.

### Prohibited search practices

- Keyword stuffing: repeating the same search term in headings or opening
  paragraphs beyond natural usage.
- Duplicate descriptions: copying the same description boilerplate across
  multiple pages.
- Titles that overpromise: describing features as implemented when they are
  planned or incomplete.
- Orphaned content: pages that no landing or navigation page links to.
