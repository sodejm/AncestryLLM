# Documentation architecture and authoring guide

This is the small, canonical Diataxis decision for AncestryLLM documentation.
It records a migration plan; it does **not** move, split, rename, or rewrite the
listed pages. `docs/` remains the only canonical corpus for the generated Pages
site and GitHub Wiki.

## Reader modes and supporting material

Choose the page's primary reader purpose before drafting. A page may contain a
small amount of another mode when that makes the current reader task clearer;
do not split it merely to make the taxonomy look tidy. Mark a page `Split later`
in the inventory when two independently useful reader journeys are substantial.

- **Tutorials** teach a newcomer by doing, with a safe, end-to-end result and
  an explanation of the steps. No tutorial is published yet; do not create a
  navigation link to a proposed tutorial until it exists.
- **How-to guides** help a reader accomplish a specific goal. They lead with
  prerequisites, steps, verification, and recovery.
- **Reference** is factual, complete enough to look something up, and stable in
  terminology: commands, contracts, compatibility, and evaluation records.
- **Explanation** gives the why: design boundaries, trade-offs, and concepts.
- **Supporting/control artifacts** are deliberately outside the four modes:
  architecture decisions, release notes and evidence, policies, governance,
  inventories, publishing mechanics, and operational controls. Do not relabel
  these as a reader mode just to fill a category.

The repository-root `ARCHITECTURE.md` and the ADRs remain authoritative
supporting records for architecture decisions. `ARCHITECTURE.md` is not staged
or published from `docs/`, so do not add an unsafe `../ARCHITECTURE.md` link to
the canonical documentation corpus; name its authority and link only to
published documentation records where a reader needs a destination.

Current user-facing behavior is the one-shot CLI, prompt-toolkit/Rich REPL, and
the released bounded Electron desktop control shell. Its supported destinations
are Home, Diagnostics, Settings, and capability onboarding; it uses only the
authenticated health/capability sidecar. Desktop-domain capabilities—genealogy/
domain routes, files, jobs, providers, cloud accounts, and updater flows—remain
planned or incomplete. State that released boundary and remaining scope near
desktop material, and never present a later capability as a current tutorial.

## Complete migration inventory

`Intended path` is the eventual canonical location after a separately approved
content migration. It is not a link and does not mean the path exists today.
`Move later` means preserve history with `git mv`; `Retain` means the current
name is intentionally stable. Search titles and descriptions are the review
briefs for the matching metadata-sidecar entries, not YAML front matter.

| Current path | Primary Diataxis type | Intended path | Action | Audience | Implementation status | Related owner | Primary search intent | Likely queries | Search-facing title | Description | Discoverable-URL disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ADR-0024-provider-framework-evaluation.md` | Supporting/control: architecture decision | `docs/ADR-0024-provider-framework-evaluation.md` | Retain | Maintainers | Implemented decision record | Provider architecture | Understand provider framework choice | provider framework decision; LiteLLM evaluation | Provider framework evaluation ADR | Records the provider framework evaluation and its decision. | Retain basename; no redirect needed |
| `ADR-0025-electron-fastapi-desktop.md` | Supporting/control: architecture decision | `docs/ADR-0025-electron-fastapi-desktop.md` | Retain | Maintainers | Released bounded shell; desktop-domain expansion planned | Desktop architecture | Understand the desktop boundary | Electron FastAPI ADR; desktop architecture decision | Electron and FastAPI desktop ADR | Records the released bounded Electron control-shell decision and the exclusion of later desktop-domain capabilities. | Retain basename; no redirect needed |
| `APPLICATION_CONTRACTS.md` | Reference | `docs/reference/APPLICATION_CONTRACTS.md` | Move later with git mv | Maintainers and adapter authors | Implemented service contracts | Application services | Look up service contracts | application DTOs; service ports | Application contracts | Defines transport-neutral application service, DTO, port, artifact, secret, and error contracts. | Plan compatibility link before move |
| `ARCHITECTURE_CONTRACTS.md` | Reference | `docs/reference/ARCHITECTURE_CONTRACTS.md` | Move later with git mv | Maintainers | Implemented contracts | Architecture ownership | Look up ownership and dependency rules | architecture contracts; layer ownership | Architecture ownership and dependency contracts | Defines ownership, layer dependencies, and service contracts. | Plan compatibility link before move |
| `CI.md` | Reference | `docs/reference/CI.md` | Move later with git mv | Contributors | Implemented CI | CI maintainers | Look up checks and reproduction | CI gates; run checks locally | Continuous integration | Describes CI gates, workflows, and local reproduction. | Plan compatibility link before move |
| `CLI.md` | Reference | `docs/reference/CLI.md` | Move later with git mv | CLI users | Implemented CLI | Command surface | Look up commands and exit codes | ancestry CLI commands; exit codes | CLI reference — AncestryLLM | Complete command, option, and exit-code reference. | Plan compatibility link before move |
| `CODE_DOCUMENTATION.md` | Supporting/control: governance | `docs/CODE_DOCUMENTATION.md` | Retain | Contributors | Implemented policy | Code documentation policy | Apply code documentation rules | code documentation policy; comments policy | Code documentation policy | Defines documentation expectations for repository code. | Retain basename; no redirect needed |
| `COMMAND_EXECUTOR.md` | Reference | `docs/reference/COMMAND_EXECUTOR.md` | Move later with git mv | Maintainers and adapter authors | Implemented executor | Command execution boundary | Look up invocation and executor contracts | CommandExecutor; CommandInvocation | Command executor | Defines the transport-neutral command execution boundary. | Plan compatibility link before move |
| `CONSOLE.md` | How-to guide | `docs/how-to/CONSOLE.md` | Move later with git mv | REPL users | Implemented REPL | Console experience | Start and use an interactive session | ancestry console; interactive REPL | Interactive console guide — AncestryLLM | Starts and uses the prompt-toolkit/Rich genealogy REPL. | Plan compatibility link before move |
| `CORE_CONTRACTS_BASELINE.md` | Supporting/control: baseline record | `docs/CORE_CONTRACTS_BASELINE.md` | Retain | Maintainers | Implemented baseline | Core contracts | Audit the contract baseline | core contracts baseline; architecture baseline | Core contracts baseline | Records the established core-contract baseline. | Retain basename; no redirect needed |
| `DEPLOYMENT.md` | Supporting/control: release operation | `docs/DEPLOYMENT.md` | Retain | Release maintainers | Released bounded desktop publication controls; domain expansion planned | Desktop release | Prepare bounded shell publication | desktop deployment; packaged desktop | Desktop deployment guide | Describes installer publication controls for the released bounded desktop shell, not a hosted application or domain-capability rollout. | Retain for the released bounded shell |
| `DESKTOP_SHELL.md` | Explanation | `docs/explanation/DESKTOP_SHELL.md` | Move later with git mv | Maintainers and desktop control-surface users | Released bounded 0.5.0 shell; desktop-domain capabilities planned | Desktop architecture | Understand released shell limits | Electron desktop shell; desktop scope | Desktop shell | Explains released Home, Diagnostics, Settings, and capability-onboarding shell boundary and its excluded domain capabilities. | Plan compatibility link before move |
| `DESKTOP_SIDECAR.md` | Reference | `docs/reference/DESKTOP_SIDECAR.md` | Move later with git mv | Desktop maintainers | Released control sidecar; domain routes excluded | Desktop architecture | Look up released sidecar constraints | desktop sidecar; Electron sidecar | Packaged desktop sidecar | Defines the released private health/capability sidecar constraints; it excludes genealogy, jobs, files, provider, cloud, and updater routes. | Plan compatibility link before move |
| `DESKTOP_VERIFICATION.md` | How-to guide | `docs/how-to/DESKTOP_VERIFICATION.md` | Move later with git mv | Desktop maintainers | Implemented verification gate for released bounded shell; later domain work planned | Desktop verification | Verify bounded desktop work | desktop verification; Electron test plan | Desktop verification guide | Defines exact-head verification for the released bounded shell and future desktop changes; it does not itself grant release approval. | Plan compatibility link before move |
| `DOCS_AUTHORING.md` | Supporting/control: governance | `docs/DOCS_AUTHORING.md` | Retain | Documentation contributors | Implemented decision | Documentation maintainers | Author and migrate docs correctly | Diataxis authoring; documentation rules | Documentation architecture and authoring guide | Defines Diataxis mapping, editorial rules, and migration control. | Retain basename; no redirect needed |
| `ENCRYPTED_BACKUPS.md` | How-to guide | `docs/how-to/ENCRYPTED_BACKUPS.md` | Move later with git mv | Operators | Implemented workflow | Backup workflow | Back up and restore data | encrypted backup; restore AncestryLLM | Encrypted backup and recovery | Creates, verifies, and restores encrypted backups. | Plan compatibility link before move |
| `FILE_INGRESS.md` | Reference | `docs/reference/FILE_INGRESS.md` | Move later with git mv | Users and maintainers | Implemented policy | File ingress | Look up safe file limits | file ingress limits; transactional publication | Bounded file ingress | Defines file budgets, race detection, and publication controls. | Plan compatibility link before move |
| `GEDCOM_COMPATIBILITY.md` | Reference | `docs/reference/GEDCOM_COMPATIBILITY.md` | Move later with git mv | GEDCOM users | Implemented compatibility | GEDCOM integrity | Check supported formats and limits | GEDCOM compatibility; import smoke tests | GEDCOM compatibility and release checks | Lists supported formats, limits, and interoperability evidence. | Plan compatibility link before move |
| `Home.md` | Supporting/control: landing navigation | `docs/Home.md` | Retain | All readers | Implemented navigation | Documentation maintainers | Start documentation journey | AncestryLLM docs; genealogy CLI help | AncestryLLM documentation | Landing page for current CLI, REPL, and documentation paths. | Retain required Home basename |
| `LOCAL_LLM_BENCHMARKS.md` | Reference | `docs/reference/LOCAL_LLM_BENCHMARKS.md` | Move later with git mv | Evaluators | Implemented evaluation record | Local model evaluation | Compare local model results | local LLM benchmarks; genealogy model quality | Local LLM benchmarks | Records local-model performance and quality evaluation. | Plan compatibility link before move |
| `LOCAL_RETRIEVAL_EVALUATION.md` | Reference | `docs/reference/LOCAL_RETRIEVAL_EVALUATION.md` | Move later with git mv | Evaluators | Implemented evaluation record | Retrieval evaluation | Understand local retrieval results | local retrieval evaluation; genealogy retrieval | Local-first retrieval evaluation | Records local-first retrieval methodology and results. | Plan compatibility link before move |
| `MODULE_AUTHORING.md` | Reference | `docs/reference/MODULE_AUTHORING.md` | Move later with git mv | Module authors | Implemented module contract | Module system | Look up built-in module requirements | author module; CommandSpec module | Built-in module authoring | Defines built-in module constraints, command registration, and testing requirements; add a procedural how-to only after the workflow is written. | Plan compatibility link before move |
| `PRIVACY_AND_CONSENT.md` | Explanation | `docs/explanation/PRIVACY_AND_CONSENT.md` | Move later with git mv | All users | Implemented policy | Privacy and provider consent | Understand local-first privacy | genealogy privacy; cloud provider consent | Privacy and consent | Explains local-first privacy and explicit cloud consent. | Plan compatibility link before move |
| `PROVIDERS.md` | Reference | `docs/reference/PROVIDERS.md` | Split later after content review | Provider users | Implemented providers | Provider policy and capabilities | Look up provider behavior and constraints | provider none; local LLM; cloud provider consent | Provider guide — AncestryLLM | Documents provider policy, execution behavior, profiles, and capability limits; configuration commands remain in the CLI reference. | Keep basename until split disposition is approved |
| `RELEASING.md` | How-to guide | `docs/how-to/RELEASING.md` | Move later with git mv | Release maintainers | Implemented release workflow | Release process | Prepare and publish a release | AncestryLLM release; release checklist | Release runbook — AncestryLLM | Prepares, validates, and publishes releases. | Plan compatibility link before move |
| `REPL_ARCHITECTURE.md` | Explanation | `docs/explanation/REPL_ARCHITECTURE.md` | Move later with git mv | Maintainers | Implemented REPL | Console architecture | Understand REPL dispatch design | REPL architecture; command dispatch | REPL architecture | Explains REPL session and command-dispatch design. | Plan compatibility link before move |
| `SECURITY_RESPONSE.md` | Supporting/control: security process | `docs/SECURITY_RESPONSE.md` | Retain | Security maintainers | Implemented response process | Security response | Handle a security report | security response; vulnerability disclosure | Security response checklist | Defines the security-response checklist and disclosure path. | Retain basename; no redirect needed |
| `SETUP_DIAGNOSTICS.md` | How-to guide | `docs/how-to/SETUP_DIAGNOSTICS.md` | Move later with git mv | New users | Implemented diagnostics | Storage diagnostics | Fix first-run storage problems | setup diagnostics; storage permissions | First-run storage diagnostics | Troubleshoots first-run storage and permission problems. | Plan compatibility link before move |
| `THREAT_MODEL.md` | Supporting/control: security governance | `docs/THREAT_MODEL.md` | Retain | Security and architecture maintainers | Implemented threat model | Threat model | Review data-flow controls | threat model; genealogy security controls | Data-flow threat model and control matrix | Records threats, controls, and residual-risk rationale. | Retain basename; no redirect needed |
| `VERSIONING.md` | Reference | `docs/reference/VERSIONING.md` | Move later with git mv | Users and release maintainers | Implemented version policy | Versioning | Check compatibility and upgrade policy | versioning; supported Python versions | Versioning and compatibility | Defines versioning, compatibility, upgrades, and deprecations. | Plan compatibility link before move |
| `WIKI_OPERATIONS.md` | Supporting/control: publishing operation | `docs/WIKI_OPERATIONS.md` | Retain | Documentation operators | Implemented Wiki operation | Wiki publishing | Recover or operate Wiki synchronization | Wiki recovery; sync rollback | Wiki operations and recovery | Operates, verifies, and recovers GitHub Wiki synchronization. | Retain basename; no redirect needed |
| `WIKI_SYNC.md` | Supporting/control: publishing mechanism | `docs/WIKI_SYNC.md` | Retain | Documentation contributors | Implemented Wiki mechanism | Wiki publishing | Understand Wiki synchronization | sync docs to GitHub Wiki; flat Wiki names | Wiki synchronization | Explains canonical Wiki synchronization and namespace rules. | Retain basename; no redirect needed |
| `_Sidebar.md` | Supporting/control: navigation | `docs/_Sidebar.md` | Retain | Wiki readers | Implemented navigation | Documentation maintainers | Browse documentation navigation | AncestryLLM Wiki sidebar; docs navigation | AncestryLLM documentation sidebar | Provides generated Wiki navigation for current documentation. | Retain required Wiki sidebar basename |
| `api/API_REFERENCE.md` | Reference | `docs/reference/api/API_REFERENCE.md` | Move later with git mv | Adapter authors | Implemented bounded 0.5.0 control API | FastAPI capability foundation | Look up health and capability API | API reference; health capability endpoint | API reference | Describes the authenticated health and capability API used by the released bounded desktop control shell. | Plan compatibility link before move |
| `release-evidence/README.md` | Supporting/control: release evidence | `docs/release-evidence/README.md` | Retain | Release maintainers | Implemented evidence index | Release evidence | Understand release-evidence layout | release evidence; verification artifacts | Release evidence index | Explains the release-evidence artifact layout. | Retain basename; no redirect needed |
| `release-evidence/issue-10-import-smoke-tests.md` | Supporting/control: release evidence | `docs/release-evidence/issue-10-import-smoke-tests.md` | Retain | Release maintainers | Historical evidence | GEDCOM release evidence | Find import smoke-test evidence | import smoke tests; GEDCOM release evidence | Import smoke-test evidence | Preserves historical GEDCOM import smoke-test evidence. | Retain basename; no redirect needed |
| `release-notes/0.2.0.md` | Supporting/control: release note | `docs/release-notes/0.2.0.md` | Retain | Users and release maintainers | Historical release | Release notes | Find version 0.2.0 changes | AncestryLLM 0.2.0 release notes | AncestryLLM 0.2.0 release notes | Records changes released in version 0.2.0. | Retain versioned pathname |
| `release-notes/0.3.0.md` | Supporting/control: release note | `docs/release-notes/0.3.0.md` | Retain | Users and release maintainers | Historical release | Release notes | Find version 0.3.0 changes | AncestryLLM 0.3.0 release notes | AncestryLLM 0.3.0 release notes | Records changes released in version 0.3.0. | Retain versioned pathname |
| `release-notes/0.4.0.md` | Supporting/control: release note | `docs/release-notes/0.4.0.md` | Retain | Users and release maintainers | Historical release | Release notes | Find version 0.4.0 changes | AncestryLLM 0.4.0 release notes | AncestryLLM 0.4.0 release notes | Records changes released in version 0.4.0. | Retain versioned pathname |
| `release-notes/0.5.0.md` | Supporting/control: release note | `docs/release-notes/0.5.0.md` | Retain | Users and release maintainers | Historical release | Release notes | Find version 0.5.0 changes | AncestryLLM 0.5.0 release notes | AncestryLLM 0.5.0 release notes | Records changes and validation evidence for the released version 0.5.0. | Retain versioned pathname |
| `release-notes/0.6.0.md` | Supporting/control: release note | `docs/release-notes/0.6.0.md` | Retain | Users and release maintainers | Planned v0.6 release record | Release notes | Review planned version 0.6.0 documentation changes | planned AncestryLLM 0.6.0 release notes | AncestryLLM 0.6.0 release notes | Prepares documentation notes for the planned version 0.6.0 release. | Retain versioned pathname |

## Authoring and editorial rules

Use the [GitHub Docs content style guide](https://docs.github.com/en/contributing/style-guide-and-content-model/style-guide)
as the editorial baseline: write for the reader's task, use clear active prose,
descriptive links, sentence-style headings, consistent terms, and accessible
alternatives for visual material. Project exceptions are intentionally narrow:

- Preserve exact command names, error codes, GEDCOM terms, code identifiers,
  historical release names, and the canonical `CLI`/`REPL` capitalisation when
  accuracy requires them.
- Preserve a stable control-artifact title when it is a release, ADR, evidence,
  or required publishing basename. Explain any other exception in the pull
  request; never exclude a whole directory from editorial review.
- Never include real genealogy data, credentials, prompt/response payloads,
  backups, or reports in examples or media.

Every public content page has a single page purpose and a concise opening that
confirms it. Use task-oriented verbs for how-to guides, a safe progression and
verification for tutorials, lookup-friendly tables for reference, and rationale
plus consequences for explanation. For a mixed page, label the primary purpose
in the inventory and add cross-links to the mode that serves the next reader
need. Split it only after content review proves two substantial journeys.

### Metadata, search, links, and media

Do not put Pages-only Jekyll front matter into canonical Markdown. Add or update
the matching `docs/_data/page_metadata.json` entry instead (the sidecar is
injected only into staged Pages output and excluded from the Wiki). Every public
page needs a unique, reader-facing title and description. Align its H1, opening,
title, description, and likely query with the actual content; use natural search
terms rather than keyword stuffing. `_Sidebar.md` is navigation control material
and has no public metadata entry.

Use source-relative Markdown links, source-relative anchors, and source-relative
asset paths in canonical content. Check an anchor after changing a heading and
give meaningful images alt text; provide prose, a table, or steps when media
carries essential meaning. Add useful cross-links from landing pages and the
relevant reader mode rather than collecting unrelated links at the end.

### Landing pages and related links

`Home.md` is the Pages-facing start page and `_Sidebar.md` is the flat Wiki
navigation entrypoint. Keep both focused on the same current reader modes and
use them to prevent high-value pages from becoming orphaned:

- Link the current user surfaces `CLI.md` and `CONSOLE.md` from both landing
  pages; they link to one another for the choice between one-shot and
  interactive work.
- Keep `APPLICATION_CONTRACTS.md`, `ARCHITECTURE_CONTRACTS.md`,
  `ADR-0024-provider-framework-evaluation.md`,
  `ADR-0025-electron-fastapi-desktop.md`, and `THREAT_MODEL.md` reachable from
  explanation, reference, or supporting architecture paths. Label released
  desktop control-surface material by its narrow scope, and label its excluded
  desktop-domain capabilities as planned or incomplete.
- Keep the publishing and release path reachable from both landing pages:
  `DOCS_AUTHORING.md`, `WIKI_SYNC.md`, `WIKI_OPERATIONS.md`,
  `release-notes/0.6.0.md` (planned), and `release-evidence/README.md`.
  Include the API reference from the reference path when linking to the
  released bounded shell's health/capability control API.

These are intentional navigation paths, not an assertion that every related
document belongs to the same Diátaxis mode. When a page moves or splits, check
its inbound links and update both landing surfaces before removing its prior
path.

Pages preserves nested source paths in its staged hierarchy. The Wiki flattens
every Markdown basename into one **case-insensitive** namespace, so a proposed
move must reserve a globally unique basename before it begins. Generated targets
use the #257 source-aware rewrite to preserve current canonical
source-relative-link semantics; do not author Pages URLs or Wiki URLs as a
substitute for canonical links. A unique basename alone is not sufficient:
validate the flat-Wiki target rewrite on every future move.

### Migration, history, and discoverability policy

Treat the matrix as the change-control record for a later move. Before a `git mv`
or split, update the row's intended path, action, status, owner, and
discoverable-URL disposition; inspect inbound links, metadata, navigation, and
the flat Wiki basename. Use `git mv` for a one-to-one relocation so history is
discoverable. For a split, retain the original until the new reader journeys,
links, metadata, and a compatibility strategy have been reviewed.

GitHub Pages and GitHub Wiki do not make an automatic redirect promise here.
Retain a stable basename where possible; otherwise add a tested compatibility
link only where the platform supports it, or record an intentional breaking URL
with owner and rationale. Do not claim that an uncreated route, desktop adapter,
or tutorial is available. Packaging, release, and workflow consumers that must
be reviewed before a later move include `MANIFEST.in`, `scripts/build_release.py`,
`scripts/validate_wiki_docs.py`, `scripts/prepare_pages_source.py`,
`.github/workflows/sync-wiki.yml`, `.github/workflows/release.yml`, and their
contract tests, especially `tests/test_release_contract.py`,
`tests/test_wiki_validation.py`, `tests/test_wiki_sync.py`, and
`tests/test_prepare_pages_source.py`.

## Validation and #263 coordination

Machine checks and human review are complementary. Run the deterministic source
and publishing checks after a documentation move or metadata change:

```console
.venv/bin/python scripts/validate_wiki_docs.py --source docs
.venv/bin/python -m pytest tests/test_documentation_architecture_\
contract.py \
  tests/test_wiki_validation.py tests/test_wiki_sync.py \
  tests/test_prepare_pages_source.py tests/test_pages_workflow_contract.py
```

Use the following boundary between machine evidence and human judgment:

| Scope | Machine validation | What it currently verifies | Human review still required |
| --- | --- | --- | --- |
| #257 publishing contract | `.venv/bin/python scripts/validate_wiki_docs.py --source docs`; `tests/test_wiki_validation.py`, `tests/test_wiki_sync.py`, `tests/test_prepare_pages_source.py`, and `tests/test_pages_workflow_contract.py` | Canonical paths and metadata coverage, source-relative links with supported anchors/assets, case-insensitive flat Wiki basenames, deterministic Wiki output, and staged Pages metadata | Useful cross-links, reader purpose, and whether language is clear or honestly frames planned work |
| #259 architecture contract | The #259 architecture-contract test named in the command above | Every Git-tracked Markdown page has one complete inventory row; landing navigation exposes reader modes; every public Pages metadata entry is complete and unique | Correct Diátaxis classification, audience fit, search wording, terminology, and whether a proposed move or split is sensible |
| #263 cutover integration (planned) | No new #263-specific command exists yet; run the #257 and #259 checks on the exact integration head, then add any move-specific checks with the change | The inherited publishing and inventory contracts, once run on the exact head | Final row dispositions, rendered discoverability, release and packaging impact, complete navigation, and any exception owner/expiry |

Current machine checks do **not** enforce sentence case, descriptive link text,
meaningful alt text, terminology consistency, prose quality, or search intent.
Review those editorial requirements manually, alongside current-versus-planned
language and the cross-links above.

Issue #263 is the integration gate that consumes this inventory. It must verify
the final disposition of every row, run the applicable deterministic Wiki,
Pages, packaging, and release checks on the exact head, inspect rendered
discoverability, and record any narrow exception with owner and expiry. This
issue does not change those publishing scripts or workflows; it supplies their
authoring contract and the test that keeps the inventory complete.
