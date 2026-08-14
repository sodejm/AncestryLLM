# Documentation architecture and authoring guide

This is the small, canonical Diataxis decision for AncestryLLM documentation.
It records the canonical classification and migration state. Issue #261 moved
the Reference and Explanation pages with history; rows for deferred How-to
migrations continue to record reviewed future work. `docs/` remains the only
canonical corpus for the generated Pages site and GitHub Wiki.

## Reader modes and supporting material

Choose the page's primary reader purpose before drafting. A page may contain a
small amount of another mode when that makes the current reader task clearer;
do not split it merely to make the taxonomy look tidy. Mark a page `Split later`
in the inventory when two independently useful reader journeys are substantial.

- **Tutorials** teach a newcomer by doing, with a safe, end-to-end result and
  an explanation of the steps. The published
  [offline GEDCOM merge tutorial](tutorials/offline-gedcom-merge.md) uses only
  fictional fixtures and `provider=none`; do not link to a proposed tutorial
  until it exists.
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
the released bounded Electron desktop control shell. Its released destinations
are Home, Diagnostics, Settings, and capability onboarding; it uses only the
authenticated health/capability sidecar. Clearly marked Unreleased source also
contains bounded file-grant, provider-configuration, and presentation-only
Tasks adapters plus a synchronous, bounded transient-chat API with exact
profile, model, policy, and consent checks. The Unreleased Chat destination
consumes the Main-owned stream through fixed bridge methods, renders hostile
model output through a closed Markdown component allowlist, and delegates plain
text copy and confirmed external HTTPS links to Main. Target-matched packaged
chat evidence, genealogy/domain task admission or execution, direct artifact
access, cloud accounts, and updater flows remain planned or incomplete. State
the released boundary, each marked Unreleased adapter, and the remaining scope
near desktop material; never present a later capability as a current tutorial.

## Deterministic screenshot contract

The schema-v1 contract in `config/docs-screenshot-manifest.json` is the single
reviewed inventory for documentation screenshots. Its closed schemas live in
`config/docs-screenshot-manifest-v1.schema.json` and
`config/docs-screenshot-fixture-v1.schema.json`; its inputs live only below
`tests/fixtures/docs_screenshots/`. The documentation maintainers own the
manifest and schemas. The Electron and terminal capture adapters consume that
contract but must not invent scenarios, destinations, fixtures, or comparison
rules outside it.

The repository-local
[documentation screenshot regeneration workflow](https://github.com/sodejm/AncestryLLM/blob/main/.agents/skills/docs-screenshot-regeneration/SKILL.md)
provides the maintainer-facing preflight, focused selection, worktree-safety,
visual-review, and final-report procedure. It delegates all capture and
validation to the canonical Make targets and never authorizes staging, commits,
pushes, or pull requests.

`scripts/docs_screenshots.py` is the shared publication and drift-check
orchestrator. `make docs-screenshots` captures all four declared scenarios into
an isolated staging tree, validates the complete inventory, then replaces the
published set transactionally with repository-readable `0644` modes. A failed
replacement restores both the previous bytes and their modes.
`make docs-screenshots-check` validates the committed inventory, captures a
fresh set into a temporary tree, compares exact PNG bytes, and leaves the
repository unchanged. PNG validation checks the complete chunk stream, chunk
ordering and CRCs, bounded image-data decompression, scanline filters, and the
declared image dimensions before an asset can be compared or published.
Markdown ownership is derived from parsed rendered-image tokens, so examples
inside code are ignored and reference-style images are enforced. Missing,
corrupt, changed, undeclared, or orphaned files; broken or undeclared rendered
Markdown references; generic alt text; and privacy-canary content fail closed.

On drift, the check may write a schema-v1 report to
`ANCESTRYLLM_DOCS_SCREENSHOT_REPORT`. The report contains only scenario IDs,
surface identifiers, expected and observed SHA-256 values, per-scenario status,
and overall status. It contains no pixels, response bodies, transcript,
environment, host identity, username, absolute path, fixture content, or other
application data. CI uploads only this bounded JSON report on failure.
Check mode still emits this report when a committed image is missing or
structurally corrupt, using the missing or invalid asset as drift evidence
rather than stopping before comparison.

The Electron adapter runs with `pnpm --dir desktop capture:docs` after the
exact locked Node, pnpm, Playwright, Electron, and bundled Inter-font identities
have been installed. The orchestrator creates an isolated tracked-source copy,
uses the repository's canonical locked desktop installer there, and sets
`ANCESTRYLLM_DOCS_SCREENSHOT_OUTPUT_ROOT` to an explicit staging directory. The
adapter builds the fixture-only desktop bundle, launches a real Electron
`BrowserWindow` through Playwright, waits for each manifest-declared ready
signal, and captures the fictional provider-none Home/Ready state and sanitized
degraded-diagnostics state twice. It requires byte-identical repeats under the
manifest viewport, device scale, light theme, UTC clock, locale, bundled Inter
font, and disabled animation controls. It inherits only a narrow environment
allowlist, blocks unexpected renderer networking, scans the rendered document
for every privacy canary, and writes only the two selected allowlisted paths.

The terminal adapter runs through `make docs-terminal-screenshots`. It validates
`config/docs-terminal-capture-policy.json`, builds a native Linux container
from exact digest-pinned VHS and uv images, verifies the expected VHS, ttyd,
Chromium, FFmpeg, and JetBrains Mono identities, and then drives the real
`.venv/bin/ancestry` one-shot CLI and interactive console through a true PTY.
Each selected terminal scenario is rendered twice with fixed shell, geometry, theme,
font, locale, timezone, prompt, timing, fictional state, and `provider=none`.
The two PNGs must be byte-identical before they are atomically published to
their exact allowlisted repository paths.

Capture execution is non-root, read-only, capability-free, and network-free.
Only an isolated temporary capture directory is writable; `HOME`, XDG,
application config, and application data all resolve inside it. Policy-owned
environment values are supplied explicitly without inheriting the host
environment, privacy canaries are scanned from the transcript, the real command
status is preserved, and temporary state must be empty after either success or
failure. The container receives no host home directory, repository credentials,
provider keys, Docker socket, or ordinary network access.

The shared manifest's `en_US.UTF-8` locale identity is backed by the pinned
image's immutable `C.utf8` locale data through an exact container-local alias.
The terminal output is intentionally ASCII-only; preflight verifies the alias,
target, selected locale name, and matching `LANG` and `LC_ALL` values before any
capture. This avoids a mutable locale-package installation while keeping the
shared Electron and terminal determinism contract unchanged.

For local macOS capture, install and start Docker Desktop (or another engine
that can run native Linux containers), run `make setup`, then run
`make docs-screenshots-check`. Host copies of VHS, ttyd, Chromium, FFmpeg, and
JetBrains Mono are neither used nor supported by this contract. The reference CI setup
uses a hosted Linux runner with exact Node 26.5.0 and pnpm 11.9.0, the frozen desktop
lock, the digest-pinned native terminal images, the manifest-owned locale,
timezone, viewport, fonts, and animation settings, and a pinned virtual display
package. A missing engine, dependency, architecture result, or capture is an
incomplete failure rather than a passing comparison.

Issue #420 owns documentation embedding, drift comparison, and CI enforcement
through this shared manifest and orchestrator.

To update the terminal toolchain, change the VHS image index digest, both
reviewed native descriptor digests, uv image digest, exact preflight version
strings, and font path and SHA-256 together in the policy and its closed schema.
Review the upstream release and native manifests, run the policy and focused
terminal-capture tests, capture twice from a clean checkout, compare the
reported PNG hashes, and visually review both fictional outputs. Never
substitute a mutable tag, alternate image, host executable, mirror, or relaxed
preflight check.

For a narrow local diagnosis, run
`make docs-screenshots DOCS_SCREENSHOT_SURFACE=<surface>` or
`make docs-screenshots DOCS_SCREENSHOT_SCENARIO=<scenario-id>`. Selection is
closed: unknown scenarios and a scenario from a different surface fail. These
selectors are not release evidence; `make docs-screenshots-check`, release
validation, and CI always execute the complete manifest.

For fixture-level tests, `--manifest` may select another validated manifest.
The orchestrator forwards that exact manifest to terminal capture and stages it
at the Electron adapter's fixed manifest path only inside the disposable capture
workspace, which is discarded without modifying the checkout. A custom manifest
is never silently replaced by the repository default.

Every publishable scenario must:

1. Use a tokenized, allowlisted launch command and the geometry for its declared
   `electron` or `terminal` surface.
2. Use a checked-in fictional fixture with `provider=none` and networking
   disabled. Never use a real genealogy record, credential, username, hostname,
   local path, prompt, response, or environment-derived value.
3. Declare one normalized repository-relative PNG destination below
   `docs/assets/screenshots/`; the exact same destination must appear once in
   the output allowlist.
4. Name each documentation page and heading that owns the image so renamed or
   retired destinations fail validation.
5. Use exact comparison. Schema v1 does not implement pixel-tolerance budgets and
   rejects them fail closed; adding tolerance requires a new reviewed schema,
   comparison implementation, and evidence contract.

The manifest fixes locale, timezone, theme, fonts, animation behavior,
timestamps, usernames, paths, identifiers, volatile values, and network policy.
The `privacy-canary` fixture is validation-only: no publishable scenario may
select it, and adapters must reject captured text containing any of its canary
values. A missing determinism control, unsafe or symlinked path, shell or URL
syntax, unknown schema field, undeclared output, or unapproved network behavior
fails closed with a stable `DOCSHOT_*` code.

To add a screenshot, first add or reuse a fictional fixture, then add the
scenario, output allowlist entry, every owning documentation reference, and
meaningful alt text in one change. Run focused manifest and publication tests,
run `make docs-screenshots`, visually review every changed fictional image, run
`make docs-screenshots-check`, then repeat the full capture and check from a
clean tree. Both cycles must produce identical hashes and no retained check-mode
changes.
To retire one, remove its scenario and output allowlist entry together, remove
the image only after every owning page stops referencing it, and confirm the
normalized plan contains no orphaned destination. Keep the success, degraded,
and unpublishable privacy-canary fixture states even when an individual scenario
is retired.

## Complete migration inventory

`Current path` is the canonical source location. `Intended path` is the reviewed
canonical destination: it exists today for moved or retained rows, while a
deferred row still records future work. `Moved` means history was preserved with
`git mv`; `Move later` requires the same treatment, and `Retain` means the current
name is intentionally stable. Search titles and descriptions are the review
briefs for the matching metadata-sidecar entries, not YAML front matter.

| Current path | Primary Diataxis type | Intended path | Action | Audience | Implementation status | Related owner | Primary search intent | Likely queries | Search-facing title | Description | Discoverable-URL disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ADR-0024-provider-framework-evaluation.md` | Supporting/control: architecture decision | `docs/ADR-0024-provider-framework-evaluation.md` | Retain | Maintainers | Implemented decision record | Provider architecture | Understand provider framework choice | provider framework decision; LiteLLM evaluation | Provider framework evaluation ADR | Records the provider framework evaluation and its decision. | Retain basename; no redirect needed |
| `ADR-0025-electron-fastapi-desktop.md` | Supporting/control: architecture decision | `docs/ADR-0025-electron-fastapi-desktop.md` | Retain | Maintainers | Released bounded shell; desktop-domain expansion planned | Desktop architecture | Understand the desktop boundary | Electron FastAPI ADR; desktop architecture decision | Electron and FastAPI desktop ADR | Records the released bounded Electron control-shell decision and the exclusion of later desktop-domain capabilities. | Retain basename; no redirect needed |
| `ADR-0026-local-first-container-remote-deployment.md` | Supporting/control: architecture decision | `docs/ADR-0026-local-first-container-remote-deployment.md` | Retain | Maintainers and operators | Profile control and macOS arm64 runtime-tool management implemented; application-runtime gates open | Deployment architecture | Understand local container and remote profiles | local container deployment; remote hosting; deployment trust boundary | Local-first container and advanced remote deployment ADR | Records the implemented profile-control and app-owned runtime-tool boundaries plus future application-runtime trust boundaries, ownership, budgets, and release gates. | Retain basename; no redirect needed |
| `reference/APPLICATION_CONTRACTS.md` | Reference | `docs/reference/APPLICATION_CONTRACTS.md` | Moved in #261 with git mv | Maintainers and adapter authors | Implemented service contracts | Application services | Look up service contracts | application DTOs; service ports | Application contracts | Defines transport-neutral application service, DTO, port, artifact, secret, and error contracts. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/ARCHITECTURE_CONTRACTS.md` | Reference | `docs/reference/ARCHITECTURE_CONTRACTS.md` | Moved in #261 with git mv | Maintainers | Implemented contracts | Architecture ownership | Look up ownership and dependency rules | architecture contracts; layer ownership | Architecture ownership and dependency contracts | Defines ownership, layer dependencies, and service contracts. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/CI.md` | Reference | `docs/reference/CI.md` | Moved in #261 with git mv | Contributors | Implemented CI | CI maintainers | Look up checks and reproduction | CI gates; run checks locally | Continuous integration | Describes CI gates, workflows, and local reproduction. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/CLI.md` | Reference | `docs/reference/CLI.md` | Moved in #261 with git mv | CLI users | Implemented CLI | Command surface | Look up commands and exit codes | ancestry CLI commands; exit codes | CLI reference — AncestryLLM | Complete command, option, and exit-code reference. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `CODE_DOCUMENTATION.md` | Supporting/control: governance | `docs/CODE_DOCUMENTATION.md` | Retain | Contributors | Implemented policy | Code documentation policy | Apply code documentation rules | code documentation policy; comments policy | Code documentation policy | Defines documentation expectations for repository code. | Retain basename; no redirect needed |
| `reference/COMMAND_EXECUTOR.md` | Reference | `docs/reference/COMMAND_EXECUTOR.md` | Moved in #261 with git mv | Maintainers and adapter authors | Implemented executor | Command execution boundary | Look up invocation and executor contracts | CommandExecutor; CommandInvocation | Command executor | Defines the transport-neutral command execution boundary. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `CONSOLE.md` | How-to guide | `docs/how-to/CONSOLE.md` | Retain in #260; move later with git mv after release-consumer path migration | REPL users | Implemented REPL | Console experience | Start and use an interactive session | ancestry console; interactive REPL | Interactive console guide — AncestryLLM | Starts and uses the prompt-toolkit/Rich genealogy REPL. | Retain current basename while release and contract consumers use it |
| `CORE_CONTRACTS_BASELINE.md` | Supporting/control: baseline record | `docs/CORE_CONTRACTS_BASELINE.md` | Retain | Maintainers | Implemented baseline | Core contracts | Audit the contract baseline | core contracts baseline; architecture baseline | Core contracts baseline | Records the established core-contract baseline. | Retain basename; no redirect needed |
| `reference/DEPENDENCY_MAINTENANCE.md` | Reference | `docs/reference/DEPENDENCY_MAINTENANCE.md` | Moved in #261 with git mv | Contributors and dependency maintainers | Implemented policy | Dependency maintenance | Update or audit dependency profiles | dependency groups; lockfile maintenance | Dependency maintenance | Describes purpose-specific groups, lock updates, and clean-environment verification. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `DEPLOYMENT.md` | Supporting/control: release operation | `docs/DEPLOYMENT.md` | Retain | Release maintainers | Released bounded desktop publication controls and macOS arm64 runtime-tool management; domain expansion planned | Desktop release | Prepare bounded shell publication | desktop deployment; packaged desktop | Desktop deployment guide | Describes installer publication controls and app-owned macOS arm64 runtime-tool management for the released bounded desktop shell, not a hosted application or domain-capability rollout. | Retain for the released bounded shell |
| `tutorials/desktop-first-run.md` | Tutorial | `docs/tutorials/desktop-first-run.md` | Create in #262; retain basename | New desktop users | Implemented v0.6 source learning path | Desktop experience | Complete a safe desktop first run | desktop first run; provider none desktop | Desktop first run | Reaches a verified network-free Home state with fictional local data and supported next steps. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/desktop-diagnostics.md` | How-to guide | `docs/how-to/desktop-diagnostics.md` | Create in #262; retain basename | Desktop users | Implemented v0.6 source guidance | Desktop diagnostics | Recover from a degraded desktop launch | desktop diagnostics; retry desktop service | Recover with desktop diagnostics | Interprets sanitized startup components, stable codes, and bounded recovery actions. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/desktop-file-access.md` | How-to guide | `docs/how-to/desktop-file-access.md` | Create in #262; retain basename | Desktop users | Implemented v0.6 source guidance | File grants | Grant bounded file access safely | desktop file access; opaque file grant | Grant desktop file access | Explains scoped opaque grants, immutable RootsMagic inputs, and loss-minimal GEDCOM handling. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/desktop-provider-consent.md` | How-to guide | `docs/how-to/desktop-provider-consent.md` | Create in #262; retain basename | Desktop provider users | Implemented v0.6 source guidance | Provider administration | Configure an endpoint and explicit consent | desktop provider consent; test endpoint | Configure a desktop provider and consent | Tests a provider endpoint, reviews exact disclosure scope, saves consent, and revokes it. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/desktop-tasks.md` | How-to guide | `docs/how-to/desktop-tasks.md` | Create in #262; retain basename | Desktop users | Implemented v0.6 source guidance | Task presentation | Monitor and cancel backend-owned tasks | desktop tasks; cancel task safe point | Monitor and cancel desktop tasks | Follows sanitized task progress and cooperative cancellation through a declared safe point. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/desktop-chat.md` | How-to guide | `docs/how-to/desktop-chat.md` | Create in #262; retain basename | Desktop provider users | Implemented v0.6 source guidance | Transient chat | Use bounded transient chat safely | desktop chat; transient conversation | Use transient desktop chat | Uses the bounded unsaved advisory chat surface without adding tools or evidence authority. | Retain unique basename; validated in flat Wiki namespace |
| `reference/DESKTOP.md` | Reference | `docs/reference/DESKTOP.md` | Create in #262; retain basename | Desktop users and maintainers | Implemented v0.6 source reference | Desktop experience | Look up desktop routes states and recovery | desktop states; desktop error codes; desktop shortcuts | Desktop reference | Records exact routes, states, stable codes, accessibility behavior, platforms, and safety boundaries. | Retain unique basename; validated in flat Wiki namespace |
| `explanation/DESKTOP_SHELL.md` | Explanation | `docs/explanation/DESKTOP_SHELL.md` | Moved in #261 with git mv | Maintainers and desktop control-surface users | Released bounded 0.5.0 shell; clearly marked Unreleased Tasks presentation and Chat presentation with audited transient-chat transport | Desktop architecture | Understand released shell limits | Electron desktop shell; desktop scope | Desktop shell | Explains the released Home, Diagnostics, Settings, and capability-onboarding boundary plus the bounded Unreleased Tasks and Chat presentation, Main-owned audited transient-chat bridge, safe model-output rendering, and excluded domain authority. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/DESKTOP_SIDECAR.md` | Reference | `docs/reference/DESKTOP_SIDECAR.md` | Moved in #261 with git mv | Desktop maintainers | Released control sidecar; Unreleased fixed job lifecycle, audited transient-chat adapters, and bounded Chat consumer; admission and domain routes excluded | Desktop architecture | Look up released sidecar constraints | desktop sidecar; Electron sidecar | Packaged desktop sidecar | Defines released private health and capability routes plus clearly marked Unreleased lifecycle and bounded chat-stream routes, bridge contracts, renderer ownership, and native-action constraints; excludes task admission, genealogy, arbitrary provider selection, cloud, and updater authority. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `DESKTOP_VERIFICATION.md` | How-to guide | `docs/how-to/DESKTOP_VERIFICATION.md` | Retain in #260; move later with git mv after release-consumer path migration | Desktop maintainers | Implemented verification gate for released bounded shell; later domain work planned | Desktop verification | Verify bounded desktop work | desktop verification; Electron test plan | Desktop verification guide | Defines exact-head verification for the released bounded shell and future desktop changes; it does not itself grant release approval. | Retain current basename while release and contract consumers use it |
| `DOCS_AUTHORING.md` | Supporting/control: governance | `docs/DOCS_AUTHORING.md` | Retain | Documentation contributors | Implemented decision | Documentation maintainers | Author and migrate docs correctly | Diataxis authoring; documentation rules | Documentation architecture and authoring guide | Defines Diataxis mapping, editorial rules, and migration control. | Retain basename; no redirect needed |
| `ENCRYPTED_BACKUPS.md` | How-to guide | `docs/how-to/ENCRYPTED_BACKUPS.md` | Retain in #260; move later with git mv after release-consumer path migration | Operators | Implemented workflow | Backup workflow | Back up and restore data | encrypted backup; restore AncestryLLM | Encrypted backup and recovery | Creates, verifies, and restores encrypted backups. | Retain current basename while release and contract consumers use it |
| `reference/FILE_INGRESS.md` | Reference | `docs/reference/FILE_INGRESS.md` | Moved in #261 with git mv | Users and maintainers | Implemented policy | File ingress | Look up safe file limits | file ingress limits; transactional publication | Bounded file ingress | Defines file budgets, race detection, and publication controls. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/GEDCOM_COMPATIBILITY.md` | Reference | `docs/reference/GEDCOM_COMPATIBILITY.md` | Moved in #261 with git mv | GEDCOM users | Implemented compatibility | GEDCOM integrity | Check supported formats and limits | GEDCOM compatibility; import smoke tests | GEDCOM compatibility and release checks | Lists supported formats, limits, and interoperability evidence. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `how-to/explore-the-interactive-console.md` | How-to guide | `docs/how-to/explore-the-interactive-console.md` | Create in #260; retain basename | New REPL users | Implemented REPL | Console experience | Inspect supported REPL modules and actions | explore ancestry console; REPL commands | Explore commands in the interactive console | Safely explores the implemented prompt-toolkit/Rich REPL without opening genealogy data or a provider. | Retain unique basename; validated in flat Wiki namespace |
| `how-to/run-an-offline-gedcom-merge.md` | How-to guide | `docs/how-to/run-an-offline-gedcom-merge.md` | Create in #260; retain basename | GEDCOM users | Implemented offline workflow | GEDCOM integrity | Merge fictional GEDCOM files without a provider | offline GEDCOM merge; provider none GEDCOM | Run an offline GEDCOM merge | Runs the supported GEDCOM 5.5.5 merge with fictional fixtures, verified output, and recovery steps. | Retain unique basename; validated in flat Wiki namespace |
| `Home.md` | Supporting/control: landing navigation | `docs/Home.md` | Retain | All readers | Implemented navigation | Documentation maintainers | Start documentation journey | AncestryLLM docs; genealogy CLI help | AncestryLLM documentation | Landing page for current CLI, REPL, and documentation paths. | Retain required Home basename |
| `reference/LOCAL_LLM_BENCHMARKS.md` | Reference | `docs/reference/LOCAL_LLM_BENCHMARKS.md` | Moved in #261 with git mv | Evaluators | Implemented evaluation record | Local model evaluation | Compare local model results | local LLM benchmarks; genealogy model quality | Local LLM benchmarks | Records local-model performance and quality evaluation. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/LOCAL_RETRIEVAL_EVALUATION.md` | Reference | `docs/reference/LOCAL_RETRIEVAL_EVALUATION.md` | Moved in #261 with git mv | Evaluators | Implemented evaluation record | Retrieval evaluation | Understand local retrieval results | local retrieval evaluation; genealogy retrieval | Local-first retrieval evaluation | Records local-first retrieval methodology and results. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/MODULE_AUTHORING.md` | Reference | `docs/reference/MODULE_AUTHORING.md` | Moved in #261 with git mv | Module authors | Implemented module contract | Module system | Look up built-in module requirements | author module; CommandSpec module | Built-in module authoring | Defines built-in module constraints, command registration, and testing requirements; add a procedural how-to only after the workflow is written. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `explanation/PRIVACY_AND_CONSENT.md` | Explanation | `docs/explanation/PRIVACY_AND_CONSENT.md` | Moved in #261 with git mv | All users | Implemented policy | Privacy and provider consent | Understand local-first privacy | genealogy privacy; cloud provider consent | Privacy and consent | Explains local-first privacy and explicit cloud consent. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/PROVIDERS.md` | Reference | `docs/reference/PROVIDERS.md` | Moved in #261 with git mv | Provider users | Implemented providers | Provider policy and capabilities | Look up provider behavior and constraints | provider none; local LLM; cloud provider consent | Provider guide — AncestryLLM | Documents provider policy, execution behavior, profiles, and capability limits; configuration commands remain in the CLI reference. | Wiki basename retained; Pages route moved after content review found no separate explanation journey, and the prior route retired to avoid duplicate canonical sources |
| `RELEASING.md` | How-to guide | `docs/how-to/RELEASING.md` | Retain in #260; move later with git mv after release-consumer path migration | Release maintainers | Implemented release workflow | Release process | Prepare and publish a release | AncestryLLM release; release checklist | Release runbook — AncestryLLM | Prepares, validates, and publishes releases. | Retain current basename while release and contract consumers use it |
| `explanation/REPL_ARCHITECTURE.md` | Explanation | `docs/explanation/REPL_ARCHITECTURE.md` | Moved in #261 with git mv | Maintainers | Implemented REPL | Console architecture | Understand REPL dispatch design | REPL architecture; command dispatch | REPL architecture | Explains REPL session and command-dispatch design. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/RUFF_EXPANSION_EVALUATION.md` | Reference | `docs/reference/RUFF_EXPANSION_EVALUATION.md` | Moved in #261 with git mv | Contributors and static-analysis evaluators | Implemented 0.6 batch record | Static-analysis toolchain | Review Ruff diagnostics and safety evidence | Ruff rules; import performance; Ruff expansion | Ruff rule-expansion evaluation | Records reviewed Ruff diagnostics, import and startup contracts, batch validation, and safety disposition. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `SECURITY_RESPONSE.md` | Supporting/control: security process | `docs/SECURITY_RESPONSE.md` | Retain | Security maintainers | Implemented response process | Security response | Handle a security report | security response; vulnerability disclosure | Security response checklist | Defines the security-response checklist and disclosure path. | Retain basename; no redirect needed |
| `security/verified-uv-bootstrap.md` | Supporting/control: security process | `docs/security/verified-uv-bootstrap.md` | Retain | Contributors and release maintainers | Implemented verified bootstrap | Supply-chain security | Verify or update the uv bootstrap | uv bootstrap; setup uv verification | Verified uv bootstrap | Describes trust policy, verification, receipts, reviewed updates, and recovery. | Retain unique basename; validated in flat Wiki namespace |
| `SETUP_DIAGNOSTICS.md` | How-to guide | `docs/how-to/SETUP_DIAGNOSTICS.md` | Retain in #260; move later with git mv after release-consumer path migration | New users | Implemented diagnostics | Storage diagnostics | Fix first-run storage problems | setup diagnostics; storage permissions | First-run storage diagnostics | Troubleshoots first-run storage and permission problems. | Retain current basename while release and contract consumers use it |
| `THREAT_MODEL.md` | Supporting/control: security governance | `docs/THREAT_MODEL.md` | Retain | Security and architecture maintainers | Implemented threat model | Threat model | Review data-flow controls | threat model; genealogy security controls | Data-flow threat model and control matrix | Records threats, controls, and residual-risk rationale. | Retain basename; no redirect needed |
| `reference/TY_ADVISORY_EVALUATION.md` | Reference | `docs/reference/TY_ADVISORY_EVALUATION.md` | Moved in #261 with git mv | Contributors and type-checker evaluators | Implemented 0.6 advisory record; 0.7 cutover conditional | Type-checking toolchain | Review ty diagnostics and cutover status | ty advisory; mypy parity; ty cutover | ty advisory evaluation | Records reproducible ty diagnostics, parity evidence, suppressions, timings, and the conditional cutover disposition. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `reference/UV_BUILD_EVALUATION.md` | Reference | `docs/reference/UV_BUILD_EVALUATION.md` | Moved in #261 with git mv | Contributors and package maintainers | Implemented 0.6 evaluation record; 0.7 adoption rejected/deferred under #305 | Build toolchain | Review backend comparison and adoption status | uv_build evaluation; artifact equivalence; build backend | uv_build evaluation | Records reproducible setuptools and uv_build artifacts, semantic drift, security controls, and the fail-closed adoption disposition. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `tutorials/offline-gedcom-merge.md` | Tutorial | `docs/tutorials/offline-gedcom-merge.md` | Create in #260; retain basename | New GEDCOM users | Implemented offline workflow | GEDCOM integrity | Learn an end-to-end offline GEDCOM merge | GEDCOM merge tutorial; provider none tutorial | Merge fictional GEDCOM records offline | Teaches a safe, end-to-end fictional GEDCOM 5.5.5 merge with no provider or network calls. | Retain unique basename; validated in flat Wiki namespace |
| `reference/VERSIONING.md` | Reference | `docs/reference/VERSIONING.md` | Moved in #261 with git mv | Users and release maintainers | Implemented version policy | Versioning | Check compatibility and upgrade policy | versioning; supported Python versions | Versioning and compatibility | Defines versioning, compatibility, upgrades, and deprecations. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
| `WIKI_OPERATIONS.md` | Supporting/control: publishing operation | `docs/WIKI_OPERATIONS.md` | Retain | Documentation operators | Implemented Wiki operation | Wiki publishing | Recover or operate Wiki synchronization | Wiki recovery; sync rollback | Wiki operations and recovery | Operates, verifies, and recovers GitHub Wiki synchronization. | Retain basename; no redirect needed |
| `WIKI_SYNC.md` | Supporting/control: publishing mechanism | `docs/WIKI_SYNC.md` | Retain | Documentation contributors | Implemented Wiki mechanism | Wiki publishing | Understand Wiki synchronization | sync docs to GitHub Wiki; flat Wiki names | Wiki synchronization | Explains canonical Wiki synchronization and namespace rules. | Retain basename; no redirect needed |
| `_Sidebar.md` | Supporting/control: navigation | `docs/_Sidebar.md` | Retain | Wiki readers | Implemented navigation | Documentation maintainers | Browse documentation navigation | AncestryLLM Wiki sidebar; docs navigation | AncestryLLM documentation sidebar | Provides generated Wiki navigation for current documentation. | Retain required Wiki sidebar basename |
| `reference/api/API_REFERENCE.md` | Reference | `docs/reference/api/API_REFERENCE.md` | Moved in #261 with git mv | Adapter authors | Implemented bounded 0.5.0 control API plus clearly marked Unreleased fixed chat-stream routes | FastAPI capability foundation | Look up health and capability API | API reference; health capability endpoint | API reference | Describes the authenticated released control API and the bounded Unreleased private chat-stream routes used by Electron Main. | Wiki basename retained; Pages route moved to the classified path and the prior route retired to avoid duplicate canonical sources |
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

- Link the current user surfaces `reference/CLI.md` and `CONSOLE.md` from both
  landing pages; they link to one another for the choice between one-shot and
  interactive work.
- Keep `reference/APPLICATION_CONTRACTS.md`,
  `reference/ARCHITECTURE_CONTRACTS.md`,
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

Treat the matrix as the change-control record for completed and future moves.
Before a `git mv` or split, update the row's intended path, action, status, owner, and
discoverable-URL disposition; inspect inbound links, metadata, navigation, and
the flat Wiki basename. Use `git mv` for a one-to-one relocation so history is
discoverable. For a split, retain the original until the new reader journeys,
links, metadata, and a compatibility strategy have been reviewed.

GitHub Pages and GitHub Wiki do not make an automatic redirect promise here.
Retain a stable basename where possible; otherwise add a tested compatibility
link only where the platform supports it, or record an intentional breaking URL
with owner and rationale. Do not claim that an uncreated route, desktop adapter,
or tutorial is available. Packaging, release, and workflow consumers that must
be reviewed for each move include `MANIFEST.in`, `scripts/build_release.py`,
`scripts/validate_wiki_docs.py`, `scripts/prepare_pages_source.py`,
`.github/workflows/sync-wiki.yml`, `.github/workflows/release.yml`, and their
contract tests, especially `tests/test_release_contract.py`,
`tests/test_wiki_validation.py`, `tests/test_wiki_sync.py`, and
`tests/test_prepare_pages_source.py`.

The source and both publishing targets intentionally cover every tracked page
under `docs/`. The Python sdist has a different consumer contract: it carries
only the reviewed transitive CLI and release-verification documentation closure
declared by `MANIFEST.in` and `scripts/build_release.py`. A publishing cutover
does not broaden that package allowlist; a new packaged-document requirement
must be reviewed and tested as a separate artifact-contract change.

### Issue #261 migration record

Issue #261 completed every one-to-one Reference and Explanation relocation with
`git mv`. GitHub Wiki discoverability remains stable because each moved page
retains its unique basename. GitHub Pages routes intentionally moved to their
classified paths, and the old source paths were retired so the corpus has one
canonical page for each subject. The executable OpenAPI schema remains at
`docs/api/openapi-v1.json`; only its prose API reference moved to
`docs/reference/api/API_REFERENCE.md`.

The provider page was reviewed as a whole and remains Reference: it supplies
lookup-oriented provider capabilities, configuration keys, and command links.
The rationale for local-first operation, cloud consent, and secret handling
belongs in `docs/explanation/PRIVACY_AND_CONSENT.md`, so no provider-page split
was warranted. Metadata, navigation, inbound links, package and release
allowlists, build evaluators, and contract tests changed with the moves.

This migration is organizational. Repository-root `ARCHITECTURE.md`, the ADRs,
the threat model, schemas, and executable contracts retain their authority.
Application and API behavior is unchanged, and the privacy, explicit cloud
consent, network-free `provider=none`, immutable-source, loss-minimal GEDCOM, and
release fail-closed invariants are not weakened.

## Validation and #263 coordination

Machine checks and human review are complementary. Run the deterministic source
and publishing checks after a documentation move or metadata change:

```console
make docs-cutover
.venv/bin/python -m pytest tests/test_documentation_architecture_\
contract.py \
  tests/test_wiki_validation.py tests/test_wiki_sync.py \
  tests/test_prepare_pages_source.py tests/test_pages_workflow_contract.py
```

Use the following boundary between machine evidence and human judgment:

| Scope | Machine validation | What it currently verifies | Human review still required |
| --- | --- | --- | --- |
| #257 publishing contract | `.venv/bin/python scripts/validate_wiki_docs.py --source docs`; `tests/test_wiki_validation.py`, `tests/test_wiki_sync.py`, `tests/test_prepare_pages_source.py`, and `tests/test_pages_workflow_contract.py` | Canonical paths and metadata coverage, source-relative links with supported anchors/assets, case-insensitive flat Wiki basenames, deterministic Wiki output, and staged Pages metadata | Useful cross-links, reader purpose, and whether language is clear or honestly frames planned work |
| #259 architecture contract | The #259 architecture-contract test named in the command above | Every Git-tracked Markdown page has one complete inventory row; landing navigation exposes reader modes; every public Pages metadata entry is complete and unique | Correct Diátaxis classification, audience fit, search wording, terminology, and whether a completed or proposed move or split is sensible |
| #263 cutover integration | `make docs-cutover` on a clean exact head, followed by the focused publishing tests and hosted exact-main checks | Exact Git SHA syntax; repeatable Pages and flat-Wiki manifests; idempotent Wiki synchronization; source links, assets, anchors, metadata, namespace collisions; and owned, reasoned, unexpired external-link exceptions without network access | Final row dispositions, prose and search quality, rendered discoverability, curated package impact, complete navigation, external URL health, and exact-main Pages and Wiki publication evidence |

Current machine checks do **not** enforce sentence case, descriptive link text,
meaningful alt text, terminology consistency, prose quality, or search intent.
Review those editorial requirements manually, alongside current-versus-planned
language and the cross-links above.

Issue #263 is the integration gate that consumes this inventory. Its local
`make docs-cutover` interface verifies repeatable Pages and Wiki staging plus
the exception registry on the exact integration head. Closing the cutover still
requires applicable packaging and release checks, editorial review, rendered
discoverability inspection, external URL health, and successful Pages and Wiki
publication from the resulting `main` commit. No local or pull-request result is
silently treated as hosted exact-main evidence.
