# AncestryLLM

AncestryLLM is a local-first platform for genealogy research tools. It combines
deterministic RootsMagic and GEDCOM workflows with optional, explicitly selected
LLM providers. There is no supported web runtime yet; application services are
kept independent of the console so a future API and WebUI can reuse them.

## Install and start

Python 3.12 through 3.14 and a working OS credential store are required.

```bash
python3 -m venv .venv
.venv/bin/pip install --editable '.[all-llm,dev]'
.venv/bin/ancestry --help
```

The install command uses the `pip` executable inside `.venv`, so the project and
its dependencies stay isolated from the system Python installation. `install`
adds the package and its dependencies to that virtual environment. The
`--editable` option installs the project from its source checkout instead of
copying its Python files into the environment, so changes under `src/` are used
the next time the application runs without reinstalling the package. The `.`
selects the project in the current directory, while `[all-llm,dev]` requests the
optional dependency groups for every supported LLM provider and for development
and testing tools. The quotes prevent the shell from interpreting the brackets.
Rerun the command after changing project metadata or dependencies; editable mode
only makes source-code changes immediately available.

Run `.venv/bin/ancestry` with no arguments for the interactive console. The
canonical command reference, examples, offline defaults, and privacy rules are
in [the CLI guide](docs/CLI.md); see [the console guide](docs/CONSOLE.md) for
interactive use.

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
make setup
make test
make lint
make typecheck
make security
make sbom
```

The dependency graph is locked in `uv.lock`. Never commit real family trees,
GEDCOM exports, databases, logs, reports, secrets, or research-person data.

Read [the architecture](ARCHITECTURE.md), [CLI guide](docs/CLI.md), [privacy and consent](docs/PRIVACY_AND_CONSENT.md),
[provider guide](docs/PROVIDERS.md), [GEDCOM compatibility](docs/GEDCOM_COMPATIBILITY.md),
[encrypted backups](docs/ENCRYPTED_BACKUPS.md), and [threat model](docs/THREAT_MODEL.md).

## Interoperability status

Automated round-trip and preservation tests run in CI. Ancestry, Geni, and
MyHeritage imports must still be manually smoke-tested for each release; this
repository does not claim production interoperability until that checklist is
completed and recorded.
