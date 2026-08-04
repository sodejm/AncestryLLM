# Security response checklist

This runbook applies to every suspected vulnerability report, including one
first posted publicly. Move a public report into a private GitHub Security
Advisory immediately and do not continue discussing sensitive details in the
public thread. The repository maintainer is the incident lead and owns release
and disclosure coordination. Name a separate security reviewer whenever the
risk policy requires independent review.

## Establish a confidential workspace

1. Move the report into a private GitHub Security Advisory and restrict all
   evidence to people who need access.
2. Remediate in the advisory's private fork or a separate access-controlled
   private repository. An unpushed local branch may be used for initial
   investigation if the workstation is appropriately protected. Use a
   `hotfix/*` branch for a high-impact critical production defect and a
   `bugfix/*` branch for other defects; keep the branch description
   non-sensitive.
3. Never push a remediation branch or open a pull request in the public
   repository until disclosure is coordinated. A branch in a public repository
   is public even when it is named `private` or has no pull request.

## Triage and contain

4. Contain the issue without reproducing sensitive content in logs, issues,
   command output, test fixtures, commits, or chat.
5. Record affected data, provider and endpoint, version, reachable attack path,
   exploitability, severity, and affected release or capability gate.
6. Revoke exposed credentials and consent grants, stop affected provider
   traffic, and preserve only sanitized evidence needed to reproduce and verify
   the issue.
7. If repository history or a released artifact contains private data,
   coordinate removal with repository administration before rewriting history,
   and identify any people who require notification.

## Remediate and verify

8. Make the narrowest complete fix and add a regression test that uses only
   fictional data. Verify that legitimate offline, provider-consent, encrypted
   storage, RootsMagic, and GEDCOM behavior remains intact as applicable.
9. Run the relevant targeted tests, then the canonical repository gates:
   `make test`, `make lint`, `make typecheck`, and `make security`. Record exact
   results. An interrupted security scan is incomplete.
10. Inspect commits, logs, generated reports, scan artifacts, SBOM changes, and
    the final diff for secrets or personal data before any public publication.

## Database-key incidents

- If an existing workspace key is missing, restore the matching key from a
  secure backup; never generate a replacement key for that database.
- If a workspace key was disclosed, isolate the affected workspace and record
  the confidentiality impact. There is currently no supported public in-place
  rekey or migration command, so do not mark the key as rotated or improvise a
  destructive migration. A reviewed migration or recovery procedure remains a
  remediation blocker.

## Disposition and disclosure

11. Critical or High residual risk cannot be accepted for an affected
    privileged, MVP, high-risk-capability, or distribution gate. Close it with
    a verified fix or avoidance, or an evidence-backed false-positive
    disposition.
12. Apply the owner, independent-review, evidence, rationale, compensating
    control, decision-date, and expiry requirements in
    [the threat model](THREAT_MODEL.md#risk-decision-and-expiry-policy) to any
    permitted Medium or Low risk acceptance.
13. Publish a concise advisory, affected-version statement, upgrade or
    mitigation guidance, and any required threat-model or control-matrix update
    only after the reporter and maintainer coordinate disclosure.

Response is complete only when every finding has a permitted disposition, no
applicable release gate or expired exception remains open, verification evidence
is recorded, and public guidance contains no sensitive data.
