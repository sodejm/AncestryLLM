# Repository Guidelines

## Project Structure

- `tools/` contains the Python application and CLI modules: `sql_router.py` for read-only RootsMagic SQLite access, `gemini_transcription.py` for optional OCR, `bootstrap.py` for startup, and GEDCOM tooling such as `gedcom_merge.py`.
- `tests/` contains pytest tests, with fixtures and test data kept alongside the relevant test suites.
- `family_trees/` is the local input directory for `.rmtree` files; do not commit personal genealogy data.
- `scripts/` contains onboarding and diagnostics helpers. `docker-compose.yml`, `.env.example`, and `requirements.txt` define runtime configuration and dependencies.

## Build, Test, and Development Commands

Use a virtual environment for local work:

```bash
make setup                 # create .venv and install dependencies
make quickstart            # diagnose, set up, and start the stack
make test                  # run pytest --verbose
make lint                  # run all pre-commit checks
make security              # run Semgrep, pip-audit, Trivy, and Gitleaks
python -m tools.bootstrap  # start services directly
```

Install hooks with `make hooks`; run `make doctor` to validate prerequisites. Use `pytest --verbose` for focused test runs, for example `pytest tests/test_router.py -q`.

## Coding Style and Naming

Write clear, typed Python using four-space indentation, PEP 8-compatible formatting, descriptive docstrings, and explicit error handling. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep imports organized and avoid unrelated refactors. `pre-commit` enforces whitespace, file-format, merge-conflict, large-file, and secret checks; use `ruff check .` when changing Python code.

## Testing Guidelines

Tests use pytest and `unittest.mock` for external services and system commands. Name files `test_<area>.py` and functions `test_<behavior>`. Add regression coverage for behavioral changes, especially read-only database access, missing configuration, CLI failures, and GEDCOM preservation. Run the relevant focused tests first, then `make test`; run lint and security checks before submitting.

## Commit and Pull Requests

Use concise, imperative commit subjects, optionally with a conventional prefix such as `refactor:` or `Fix CI:` (example: `Add read-only guard for SQLite router`). Keep commits focused. Pull requests should explain the summary, rationale, risk, and test evidence; update documentation for configuration or behavior changes. Confirm tests, `make lint`, and `make security` pass, and never include secrets, API keys, or private family-tree files.

## Security and Data Safety

Treat `.rmtree` files as immutable: the router and Docker volume must remain read-only. Keep credentials in an untracked `.env` copied from `.env.example`. Report suspected secret exposure or genealogy-data leakage immediately and do not reproduce sensitive values in issues or logs.
