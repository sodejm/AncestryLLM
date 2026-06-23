# Copilot Instructions

## Project Focus

This repository is local-first and privacy-sensitive. Prefer safe defaults over
feature breadth when uncertain.

## Non-Negotiable Rules

- Never introduce write paths to RootsMagic `.rmtree` files.
- Keep `family_trees` mounted read-only in Compose.
- Never hardcode credentials, tokens, or API keys.
- Preserve existing security hooks and CI checks.

## Coding Expectations

- Add tests for behavior changes in [tests](../tests).
- Keep changes small and focused.
- Use clear error messages that aid human troubleshooting.
- Maintain cross-platform compatibility where practical.

## Data and Security

- Treat genealogy files as private personal data.
- Respect `.gitignore` boundaries for `.env`, `.rmtree`, `.venv`, and caches.
- If configuration changes affect security posture, update [SECURITY.md](../SECURITY.md) and [README.md](../README.md).

## PR Quality Bar

A PR should include:

- What changed and why.
- Risk assessment.
- Test evidence (`pytest --verbose`).
- Security impact notes if relevant.
