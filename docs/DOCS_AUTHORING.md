# Documentation authoring guide

This guide defines the Diátaxis information architecture, authoring rules,
editorial baseline, migration inventory, and navigation contract for the
AncestryLLM documentation. It is a living document and should be updated as
pages are moved, split, or retired.

## Information architecture

AncestryLLM documentation follows the [Diátaxis framework](https://diataxis.fr/).
Each maintained page has one primary reader purpose:

| Mode | Purpose | Verb | Example |
|---|---|---|---|
| Tutorial | Learning-oriented guided experience | teaches | First genealogy search |
| How-to guide | Goal-oriented task | helps | Import a GEDCOM file |
| Reference | Factual, accurate, complete | informs | CLI command reference |
| Explanation | Understanding-oriented concepts | clarifies | Why provider none is default |

Publishing infrastructure, ADRs, release notes, and release evidence are not
Diátaxis content pages; they serve a durable-record or governance purpose and
are navigated from the sidebar under clearly labeled headings.

## Authoring rules

### Tutorials

- Teach through a safe, repeatable, end-to-end experience with fictional data.
- Start from a supported setup and reach a meaningful result.
- Distinguish prerequisites, expected outcomes, and cleanup explicitly.
- Describe only implemented CLI/REPL behavior; never present planned desktop
  behavior as available.
- Include a provider-`none`, network-free path wherever the task permits.

### How-to guides

- Help an already oriented reader accomplish one concrete goal.
- Lead with the goal in the heading.
- Keep examples aligned with the implemented one-shot CLI and interactive REPL.
- Include explicit consent and privacy guidance where cloud providers are involved.
- Never instruct readers to modify RootsMagic databases (read-only, loss-minimal).

### Reference

- Factual, scannable, version-specific.
- State what is implemented now; mark planned behavior with "Planned (v0.x)".
- Never restate rules already defined in `ARCHITECTURE.md` or accepted ADRs;
  link to them instead.

### Explanation

- Clarify rationale and relationships without becoming a task recipe.
- Link to authoritative architecture and ADR sources rather than duplicating them.
- Separate implemented behavior from planned behavior visibly.

## Editorial baseline

Use the [GitHub Docs style guide](https://docs.github.com/en/contributing/style-guide-and-content-model/style-guide)
as the default editorial baseline. Diátaxis determines page purpose;
the style guide governs how a page is written.

### Sentence case

Use sentence case for headings and titles. Exceptions: product names,
command names, and proper nouns (`AncestryLLM`, `GEDCOM`, `Home.md`).

### Descriptive links

Use descriptive link text that identifies the destination. Avoid "click here",
"this link", or "here".

- ✅ See the [CLI reference](CLI.md) for one-shot commands.
- ❌ See the CLI reference [here](CLI.md).

### Accessible media text

Every image must have meaningful alt text that conveys what the image shows
to a reader using a screen reader.

### Implemented vs. planned behavior

Every page must distinguish currently implemented behavior from planned or
deferred behavior. Use the following conventions:

- Implemented: state the behavior directly.
- Planned: prefix with "Planned (v0.x):" or add a note block.

### Terminology

Use these canonical terms consistently:

| Term | Meaning |
|---|---|
| `provider none` | The local, network-free LLM provider |
| one-shot CLI | The non-interactive `ancestry` command |
| interactive REPL | The prompt-toolkit/Rich console |
| GEDCOM | The genealogy data interchange format |
| RootsMagic file | `.rmtree` files (always read-only) |

### Project-specific exceptions to the GitHub Docs baseline

These exceptions apply only where required:

| Exception | Rationale |
|---|---|
| Exact CLI flag names in code spans (`--provider none`) | Must match the implemented interface exactly |
| Schema identifiers and field names in code spans | Must match the authoritative contract |
| Quoted UI labels in desktop documentation | Must match the rendered UI string exactly |
| `GEDCOM` always uppercase | Industry-standard convention |
| `AncestryLLM` as one word with camel case | Product name |

## Machine-checkable requirements

The following requirements can be checked deterministically and locally:

- `python scripts/validate_wiki_docs.py --source docs` — no symlinks,
  required pages present, no case collisions, no duplicate flat basenames,
  no broken sidebar targets.
- `python scripts/prepare_pages_source.py --source docs --destination /tmp/pages-check` —
  no broken Markdown targets in the Pages staging tree.
- `python scripts/check_architecture_contracts.py` — no architecture contract
  violations in source.
- `./scripts/check_repository_safety.sh` — no private artifacts tracked.

## Human editorial review checklist

The following must be reviewed by a human before cutover:

- [ ] Clarity and concision for the target audience.
- [ ] Audience fit: each page addresses one primary reader purpose.
- [ ] Descriptive links: no "click here" or "here" link text.
- [ ] Accessible media text: all images have meaningful alt text.
- [ ] Consistent terminology per the table above.
- [ ] Accurate current-versus-planned status on every page.
- [ ] No page contradicts `ARCHITECTURE.md`, accepted ADRs, or current code.

## Migration inventory

The table below maps every maintained page to its Diátaxis type, reader
audience, implementation status, and disposition. Pages not listed here are
publishing infrastructure, ADRs, release notes, or release evidence.

| Page | Diátaxis type | Audience | Status | Disposition |
|---|---|---|---|---|
| Home.md | Navigation | All | Implemented | Retain as navigation landing |
| CLI.md | Reference | Users | Implemented | Retain |
| CONSOLE.md | How-to + Reference | Users | Implemented | Retain; split tutorial later |
| PROVIDERS.md | Reference + Explanation | Users | Implemented | Retain |
| PRIVACY_AND_CONSENT.md | Explanation | Users | Implemented | Retain |
| GEDCOM_COMPATIBILITY.md | Reference | Users + Maintainers | Implemented | Retain |
| VERSIONING.md | Reference | Users + Maintainers | Implemented | Retain |
| RELEASING.md | How-to | Maintainers | Implemented | Retain |
| ENCRYPTED_BACKUPS.md | How-to | Users | Implemented | Retain |
| SETUP_DIAGNOSTICS.md | How-to + Reference | Users | Implemented | Retain |
| FILE_INGRESS.md | Reference + Explanation | Users + Maintainers | Implemented | Retain |
| MODULE_AUTHORING.md | How-to + Reference | Maintainers | Implemented | Retain |
| LOCAL_LLM_BENCHMARKS.md | Reference | Maintainers | Implemented | Retain |
| LOCAL_RETRIEVAL_EVALUATION.md | Reference | Maintainers | Implemented | Retain |
| CI.md | Reference | Maintainers | Implemented | Retain |
| ARCHITECTURE_CONTRACTS.md | Reference | Maintainers | Implemented | Retain |
| COMMAND_EXECUTOR.md | Reference + Explanation | Maintainers | Implemented | Retain |
| REPL_ARCHITECTURE.md | Explanation | Maintainers | Implemented | Retain |
| APPLICATION_CONTRACTS.md | Reference | Maintainers | Implemented | Retain |
| CORE_CONTRACTS_BASELINE.md | Reference | Maintainers | Implemented | Retain (publishing infra) |
| DEPLOYMENT.md | How-to + Reference | Maintainers | Implemented | Retain |
| WIKI_SYNC.md | How-to + Reference | Maintainers | Implemented | Retain (publishing infra) |
| WIKI_OPERATIONS.md | How-to | Maintainers | Implemented | Retain (publishing infra) |
| SECURITY_RESPONSE.md | How-to | Maintainers | Implemented | Retain |
| THREAT_MODEL.md | Reference + Explanation | Maintainers | Implemented | Retain |
| DESKTOP_SHELL.md | Reference | Users | Planned (v0.5) | Retain; mark planned sections |
| DESKTOP_SIDECAR.md | Explanation | Maintainers | Planned (v0.5) | Retain; mark planned sections |
| DESKTOP_VERIFICATION.md | Reference | Maintainers | Planned (v0.5) | Retain; mark planned sections |
| ADR-0024-\*.md | ADR | Maintainers | — | Retain as ADR |
| ADR-0025-\*.md | ADR | Maintainers | — | Retain as ADR |

## Path and basename rules

All Markdown files under `docs/` must have globally unique, case-insensitive
basenames. The GitHub Wiki uses a flat page namespace: `docs/guides/CLI.md`
would conflict with `docs/CLI.md` because both produce a Wiki page named `CLI`.

Current `docs/` uses a flat layout except for organized subdirectories
(`release-notes/`, `release-evidence/`, `api/`, `_layouts/`, `_data/`).
Subdirectory pages under `release-notes/`, `release-evidence/`, and `api/`
are not published to the Wiki (they are not top-level `*.md` files).

When moving a page:
1. Use `git mv` to preserve history.
2. Update all inbound links in `docs/` and `_Sidebar.md`.
3. Update `MANIFEST.in` and any release-building script references.
4. Record the old basename in this inventory as "moved to X".

## Link preservation policy

Previously discoverable documentation pages must be accounted for:

- **Retained**: the page exists at the same path.
- **Compatibility-linked**: a stub page at the old path links to the new location.
- **Redirected**: GitHub Pages redirect (not yet supported by the Jekyll build).
- **Intentionally broken**: listed here with rationale.

There are no intentionally broken documentation URLs in the current v0.6 scope.
