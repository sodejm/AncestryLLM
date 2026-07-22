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
