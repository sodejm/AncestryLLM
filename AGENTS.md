# Repository Guidelines

## Structure

- `src/ancestryllm/` is the installable application package.
- `tests/` contains pytest tests and fictional fixtures.
- `docs/` contains architecture, privacy, provider, console, GEDCOM, backup,
  security-response, and threat-model guidance.
- `family_trees/` is local-only; never commit its contents.

## Commands

Use `make setup`, `make test`, `make lint`, `make typecheck`, and `make security`.
Run the app with `.venv/bin/ancestry`; no arguments open the console.

## Engineering and data safety

Write typed Python, keep adapters thin, return serializable service DTOs, and use
stable coded errors. Add regression tests for every behavior change. Treat
RootsMagic files as immutable and GEDCOM as loss-minimal. Provider `none` must
remain network-free even when environment keys exist.

Never commit credentials, `.env`, real genealogy records, databases, backups,
reports, logs, or prompt/response payloads. Secrets go through the OS-keyring
service; environment injection is headless/CI fallback only. Do not auto-load
`.env`. Cloud calls require explicit provider selection and consent.


# Git and Workflow Rules

## Branch and Worktree Isolation
* ALWAYS isolate every new feature, task, or bugfix into its own dedicated Git branch.
* NEVER make modifications, apply patches, or commit directly to the `main` or `master` branch.
* If a task is started, immediately check out a new branch before executing any file modifications.

## Branch Naming Convention
* Format branch names dynamically based on the task description or issue number:
  `feature/short-description` or `bugfix/issue-number-short-description`
* Do not use generic names like `temp-branch` or `patch`.

## Local Execution Workflow
1. Read and understand the assigned issue.
2. Run `git checkout -b <new-branch-name>` from the updated base branch.
3. Apply code modifications only after the new branch is verified active.
4. Stage, commit, and prepare the branch for review or pushing.

## Commit Messaged
1. Minimum 1 sentence of what was changed
2. If multiple changes, list the major changes in bullets.
3. When a commit or PR resolves a GitHub issue, include the appropriate closing keyword (for example, `Fixes #123`) so GitHub links and closes that issue when the work is merged.
