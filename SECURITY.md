# Security policy

Report vulnerabilities privately through GitHub Security Advisories. Do not put
credentials, genealogy records, exploit payloads containing personal data, or
database keys in an issue. Include affected version, reproduction with fictional
data, impact, and suggested mitigation.

Supported releases are the current `main` branch and latest tagged release.
Critical and High findings block a release until fixed or explicitly triaged and
documented by a maintainer. Absolute absence of vulnerabilities is not claimed.

Security boundaries include the OS keyring, SQLCipher database, consent policy,
immutable RootsMagic reader, structured-output validation, endpoint allowlisting,
atomic GEDCOM output, dependency lock, secret scanning, Semgrep, dependency
audit, CodeQL, and repository artifact guard. See [the threat model](docs/THREAT_MODEL.md).

If sensitive data is accidentally committed: stop sharing, revoke exposed
credentials, rotate the database key only through a documented migration,
remove the artifact from history with coordinated repository administration,
and notify affected people as applicable. Never paste the leaked value into a
ticket or chat while responding.
