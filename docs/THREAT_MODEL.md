# Data-flow threat model and control matrix

## Assets and trust boundaries

Sensitive assets are genealogy records, living-person status, notes, provider
credentials, SQLCipher keys, prompts/responses, consent grants, and RootsMagic
source files. Data crosses boundaries at prompt-toolkit/Rich REPL input,
one-shot CLI input, GEDCOM/RootsMagic parsing,
the OS keyring, encrypted database, configured provider endpoints, and exported
files. The local operator is trusted to choose data and consent; imported
genealogy content and every model response are untrusted.

```text
untrusted GEDCOM / RootsMagic -> bounded parsers -> application services
                                                 |-> SQLCipher workspace <- OS keyring
                                                 |-> consent + minimization -> LLM HTTPS
                                                 +-> atomic local exports
```

## OWASP Top 10:2025

| Risk | Applicability and controls | Verification |
|---|---|---|
| A01 Broken Access Control | Single-user local v1; paths are scoped, module registry is explicit, RootsMagic is immutable. Future API must add authentication/authorization. | Path traversal and disabled-module tests. |
| A02 Security Misconfiguration | Secure defaults, no network provider by default, prompt-toolkit/Rich REPL as the only interactive console, no shell/redirection, restrictive permissions, bounded limits. | Clean-install and console tests. |
| A03 Software Supply Chain Failures | Locked dependencies, optional provider extras, Dependabot, audit, SBOM, pinned CI actions. | `uv lock`, `pip-audit`, CycloneDX. |
| A04 Cryptographic Failures | SQLCipher required; 256-bit random key in OS keyring; plaintext and wrong keys rejected; encrypted backups. | Header, wrong-key, integrity, backup tests. |
| A05 Injection | SQL AST validation, allowlisted schema, SQLite authorizer; no generated command/code execution; prompt content delimited as data; REPL parsing is strict and transport-neutral. | SQL/prompt/console injection tests and Semgrep. |
| A06 Insecure Design | Separate adapters/services, explicit consent, explicit module registry and command specifications, data minimization, conservative deletions, threat review. | Architecture and invariant tests. |
| A07 Authentication Failures | Not applicable to the single-user local adapter; OS keyring supplies platform authentication. API remains out of scope. | Future-API release blocker. |
| A08 Software or Data Integrity Failures | Source hashes, SQLCipher integrity, validated structured output, immutable prompt revisions, atomic writes. | Hash, schema, round-trip, rollback tests. |
| A09 Security Logging and Alerting Failures | Stable error codes and privacy-minimal run metadata; payload logging off; secret redaction. Local v1 has no remote alerting. | Error/redaction tests; documented limitation. |
| A10 Mishandling of Exceptional Conditions | Fail-closed provider/storage policy, timeouts, bounded reads, atomic output, safe rollback. | Timeout, malformed input, keyring failure tests. |

## OWASP Top 10 for LLM Applications 2025

| Risk | Controls and disposition |
|---|---|
| LLM01 Prompt Injection | Imported text is untrusted data; models receive no tools; generated SQL is parsed and authorizer-enforced. |
| LLM02 Sensitive Information Disclosure | Pre-render consent, minimal fields, living-person denial, OS keyring, encrypted optional retention. |
| LLM03 Supply Chain | Provider SDKs are optional and locked; dependency/SBOM/security scans gate release. |
| LLM04 Data and Model Poisoning | Retrieval is not implemented. Any future local index must fingerprint sources, preserve provenance, treat retrieved text as untrusted context, and detect stale/conflicting material before display or generation. |
| LLM05 Improper Output Handling | JSON Schema validation and length caps; output is never executable. |
| LLM06 Excessive Agency | No autonomous agents, tool calls, shell, interactive-console escape hatch, write-capable SQL, or automatic destructive decisions. |
| LLM07 System Prompt Leakage | Prompts contain no credentials; templates and untrusted content are separated; disclosure is treated as possible. |
| LLM08 Vector and Embedding Weaknesses | Embeddings/vector stores remain unimplemented. A future feature requires SQLCipher-local storage by default, workspace and consent partitioning, restricted-data exclusion, versioned invalidation, bounded retrieval, and explicit cloud-retention consent. |
| LLM09 Misinformation | Deterministic evidence remains authoritative; LLM adjudication is optional and cannot delete conflicts. |
| LLM10 Unbounded Consumption | Token, output, timeout, cost, model, purpose, and row caps are enforced. |

## Release decision

A release requires zero known untriaged Critical/High findings. Every relevant
finding must link to a fix, accepted-risk rationale with owner/expiry, or false
positive evidence. Manual Ancestry/Geni/MyHeritage import results and any control
exceptions must be recorded in release notes. This model does not prove absence
of vulnerabilities; it defines required evidence and fail-closed boundaries.
