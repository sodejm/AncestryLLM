# Security policy

## Report a vulnerability privately

Use a
[private GitHub Security Advisory](https://github.com/sodejm/AncestryLLM/security/advisories/new).
Do not put credentials, genealogy records, database keys, private prompts or
responses, or exploit payloads containing personal data in a public issue,
discussion, pull request, or chat.

Include the affected version, a minimal reproduction using fictional data, the
security impact, and any conditions required to reach the issue. A suggested
mitigation is helpful but optional. Please coordinate public disclosure through
the advisory until a fix and upgrade guidance are ready. The maintainer will use
the advisory to triage the report, request sanitized evidence, and coordinate
remediation and disclosure.

If the advisory form is unavailable, use the repository owner's verified GitHub
profile only to request a private channel. Do not include vulnerability details
or sensitive data in that request.

## Supported versions

| Version | Security status |
|---|---|
| `main` (unreleased development) | Reports accepted; not a stable release |
| `0.4.x` | Supported stable release line |
| Earlier than `0.4.0` | Unsupported; reproduce on a supported version when possible |

Update this table whenever the stable release line changes.

## Current supported attack surface

AncestryLLM is currently a local, single-user application with a one-shot CLI
and a prompt-toolkit/Rich REPL. Its supported attack surface includes local
GEDCOM and RootsMagic files, the SQLCipher workspace and OS credential store,
generated local artifacts, configuration, and explicitly selected local or
remote LLM providers.

FastAPI, Electron, browser renderers, multi-user service operation, plugins,
automatic updating, and vector retrieval are not implemented or supported
runtime surfaces. Their roadmap controls in
[the threat model](docs/THREAT_MODEL.md) are design requirements, not evidence
of an effective control and not a reason to reduce current risk.

The local operator is trusted to choose files, provider profiles, and consent.
Imported GEDCOM and RootsMagic content, prompt variables, OCR text, provider
output, and external snapshots are untrusted data. RootsMagic and ordinary
GEDCOM files remain genealogy source artifacts; the SQLCipher workspace stores
supporting research and policy state, not a replacement family tree. Model
output is never an authority for genealogy facts.

## Implemented security invariants

- Secrets use the OS keyring. Protected environment injection is limited to
  headless or CI use; AncestryLLM never auto-loads `.env`, and the presence of a
  key never selects a provider.
- The application requires SQLCipher and fails closed for plaintext databases,
  missing or wrong keys, and failed integrity checks. It does not silently
  create a replacement key for an existing workspace.
- Cloud calls require an explicitly selected provider profile and consent for
  the exact provider, endpoint, purpose, module, model, and data classes.
  `provider=none` remains network-free. Remote endpoints require the documented
  HTTPS and hostname policy.
- RootsMagic sources are opened through the immutable, bounded read boundary.
  GEDCOM operations do not overwrite their inputs, preserve unsupported source
  structure where possible, and validate output. Incremental GEDCOM updates
  stage and atomically publish complete release bundles.
- Provider and model output is untrusted data, is schema-validated when a
  structured result is required, and is never executed. An LLM receives no
  shell, SQL, filesystem, or other tool capability.
- Real genealogy records, databases, backups, reports, logs, credentials, and
  prompt or response payloads must not be committed to the repository. Tests
  and reproductions use fictional data.

A realistic path that violates one of these invariants, exposes or corrupts
private genealogy data, bypasses provider consent, causes unauthorized network
or provider spending, or compromises build and release integrity is in security
reporting scope. Genealogy loss or corruption is security-relevant when
untrusted input or a trust-boundary failure can cause it. No vulnerability class
is excluded by default. A roadmap-only design gap is not a current
vulnerability unless it also reaches implemented code, data, contracts, or
release artifacts.

## Finding disposition and release gates

Critical or High residual risk cannot be accepted for an affected privileged,
MVP, high-risk-capability, or distribution gate. Such a finding requires a
verified fix or avoidance, or an evidence-backed false-positive disposition;
triage notes alone do not unblock the gate.

Medium residual risk may be accepted only by the repository maintainer and a
security reviewer other than the implementer. Low residual risk that affects a
trust boundary still needs an accountable owner and recorded rationale. Every
acceptance records the risk and evidence, rationale, compensating controls,
owner, independent reviewer, decision and review dates, and an expiry no later
than the next release or 90 days. Expired exceptions fail the gate. The complete
rules are in [the threat model](docs/THREAT_MODEL.md#risk-decision-and-expiry-policy).

## Assurance evidence and known limitations

Dependency locking, secret scanning, Semgrep, dependency audit, CodeQL, and the
repository artifact guard provide development and release evidence. They are
not runtime trust boundaries, and their presence or a passing scan does not
prove that vulnerabilities are absent. A stopped or interrupted security scan
is incomplete.

Repository setup, CI, and release jobs use the same
[verified uv bootstrap](docs/security/verified-uv-bootstrap.md). The reviewed
schema-v1 policy binds the supported platform and architecture to exact
GitHub CLI and `uv` release archives, hashes, source identity, signer workflow,
OIDC issuer, and SLSA provenance predicate. Unknown or mismatched inputs fail
before either tool may execute; cached `uv` binaries are re-hashed before use,
and release evidence requires a sanitized successful receipt. This control is
specific to the repository's `uv` bootstrap and does not establish trust in
unrelated deployment or application update channels.

Current limitations that must not be credited as controls include:

- there is no public in-place SQLCipher migration or rekey command;
- `max_cost_usd` is persisted but is not enforced, so provider-side spending
  limits remain necessary;
- RootsMagic schema and live-file coverage are intentionally incomplete;
- GEDCOM interoperability still needs importer smoke evidence for supported
  Ancestry, Geni, and MyHeritage workflows; and
- several GEDCOM synchronization and recovery paths need broader end-to-end
  evidence.

See [the authoritative architecture](ARCHITECTURE.md) for current implementation
and evidence status. Absolute absence of vulnerabilities is not claimed.

## Accidental sensitive-data exposure

Stop sharing the artifact, preserve sanitized evidence, revoke exposed
credentials or consent grants, and coordinate any history rewrite and affected
user notification privately. Never paste the leaked value into a ticket or chat
while responding.

For an exposed SQLCipher key, do not claim that rotation is available or
generate a replacement key for an existing workspace: the project has no
supported in-place rekey workflow. Isolate the affected workspace and treat a
reviewed migration or recovery procedure as an unresolved remediation blocker.
Follow the [security response checklist](docs/SECURITY_RESPONSE.md).
