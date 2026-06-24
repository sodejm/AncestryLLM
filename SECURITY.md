# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities **privately** using GitHub's
[Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
feature for this repository (the **Security** tab → **Report a vulnerability**).

Please do **not** open a public issue for security reports.

## Handling Sensitive Data

This is a local-first project. To keep your data and credentials safe:

- **Never commit `.env`.** Only `.env.example` (with empty placeholders) belongs
  in version control. Real API keys must stay local.
- Local storage paths such as `FAMILY_TREES_HOST_DIR` and
  `OPEN_WEBUI_DATA_DIR` are configured through `.env`; keep those paths local
  and keep the RootsMagic mount read-only.
- **Never commit `.rmtree` databases.** RootsMagic files contain private,
  personally identifiable genealogy data and are ignored by `.gitignore`.
- API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) are read
  from environment variables at runtime and are never hardcoded.

## Automated Safeguards

- `.gitignore` excludes secrets, virtual environments, caches, and all
  genealogy databases.
- `.pre-commit-config.yaml` runs `gitleaks` and `detect-private-key` before
  every commit to block accidental secret disclosure.
- Continuous integration runs `pip-audit` and `semgrep` on every push and pull
  request.

## Supported Versions

The latest commit on the `main` branch is the supported version.
