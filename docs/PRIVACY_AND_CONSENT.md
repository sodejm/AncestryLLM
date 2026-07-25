# Privacy and consent

Local encrypted research is allowed for living and possibly living people.
Cloud disclosure and portable sharing are denied unless an active consent
profile explicitly permits the required data classes. Prefer excluding living
people; redaction is available where a workflow must preserve graph shape.

Consent is provider-specific and revocable. It restricts modules, purposes,
models, data classes, retention, and budget. The cloud policy runs before prompt
rendering, minimizes fields, labels untrusted genealogy text, and refuses a
request that exceeds the grant. LLM run metadata is stored by default; full
input/output is stored only with explicit retention consent in SQLCipher.

The research workspace is curated supporting data, not the authoritative family
tree. Store provenance and RootsMagic/GEDCOM identifiers so claims can be traced
without copying an entire tree into the workspace.

## Desktop privacy boundary

The accepted desktop design is
[ADR-0025](ADR-0025-electron-fastapi-desktop.md). It applies OWASP Top 10:2025,
applicable OWASP ASVS 5.0.0 requirements, and NIST SP 800-218 secure-development
practices. The desktop is not a browser service: it launches with no in-app
authentication for the signed-in OS user, and its internal API binds only to
loopback with per-launch authentication.

The sandboxed renderer is untrusted. It must never receive a provider or
SQLCipher secret, keyring value, internal API bearer/port, unrestricted path,
raw crash data, or direct database/provider/filesystem/network capability.
Secret operations are set, delete, and presence only; Python `SecretStore` and
the OS keyring remain the sole authority. File choices become scoped opaque
grants, not renderer-visible paths.

Desktop capability discovery uses non-sensitive metadata. It must not probe
private files, databases, keyrings, people, providers, or networks. Local,
remote, sensitive, destructive, and Post-MVP states use text and icons as well
as color. The accessible degraded diagnostics state contains stable codes and
recovery steps, never genealogy values, prompts/responses, secrets, paths, or
bootstrap material.

`provider=none` remains network-free even when environment credentials or SDKs
exist. Remote UI state cannot grant consent or select a provider by itself;
Python policy verifies the provider, model, purpose, data classes, retention,
and current consent before any disclosure. Model output and Markdown remain
untrusted display data and cannot gain tools or renderer privileges.


## Interactive console privacy

The only supported interactive console is the prompt-toolkit/Rich REPL. It uses
the same command specifications, provider policy, and consent checks as one-shot
CLI execution; there is no separate interactive path that can bypass consent or
provider selection. Session options are non-secret, secret-like option names are
rejected, and secret entry must go through no-echo `secrets` commands backed by
the OS-keyring service.

Completion is privacy-filtered and read-only. It may use command metadata,
static enum values, enabled module names, startup snapshots of configured
profile and consent names, static secret-reference types, and bounded local file
listings for file-valued arguments. It must not query databases, keyrings,
providers, networks, people, trees, prompts, workspaces, prompt names, or secret
values.

Interactive history is stored with owner-only permissions. Secret entry and
secret-like commands are excluded from history and defensively redacted from
persisted history. Do not paste credentials, private genealogy records, or
prompt/response payloads into ordinary commands.
