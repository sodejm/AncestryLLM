# Security response checklist

1. Contain without reproducing sensitive content in logs, issues, or chat.
2. Classify affected data, provider, version, exploitability, and severity.
3. Revoke credentials and consent grants; preserve sanitized evidence.
4. Fix on a private branch and add a fictional-data regression test.
5. Run tests, Ruff, mypy, Semgrep, dependency audit, secret scan, and SBOM diff.
6. Coordinate history rewriting and user notification if private data escaped.
7. Publish a concise advisory, upgrade guidance, and control-matrix update.

Critical and High issues remain release blockers until disposition is documented.
