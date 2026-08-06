# Documentation inventory and migration matrix

This document is the authoritative inventory for every maintained Markdown file
under `docs/`. It records the current path, primary Diátaxis type, intended
future path, disposition action, audience, implementation status, and
search-metadata fields required by the discoverability rules in
[DOC_AUTHORING.md](DOC_AUTHORING.md).

**Current epoch:** 0.5.0 pre-migration. No files are moved as part of this
document. All `intended path` entries in the **Move (future)** category are
targets for the content migration tracked in sodejm/AncestryLLM#258. Files
marked **Retain** stay at their current paths indefinitely.

## How to read this inventory

| Column | Meaning |
|--------|---------|
| Current path | Path relative to `docs/` |
| Diátaxis type | Tutorial / How-to / Reference / Explanation / Infrastructure / ADR / Release |
| Intended path | Target path after migration (if any) |
| Action | Retain / Move (future) / Split (future) / Merge (future) / Retire (future) |
| Audience | Primary reader |
| Status | Implemented / Partial / Planned |
| Search intent | What the reader is trying to accomplish |
| Likely queries | Representative search terms |
| Description | One-sentence description for the Pages sidecar contract |
| URL disposition | Stable / Redirect-from (old path) / Retired |

## Core navigation pages

| Current path | Diátaxis type | Intended path | Action | Audience | Status | Search intent | Likely queries | Description | URL disposition |
|---|---|---|---|---|---|---|---|---|---|
| `Home.md` | Infrastructure | `Home.md` | Retain | All readers | Implemented | Find the documentation home page and navigate to the right section | "AncestryLLM docs", "ancestry CLI documentation" | The central navigation and orientation page for AncestryLLM documentation. | Stable |
| `_Sidebar.md` | Infrastructure | `_Sidebar.md` | Retain | All readers | Implemented | Navigate between documentation sections | (navigation aid, not a search target) | Wiki sidebar navigation for AncestryLLM documentation. | Stable |
| `DOC_AUTHORING.md` | Infrastructure | `DOC_AUTHORING.md` | Retain | Contributors, maintainers | Implemented | Understand the documentation architecture and authoring rules | "documentation guidelines AncestryLLM", "Diátaxis AncestryLLM" | Diátaxis information architecture, prose standards, and validation rules for the AncestryLLM documentation corpus. | Stable |
| `DOC_INVENTORY.md` | Infrastructure | `DOC_INVENTORY.md` | Retain | Contributors, maintainers | Implemented | Find the complete documentation migration plan and page inventory | "doc inventory AncestryLLM", "migration matrix docs" | Full inventory and migration matrix for every maintained Markdown file under `docs/`. | Stable |

## Reference pages

| Current path | Diátaxis type | Intended path | Action | Audience | Status | Search intent | Likely queries | Description | URL disposition |
|---|---|---|---|---|---|---|---|---|---|
| `CLI.md` | Reference | `reference/CLI.md` | Move (future) | End users, script authors | Implemented | Find the supported `ancestry` commands and flags | "ancestry CLI commands", "ancestry --help", "ancestry command reference" | Canonical command-line reference for the `ancestry` tool, covering every implemented command and flag. | Stable → Redirect from `CLI.md` when moved |
| `COMMAND_EXECUTOR.md` | Reference | `reference/COMMAND_EXECUTOR.md` | Move (future) | Contributors, extension authors | Implemented | Understand the transport-neutral command executor contract | "CommandInvocation", "CommandExecutor", "command contract AncestryLLM" | Reference for the shared transport-neutral `CommandInvocation` and `CommandExecutor` boundary used by the CLI and REPL. | Stable → Redirect from `COMMAND_EXECUTOR.md` when moved |
| `APPLICATION_CONTRACTS.md` | Reference | `reference/APPLICATION_CONTRACTS.md` | Move (future) | Contributors, future adapter authors | Implemented | Find the framework-independent application-service boundary | "application contracts", "service DTOs", "adapter contracts AncestryLLM" | Framework-independent application-service contracts shared by the terminal adapters and future FastAPI/Electron adapters. | Stable → Redirect when moved |
| `ARCHITECTURE_CONTRACTS.md` | Reference | `reference/ARCHITECTURE_CONTRACTS.md` | Move (future) | Contributors, maintainers | Implemented | Find the ownership boundary checks enforced in CI | "architecture contracts", "ownership boundary", "dependency check" | Executable checks that enforce the public façades, ownership boundaries, and dependency contracts defined in `ARCHITECTURE.md`. | Stable → Redirect when moved |
| `VERSIONING.md` | Reference | `reference/VERSIONING.md` | Move (future) | End users, integrators | Implemented | Understand the versioning scheme and compatibility guarantees | "versioning AncestryLLM", "semantic versioning", "compatibility policy" | AncestryLLM versioning policy, Semantic Versioning 2.0.0 contract, and compatibility guarantees. | Stable → Redirect when moved |
| `GEDCOM_COMPATIBILITY.md` | Reference | `reference/GEDCOM_COMPATIBILITY.md` | Move (future) | End users, integrators | Implemented | Find the supported GEDCOM versions and RootsMagic syntax | "GEDCOM support", "RootsMagic compatibility", "GEDCOM export AncestryLLM" | Supported GEDCOM versions, RootsMagic export profiles, and release-gate checks for GEDCOM interoperability. | Stable → Redirect when moved |
| `FILE_INGRESS.md` | Reference | `reference/FILE_INGRESS.md` | Move (future) | Contributors, extension authors | Implemented | Understand the file-ingress policy and budget limits | "file ingress policy", "input validation", "byte budget" | Bounded file-ingress policy covering byte and record budgets, race detection, output alias rejection, and transactional publication. | Stable → Redirect when moved |
| `PROVIDERS.md` | Reference | `reference/PROVIDERS.md` | Move (future) | End users | Implemented | Find available LLM providers and how to configure them | "LLM providers", "Ollama AncestryLLM", "OpenAI AncestryLLM", "provider configuration" | Reference guide for the available LLM provider adapters, installation, secrets management, and consent profiles. | Stable → Redirect when moved |
| `MODULE_AUTHORING.md` | Reference | `reference/MODULE_AUTHORING.md` | Move (future) | Contributors, extension authors | Implemented | Learn how to register a built-in module | "built-in module", "ModuleDescriptor", "CommandSpec", "module authoring" | Reference for registering built-in modules through the explicit module registry using `ModuleDescriptor` and `CommandSpec`. | Stable → Redirect when moved |
| `api/API_REFERENCE.md` | Reference | `reference/API_REFERENCE.md` | Move (future) | API consumers, contributors | Partial | Find the FastAPI control API reference | "FastAPI API", "control API", "AncestryLLM API reference" | Reference for the authenticated FastAPI health and capability foundation introduced in 0.5.0. | Stable → Redirect when moved |
| `CORE_CONTRACTS_BASELINE.md` | Reference | `reference/CORE_CONTRACTS_BASELINE.md` | Move (future) | Contributors, maintainers | Implemented (historical) | Understand the pre-migration core-contracts characterization baseline | "core contracts baseline", "0.2.0 characterization" | Immutable characterization of implemented 0.2.0 behavior captured as a historical baseline before the Core Contracts migration. | Stable → Redirect when moved |

## How-to guides

| Current path | Diátaxis type | Intended path | Action | Audience | Status | Search intent | Likely queries | Description | URL disposition |
|---|---|---|---|---|---|---|---|---|---|
| `CONSOLE.md` | How-to | `how-to/CONSOLE.md` | Move (future) | End users | Implemented | Learn how to start and use the interactive console | "interactive console", "REPL AncestryLLM", "ancestry console" | Guide to starting and using the prompt-toolkit/Rich interactive console, including available commands and navigation. | Stable → Redirect when moved |
| `ENCRYPTED_BACKUPS.md` | How-to | `how-to/ENCRYPTED_BACKUPS.md` | Move (future) | End users | Implemented | Back up and restore an AncestryLLM database | "database backup AncestryLLM", "encrypted backup", "backup and recovery" | Instructions for creating encrypted backups with `ancestry database backup` and restoring from them. | Stable → Redirect when moved |
| `SETUP_DIAGNOSTICS.md` | How-to | `how-to/SETUP_DIAGNOSTICS.md` | Move (future) | End users, administrators | Implemented | Run storage diagnostics before first use | "first-run diagnostics", "storage check", "ancestry diagnostics" | How to run the read-only local health check before creating or opening a workspace. | Stable → Redirect when moved |
| `PRIVACY_AND_CONSENT.md` | How-to | `how-to/PRIVACY_AND_CONSENT.md` | Move (future) | End users | Implemented | Configure privacy controls and consent profiles | "privacy AncestryLLM", "consent profile", "living person rules", "data privacy" | How to configure privacy controls, consent profiles, and data-class restrictions for living and possibly living people. | Stable → Redirect when moved |
| `RELEASING.md` | How-to | `how-to/RELEASING.md` | Move (future) | Maintainers | Implemented | Release a new version of AncestryLLM | "release runbook", "AncestryLLM release process", "publish release" | Step-by-step runbook for preparing, signing, and publishing an AncestryLLM release from a clean `main` commit. | Stable → Redirect when moved |
| `WIKI_SYNC.md` | How-to | `how-to/WIKI_SYNC.md` | Move (future) | Maintainers | Implemented | Reproduce the wiki synchronization step locally | "wiki sync", "sync wiki docs", "publish wiki locally" | Instructions for running the deterministic wiki-synchronization step locally to verify publishing output. | Stable → Redirect when moved |
| `WIKI_OPERATIONS.md` | How-to | `how-to/WIKI_OPERATIONS.md` | Move (future) | Maintainers | Implemented | Operate, troubleshoot, or recover the wiki | "wiki recovery", "wiki operations", "wiki rollback" | Runbook for wiki dispatch, verification, troubleshooting, rollback, and reinitialization. | Stable → Redirect when moved |
| `DEPLOYMENT.md` | How-to | `how-to/DEPLOYMENT.md` | Move (future) | Maintainers | Implemented | Manage the hosted controls that build and publish desktop installers | "deployment AncestryLLM", "desktop installer build", "hosted controls" | Operations guide for the hosted controls used to build and publish AncestryLLM desktop installers. | Stable → Redirect when moved |
| `SECURITY_RESPONSE.md` | How-to | `how-to/SECURITY_RESPONSE.md` | Move (future) | Maintainers, security reporters | Implemented | Respond to a suspected vulnerability report | "security response", "vulnerability report AncestryLLM", "security advisory" | Checklist for handling suspected vulnerability reports, from private advisory creation through coordinated disclosure. | Stable → Redirect when moved |

## Explanation pages

| Current path | Diátaxis type | Intended path | Action | Audience | Status | Search intent | Likely queries | Description | URL disposition |
|---|---|---|---|---|---|---|---|---|---|
| `REPL_ARCHITECTURE.md` | Explanation | `explanation/REPL_ARCHITECTURE.md` | Move (future) | Contributors | Implemented | Understand the REPL architecture and compatibility boundary | "REPL architecture", "prompt-toolkit Rich REPL", "console architecture" | Design rationale and compatibility boundary for the prompt-toolkit/Rich interactive console. | Stable → Redirect when moved |
| `THREAT_MODEL.md` | Explanation | `explanation/THREAT_MODEL.md` | Move (future) | Maintainers, security reviewers | Implemented | Understand the data-flow threat model and security controls | "threat model AncestryLLM", "OWASP controls", "security architecture" | Data-flow threat model and OWASP Top 10:2025 and NIST SP 800-218 control matrix for AncestryLLM. | Stable → Redirect when moved |
| `LOCAL_LLM_BENCHMARKS.md` | Explanation | `explanation/LOCAL_LLM_BENCHMARKS.md` | Move (future) | Contributors, maintainers | Implemented | Understand the local LLM benchmark methodology and results | "local LLM benchmarks", "Ollama benchmark", "LLM performance AncestryLLM" | Methodology and results for the local LLM benchmarking script using fictional workloads. | Stable → Redirect when moved |
| `LOCAL_RETRIEVAL_EVALUATION.md` | Explanation | `explanation/LOCAL_RETRIEVAL_EVALUATION.md` | Move (future) | Contributors, maintainers | Planned | Understand the design boundary for future retrieval-augmented generation | "retrieval evaluation", "RAG AncestryLLM", "vector store design" | Design boundary and evaluation criteria for a possible future retrieval-augmented generation feature; not an implementation. | Stable → Redirect when moved |
| `CI.md` | Explanation | `explanation/CI.md` | Move (future) | Contributors, maintainers | Implemented | Understand the CI gate structure and what each gate checks | "CI AncestryLLM", "continuous integration gates", "CI workflow" | Design and structure of the AncestryLLM CI pipeline, including gate ordering and what each gate validates. | Stable → Redirect when moved |
| `DESKTOP_SHELL.md` | Explanation | `explanation/DESKTOP_SHELL.md` | Move (future) | Contributors, maintainers | Partial (0.5.0) | Understand the bounded 0.5.0 Electron shell scope and design | "desktop shell AncestryLLM", "Electron shell 0.5.0", "desktop scope" | Design and scope of the bounded 0.5.0 Electron control shell: supported targets, unsigned policy, and recovery contract. | Stable → Redirect when moved |
| `DESKTOP_SIDECAR.md` | Explanation | `explanation/DESKTOP_SIDECAR.md` | Move (future) | Contributors, maintainers | Partial (0.5.0) | Understand the packaged desktop sidecar architecture | "desktop sidecar", "packaged sidecar", "Electron sidecar" | Architecture of the control-only native sidecar used by the packaged Electron main process in 0.5.0. | Stable → Redirect when moved |
| `DESKTOP_VERIFICATION.md` | Explanation | `explanation/DESKTOP_VERIFICATION.md` | Move (future) | Maintainers | Partial (0.5.0) | Understand the desktop verification gate and evidence requirements | "desktop verification", "desktop gate", "desktop release evidence" | Structure of the Desktop gate: hosted matrix, machine-readable evidence, and release blockers for the bounded 0.5.0 shell. | Stable → Redirect when moved |

## ADRs (Architecture Decision Records)

ADRs remain outside the four Diátaxis mode directories. They are accepted
decision records and must not be reclassified or moved.

| Current path | Diátaxis type | Intended path | Action | Audience | Status | Search intent | Likely queries | Description | URL disposition |
|---|---|---|---|---|---|---|---|---|---|
| `ADR-0024-provider-framework-evaluation.md` | ADR | `ADR-0024-provider-framework-evaluation.md` | Retain | Contributors, maintainers | Implemented (Accepted) | Understand the decision to retain native provider adapters | "ADR-0024", "provider framework evaluation", "native provider adapters" | Accepted ADR: retain native provider adapters after evaluating third-party framework alternatives. | Stable |
| `ADR-0025-electron-fastapi-desktop.md` | ADR | `ADR-0025-electron-fastapi-desktop.md` | Retain | Contributors, maintainers | Accepted | Understand the hardened Electron and FastAPI desktop architecture decision | "ADR-0025", "Electron FastAPI desktop", "desktop architecture decision" | Accepted ADR: adopt a hardened Electron and FastAPI desktop architecture with a bounded 0.5.0 MVP scope. | Stable |

## Release notes

| Current path | Diátaxis type | Intended path | Action | Audience | Status |
|---|---|---|---|---|---|
| `release-notes/0.2.0.md` | Release | `release-notes/0.2.0.md` | Retain | All readers | Implemented |
| `release-notes/0.3.0.md` | Release | `release-notes/0.3.0.md` | Retain | All readers | Implemented |
| `release-notes/0.4.0.md` | Release | `release-notes/0.4.0.md` | Retain | All readers | Implemented |
| `release-notes/0.5.0.md` | Release | `release-notes/0.5.0.md` | Retain | All readers | Implemented |

## Release evidence

| Current path | Diátaxis type | Intended path | Action | Audience | Status |
|---|---|---|---|---|---|
| `release-evidence/README.md` | Infrastructure | `release-evidence/README.md` | Retain | Maintainers | Implemented |
| `release-evidence/issue-10-import-smoke-tests.md` | Infrastructure | `release-evidence/issue-10-import-smoke-tests.md` | Retain | Maintainers | Implemented |

## Packaging, test, and workflow references

The following table identifies references that each future file move must
update before the move pull request can be merged.

| File | References to update |
|------|---------------------|
| `MANIFEST.in` | Any `include docs/FILENAME.md` line for moved files |
| `scripts/build_release.py` | Documentation path arguments |
| `tests/test_release_contract.py` | Hardcoded `docs/` paths |
| `tests/test_pages_workflow_contract.py` | Any checked documentation paths |
| `tests/test_wiki_validation.py` | Sidebar target assertions |
| `.github/workflows/jekyll-gh-pages.yml` | Path arguments to `prepare_pages_source.py` |
| `.github/workflows/sync-wiki.yml` | Source paths passed to `sync_wiki_docs.py` |

Check the output of `grep -r "docs/" .github/ scripts/ tests/` before moving
any file and update every reference in the same pull request.
