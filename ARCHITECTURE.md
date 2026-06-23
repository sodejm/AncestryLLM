# Architecture

## Goals

- Local-first genealogy analysis with optional cloud OCR.
- Strict read-only protection for RootsMagic databases.
- Minimal operational complexity for contributors.

## System Components

1. Runtime bootstrap: [tools/bootstrap.py](tools/bootstrap.py)
- Detects OS.
- On macOS, ensures Homebrew + Ollama setup.
- Starts Docker Compose stack.

2. Web interface: [docker-compose.yml](docker-compose.yml)
- Runs Open WebUI on port 3000.
- Mounts family tree data read-only (`:ro`).
- Supports optional BYOK passthrough for OpenAI and Anthropic.

3. SQL router: [tools/sql_router.py](tools/sql_router.py)
- Discovers `.rmtree` files from `/app/backend/data/family_trees`.
- Resolves tree paths safely (no directory traversal).
- Opens SQLite in read-only mode (`mode=ro`).
- Uses pooled SQLAlchemy engine args for low-overhead access.

4. OCR preprocessor + cloud mapping: [tools/gemini_transcription.py](tools/gemini_transcription.py)
- Normalizes OCR text (whitespace/duplicate/non-ASCII pruning).
- Sends compact input to Gemini only when API key is present.

## Data Safety Model

RootsMagic `.rmtree` files are SQLite databases and may be corrupted by unsafe
writes during live usage. This project enforces immutable access:

- Container volume mount is read-only.
- Router URI is explicit read-only SQLite mode.
- No write paths or mutating SQL flow are implemented.

## Security Guardrails

- Local pre-commit hooks with gitleaks and private-key detection.
- `.gitignore` excludes `.env`, virtual environments, caches, and genealogy DB files.
- CI pipeline runs tests + `pip-audit` + `semgrep`.
- Dependabot updates pip and GitHub Actions dependencies.

## Test Strategy

- Unit tests in [tests](tests) cover bootstrap behavior, router logic, and OCR preprocessing.
- CI executes `pytest --verbose` on all pushes and PRs to `main`.

## Operational Limits

- `OLLAMA_NUM_CTX` default is bounded (`8192`) to reduce memory pressure.
- SQL agent result size is bounded via `SQL_AGENT_TOP_K`.
