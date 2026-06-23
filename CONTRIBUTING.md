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

## Linting Best Practices

Use automated checks locally before every push:

1. Run repository linting and formatting hooks:

```bash
make lint
```

2. If you add Python code, also run static analysis locally (recommended):

```bash
pip install ruff
ruff check .
```

Best-practice expectations:

- Keep imports organized and remove dead code.
- Prefer explicit error handling and actionable error messages.
- Avoid large refactors without accompanying tests.

## Security Testing Best Practices

Run security checks before opening or updating a PR:

```bash
make security
```

This executes:

- `semgrep` for SAST logic and code security checks.
- `pip-audit` for known package vulnerabilities.
- `trivy config` for IaC misconfiguration scanning.
- `gitleaks` for credential/secret detection.

Treat any security finding as blocking until resolved or explicitly documented.

## Test Before Push

Run local quality checks before opening a PR:

```bash
make lint
pytest --verbose
make security
```

## Pull Request Checklist

Before opening a pull request, ensure:

- Your branch is rebased on latest `main`.
- Tests pass locally.
- Linting and formatting checks pass (`make lint`).
- Security checks pass (`make security`).
- Documentation is updated for behavior or configuration changes.
- PR description includes: summary, rationale, risk, and test evidence.

## Commit Guidance

Use clear, imperative commit messages, for example:

- `Add read-only guard for SQLite router`
- `Harden docker-compose runtime security`
- `Add regression test for missing tree handling`
