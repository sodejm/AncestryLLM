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
* For GitHub issue work, create a dedicated branch named for the issue before making changes (for example, `bugfix/123-export-validation`).

## Issue and Branch Lifecycle
* Link the GitHub issue in the PR, and use a closing keyword such as `Fixes #123` in the PR description or commit message when the work fully resolves it. Verify that the issue is closed after the PR merges.
* After a PR is merged or closed, delete its unused branch only after verifying it has no unique unmerged commits. Keep active branches, branches with open PRs, and branches that contain work not merged into the target branch.

## Local Execution Workflow
1. Read and understand the assigned issue.
2. Run `git checkout -b <new-branch-name>` from the updated base branch.
3. Apply code modifications only after the new branch is verified active.
4. Stage, commit, and prepare the branch for review or pushing.

## Model and Reasoning Selection
* Choose the model and reasoning effort for the problem's complexity, correctness risk, and latency needs; do not treat a higher effort as a substitute for selecting an appropriate model.
* Leave model selection automatic when it is sufficient. When explicitly selecting, use `gpt-5.6` for demanding, ambiguous, multi-step work and `gpt-5.6-terra` for fast, lower-cost exploration or read-heavy support work.
* Use low effort for obvious, low-risk edits; medium as the default for ordinary engineering; high for complex logic, review, or edge cases; and xhigh, max, or ultra only when supported and warranted by high-stakes correctness, security, migration, or data-integrity risk.

## Commit Messaged
1. Minimum 1 sentence of what was changed
2. If multiple changes, list the major changes in bullets.
