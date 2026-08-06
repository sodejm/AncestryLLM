# AncestryLLM

AncestryLLM is a local-first platform for genealogy research tools. It combines
deterministic RootsMagic and GEDCOM workflows with optional, explicitly selected
LLM providers. The one-shot CLI and prompt-toolkit/Rich REPL are the only
implemented product surfaces in 0.5.0. They derive commands from the same
`CommandSpec` metadata and dispatch through the shared `CommandExecutor` into
transport-neutral application services. Version 0.5.0 adds a bounded offline
Electron shell with Home, Diagnostics, a sanitized capability summary, and
local visual Settings only, backed by a private packaged sidecar. The first
launch presents a bounded local welcome that asks for no account, provider,
credential, genealogy data, or cloud consent. It has no genealogy, file, job,
chat, provider, cloud-account, or updater surface. A supported v0.5.0 desktop
release is a manually installed official unsigned installer. macOS or Windows
may show an unknown-publisher or Gatekeeper prompt; verify the published
checksums and release evidence before installation. Unsigned CI artifacts and
unpacked development builds are verification inputs, not supported releases. The
internal adapter is not a public API, and later domain adapters must reuse the
existing service surface.

## Documentation

The canonical documentation in `docs/` is published to the
[AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/). The
[GitHub Wiki](https://github.com/sodejm/AncestryLLM/wiki) remains available as
another published view of the same source; do not treat it as a separate source
of truth.

## Install and start

Python 3.12 through 3.14 and a working OS credential store are required. Install
the isolated command with [pipx](https://pipx.pypa.io/):

```bash
pipx install ancestryllm
ancestry --version
ancestry --help
```

Install every optional LLM provider with
`pipx install 'ancestryllm[all-llm]'`. Remote providers are never selected from
installed packages or environment keys: every cloud call still requires an
explicit profile and consent.

Run `ancestry` with no arguments for the prompt-toolkit/Rich interactive
console. It is the only supported interactive console; the
prompt stays responsive while background operations render live, sanitized
spinner or completed-unit progress above it. One-shot and JSON output never
emit terminal animation. The canonical command reference, examples, offline
defaults, and privacy rules are
in [the CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CLI.md);
see [the console guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md)
for interactive use and
[the file-ingress policy](https://github.com/sodejm/AncestryLLM/blob/main/docs/FILE_INGRESS.md)
for per-format input limits and stable rejection codes.

## Included modules

- `rootsmagic`: immutable, bounded SELECT/CTE queries and deterministic GEDCOM export.
- `gedcom`: merge, rooted subtree, quality analysis, incremental update, and rebase.
- `prompts`: immutable prompt revisions with declared variables and output schemas.
- `people`: curated research people, identifiers, facts, links, and provenance.
- `providers`: explicit Ollama, OpenAI, Anthropic, Gemini, and OpenRouter profiles.
- `ocr`: schema-validated extraction through the same provider boundary.
- `secrets`: no-echo OS-keyring management; values never appear in status output.

## Development

```bash
git clone https://github.com/sodejm/AncestryLLM.git
cd AncestryLLM
make bootstrap
make test
make lint
make typecheck
make security
make sbom
```

`make bootstrap` creates `.venv`, installs the checkout in editable mode with
locked development and provider dependencies, and installs lightweight commit
hooks plus the canonical pre-push quality/security gate. Use `make setup` when
automation needs the environment without modifying Git hooks. The equivalent
manual install command is
`.venv/bin/pip install --editable '.[all-llm,dev]'`; it is for contributors,
not end-user installation.

The dependency graph is locked in `uv.lock`. Never commit real family trees,
GEDCOM exports, databases, logs, reports, secrets, or research-person data.

Read [the architecture](https://github.com/sodejm/AncestryLLM/blob/main/ARCHITECTURE.md),
[desktop shell guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/DESKTOP_SHELL.md),
[desktop ADR](https://github.com/sodejm/AncestryLLM/blob/main/docs/ADR-0025-electron-fastapi-desktop.md),
[CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CLI.md),
[privacy and consent](https://github.com/sodejm/AncestryLLM/blob/main/docs/PRIVACY_AND_CONSENT.md),
[provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/PROVIDERS.md),
[GEDCOM compatibility](https://github.com/sodejm/AncestryLLM/blob/main/docs/GEDCOM_COMPATIBILITY.md),
[encrypted backups](https://github.com/sodejm/AncestryLLM/blob/main/docs/ENCRYPTED_BACKUPS.md),
[bounded file ingress](https://github.com/sodejm/AncestryLLM/blob/main/docs/FILE_INGRESS.md),
[CI workflow guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CI.md),
and [threat model](https://github.com/sodejm/AncestryLLM/blob/main/docs/THREAT_MODEL.md).

Versioning follows [Semantic Versioning 2.0.0](https://semver.org/). The
[versioning policy](https://github.com/sodejm/AncestryLLM/blob/main/docs/VERSIONING.md)
defines the CLI contracts covered by a release.

## Interoperability status

Automated round-trip and preservation tests run in CI. Ancestry, Geni, and
MyHeritage imports must still be manually smoke-tested for each release; this
repository does not claim production interoperability until that checklist is
completed and recorded.
