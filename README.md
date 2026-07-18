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
.venv/bin/ancestry                 # interactive console
.venv/bin/ancestry --help          # one-shot commands
```

The first command that uses the research workspace creates an encrypted
SQLCipher database. Its random 256-bit key is stored only in macOS Keychain,
Windows Credential Locker, or Linux Secret Service. Non-secret settings live in
the platform-specific `config.toml`.

```bash
ancestry secrets set openai.api_key   # value is requested without echo
ancestry providers create personal-openai --provider openai --model gpt-5-mini
ancestry modules list
ancestry rootsmagic list
ancestry gedcom quality tree.ged --output quality.md --root-person "Ada Lovelace"
```

No provider is inferred from an installed key. `none` is the default and makes
no network requests. Cloud providers require an explicit provider profile and a
matching, active consent profile before genealogy data is rendered into a
request. Living and possibly living people are denied by default.

## Included modules

- `rootsmagic`: immutable, bounded SELECT/CTE queries and deterministic GEDCOM export.
- `gedcom`: merge, rooted subtree, quality analysis, incremental update, and rebase.
- `prompts`: immutable prompt revisions with declared variables and output schemas.
- `people`: curated research people, identifiers, facts, links, and provenance.
- `providers`: explicit Ollama, OpenAI, Anthropic, Gemini, and OpenRouter profiles.
- `ocr`: schema-validated extraction through the same provider boundary.
- `secrets`: no-echo OS-keyring management; values never appear in status output.

In the console, use `modules`, `use`, `info`, `show actions`, `show options`,
`set`, `unset`, `run`, and `back`. Shell execution, Python execution, scripts,
editing shortcuts, and redirection are disabled.

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

Read [the architecture](ARCHITECTURE.md), [privacy and consent](docs/PRIVACY_AND_CONSENT.md),
[provider guide](docs/PROVIDERS.md), [GEDCOM compatibility](docs/GEDCOM_COMPATIBILITY.md),
[encrypted backups](docs/ENCRYPTED_BACKUPS.md), and [threat model](docs/THREAT_MODEL.md).

## Interoperability status

Automated round-trip and preservation tests run in CI. Ancestry, Geni, and
MyHeritage imports must still be manually smoke-tested for each release; this
repository does not claim production interoperability until that checklist is
completed and recorded.
