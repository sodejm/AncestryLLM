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

1. Contain the issue without reproducing sensitive content in logs, issues,
   command output, test fixtures, commits, or chat.
2. Record affected data, provider and endpoint, version, reachable attack path,
   exploitability, severity, and affected release or capability gate.
3. Revoke exposed credentials and consent grants, stop affected provider
   traffic, and preserve only sanitized evidence needed to reproduce and verify
   the issue.
4. If repository history or a released artifact contains private data,
   coordinate removal with repository administration before rewriting history,
   and identify any people who require notification.

## Remediate and verify

1. Make the narrowest complete fix and add a regression test that uses only
   fictional data. Verify that legitimate offline, provider-consent, encrypted
   storage, RootsMagic, and GEDCOM behavior remains intact as applicable.
2. Run the relevant targeted tests, then the canonical repository gates:
   `make test`, `make lint`, `make typecheck`, and `make security`. Record exact
   results. An interrupted security scan is incomplete.
3. Inspect commits, logs, generated reports, scan artifacts, SBOM changes, and
   the final diff for secrets or personal data before any public publication.

## Database-key incidents

- If an existing workspace key is missing, restore the matching key from a
  secure backup; never generate a replacement key for that database.
- If a workspace key was disclosed, isolate the affected workspace and record
  the confidentiality impact. There is currently no supported public in-place
  rekey or migration command, so do not mark the key as rotated or improvise a
  destructive migration. A reviewed migration or recovery procedure remains a
  remediation blocker.

## Desktop diagnostic evidence

- Prefer the stable event code, application version, and normalized platform
  labels shown by the Diagnostics workspace. Do not request genealogy content,
  credentials, host details, raw process output, or environment dumps.
- Local component files are bounded structural evidence, not a complete audit
  trail. Their writers can fail by design so that security and shutdown remain
  authoritative. Never infer a successful shutdown from diagnostic JSON; use
  the separately verified shutdown receipt.
- There is no automatic upload or export. If a confidential investigation needs
  the local files, have the user inspect them first, transfer them only through
  the approved confidential workspace, limit access and retention, and clear
  them when the incident need ends. Never attach unreviewed records publicly.

## Disposition and disclosure

1. Critical or High residual risk cannot be accepted for an affected
    privileged, MVP, high-risk-capability, or distribution gate. Close it with
    a verified fix or avoidance, or an evidence-backed false-positive
    disposition.
2. Apply the owner, independent-review, evidence, rationale, compensating
    control, decision-date, and expiry requirements in
    [the threat model](THREAT_MODEL.md#risk-decision-and-expiry-policy) to any
    permitted Medium or Low risk acceptance.
3. Publish a concise advisory, affected-version statement, upgrade or
    mitigation guidance, and any required threat-model or control-matrix update
    only after the reporter and maintainer coordinate disclosure.

Response is complete only when every finding has a permitted disposition, no
applicable release gate or expired exception remains open, verification evidence
is recorded, and public guidance contains no sensitive data.
