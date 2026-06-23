# Contributing

Thanks for helping improve this project.

## Development Setup

1. Fork and clone your fork.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Install git hooks.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pre-commit
pre-commit install
```

## Branching Workflow

1. Sync your fork with upstream `main`.
2. Create a feature branch from `main`.

```bash
git checkout main
git pull origin main
git checkout -b feature/short-description
```

## Coding Standards

- Keep changes small and focused.
- Add or update tests for behavioral changes.
- Do not commit secrets or personal genealogy data.

## Test Before Push

Run both local checks before opening a PR:

```bash
pre-commit run --all-files
pytest --verbose
```

## Pull Request Checklist

Before opening a pull request, ensure:

- Your branch is rebased on latest `main`.
- Tests pass locally.
- Security checks pass (`pre-commit` includes gitleaks).
- Documentation is updated for behavior or configuration changes.
- PR description includes: summary, rationale, risk, and test evidence.

## Commit Guidance

Use clear, imperative commit messages, for example:

- `Add read-only guard for SQLite router`
- `Harden docker-compose runtime security`
- `Add regression test for missing tree handling`
