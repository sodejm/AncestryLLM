# Privacy and consent

Local encrypted research is allowed for living and possibly living people.
Cloud disclosure and portable sharing are denied unless an active consent
profile explicitly permits the required data classes. Prefer excluding living
people; redaction is available where a workflow must preserve graph shape.

Consent is profile/endpoint-specific and revocable. It restricts providers,
modules, purposes, models, data classes, retention, and budget. The cloud policy
runs before adapter or SDK use, minimizes fields, labels untrusted genealogy
text, and refuses a request that exceeds the grant. LLM run metadata is stored
by default; full input/output is stored only with explicit retention consent in
SQLCipher.

An operational profile may opt deterministic structured requests into a
bounded exact-result cache. Cache content remains in process memory only, is
partitioned by workspace process and consent ID using a process-random HMAC
key, and is removed by TTL/LRU expiry or application shutdown. Cache hits add
privacy-minimal audit metadata but do not persist an additional prompt or
response payload.

An Ollama endpoint is local only when it explicitly names loopback. Any
non-loopback Ollama endpoint requires HTTPS and the same exact profile-bound
consent as another remote route. Supplying a retention consent to a local route
also validates the profile, module, purpose, data classes, and model before any
payload may be retained.

GEDCOM identity adjudication always declares
`possibly_living_person`, even when both candidate people have recorded death
dates. Its bounded comparison context can include partners, parents, and
children whose living status is unknown, so deceased-person consent alone can
never authorize that remote disclosure.

The research workspace is curated supporting data, not the authoritative family
tree. Store provenance and RootsMagic/GEDCOM identifiers so claims can be traced
without copying an entire tree into the workspace.

RootsMagic inputs remain immutable sources, and GEDCOM import and export retain
the project's loss-minimal behavior. Encrypted backups provide the recovery
boundary without turning a copied tree into a new authority. For the exact
operational and format contracts, see the
[provider reference](../reference/PROVIDERS.md),
[GEDCOM compatibility reference](../reference/GEDCOM_COMPATIBILITY.md),
[encrypted-backup guide](../ENCRYPTED_BACKUPS.md), and
[data-flow threat model](../THREAT_MODEL.md). Those pages own lookup details,
procedures, compatibility guarantees, and security controls; this page explains
why the boundaries exist.

## Desktop privacy boundary

The accepted desktop design is
[ADR-0025](../ADR-0025-electron-fastapi-desktop.md). It applies OWASP Top 10:2025,
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

The unreleased Issue #105 source contract exposes only the reviewed non-secret
settings schema and the exact credential references owned by `SecretStore`.
Credential status is limited to `present`, `missing`, or `unavailable`; no
route, bridge response, mock fixture, or renderer state can read a value.
Environment-injected credentials remain a read-only headless/CI input, and a
desktop set or delete attempt fails closed until that injection is removed.
A successful delete is reported only after the keyring confirms absence.

The renderer uses an uncontrolled password field and clears its local value
before every set request and again after every success or failure. It retains
status only. Electron `safeStorage`, `localStorage`, IndexedDB, plaintext
configuration, and any parallel Node-side credential store are not permitted.
Editing a default-provider setting does not activate a provider or grant cloud
consent; the normal explicit provider and consent checks still apply.

Unreleased Issue #108 adds the explicit desktop administration path for those
checks. Provider endpoints must be tested before profile save, and the test
returns only a redacted destination digest. A consent preview lists the exact
provider, profile, model, allowed modules, purposes, data classes, retention,
warnings, and optional budget before a grant can be created. Living-person data
and remote retention receive explicit warnings. Creation requires the current
optimistic revision and the exact preview; revocation is a separate explicit
action. The surface does not execute a provider request or genealogy workflow.

Unreleased Issue #110 adds a separate source-level transient-chat execution
boundary behind the authenticated private API. It accepts only an exact stored
profile and model, rejects direct or ambient provider selection, and rechecks
endpoint, credential, policy, and current consent before every run. Its fixed
system instruction treats all user content as untrusted, grants no tools or
file, database, shell, plugin, genealogy, or autonomous authority, and labels
provider output as advisory rather than evidence. Content is bounded and held
only in process memory; failed runs retain nothing, deletion and shutdown clear
the session, and audit records contain only identifiers, counters, usage, and
one-way payload hashes. No Electron chat surface, streaming transport, or safe
renderer presentation is included yet.

Desktop capability discovery uses non-sensitive metadata. It must not probe
private files, databases, keyrings, people, providers, or networks. Local,
remote, sensitive, destructive, and Post-MVP states use text and icons as well
as color. The accessible degraded diagnostics state contains stable codes and
recovery steps, never genealogy values, prompts/responses, secrets, paths, or
bootstrap material.

`provider=none` remains network-free even when environment credentials or SDKs
exist. Renderer state alone cannot grant consent or select a provider; the
fixed desktop consent route accepts only a current, exact preview, and Python
policy verifies the endpoint identity, profile, provider, model, purpose, data
classes, retention, and active consent again before any disclosure. Model
output and Markdown remain untrusted display data and cannot gain tools or
renderer privileges. The source-level chat service does not weaken this rule:
it rejects `provider=none` for generation while leaving the offline profile
socket-free and cannot be reached through the renderer.

## Deployment-profile privacy boundary

The unreleased source implements the versioned, non-secret deployment-profile
control plane accepted by
[ADR-0026](../ADR-0026-local-first-container-remote-deployment.md). Local Desktop
is the safe default and recommended choice. Connect Remote and Host Remote are
advanced intents whose enrollment and hosting runtimes are not implemented or
supported. An absent profile migrates to Local Desktop; malformed, stale,
downgraded, or mismatched state fails closed.

A profile may never be inferred from an environment variable, listener,
hostname, Docker context, installer omission, or discovered service. Reading,
previewing, switching, diagnosing, or exporting structural profile evidence
does not open a listener, start a runtime, copy or upload a tree, select a
provider, or grant cloud consent. Migration, export, import, and synchronization
remain separate reviewed operations with their own confirmation boundaries.

The renderer boundary does not change between profiles. It receives no raw
network access, endpoint, session or enrollment bearer, Docker credential,
filesystem path, keyring value, provider secret, or SQLCipher key. The shared
Python service owns profile policy and redacted evidence; a future Electron
presentation must keep its bridge fixed and typed. When a non-local runtime is
implemented, switching profiles must clear renderer state, revoke scoped
grants, and establish a fresh authenticated session rather than carrying
authority across the boundary.

Issue #363's host container-control foundation remains Main-process-only and
unreachable from profile selection, preload, renderer, shared renderer types,
and application containers. It transports no genealogy data, secret value,
provider payload, raw process output, local path, socket, Docker credential, or
generic Docker authority across those boundaries. Its checked native receipt
contains only normalized control facts and counts. Runtime integration must
retain these exclusions and pass its own consent, secret-delivery, mount, and
data-flow review before any application workload is started.

Local containers do not read the OS keyring directly. A narrow host broker may
provide a required secret to one authorized process after policy and consent
checks through non-pageable or locked no-swap memory, or a no-swap
memory-backed filesystem with equivalent platform evidence. The value must not
enter Compose files, images, environment manifests, command arguments, logs,
inspection output, swap, or renderer state. Data and SQLCipher-key material use
separate storage and backup paths.
`provider=none` is incompatible with Connect Remote and Host Remote. It forces
Local Desktop/local execution, opens no network socket, and rejects remote
activation even when endpoint state or ambient credentials exist. Remote use
requires a separate explicit profile and may not claim `provider=none`. The
offline profile selects the socket-free native application-service path and
does not start the container backend, host supervisor, Engine API, gateway,
workers, or containers.

Host Remote is an explicit, advanced, self-supported profile for one trusted
household. Its operator controls the host root account, container runtime, DNS,
TLS edge, identity provider, logs, backups, and recovery and can therefore
observe plaintext handled by the service. The data owner explicitly accepts
that custody; containers do not protect data from a malicious or compromised
operator. Host Remote authorizes exactly one household principal and rejects
every other OIDC subject. Unrelated or mutually distrusting households require
separate hosts, secrets, volumes, and identity realms.
Remote hosting does not relax provider selection, consent, retention,
redaction, authentication, or audit policy.

Only the validated TLS gateway may be publicly reachable. Internal network or
VPN membership is not identity: every application route requires an
authenticated, authorized session, and there are no anonymous health, schema,
documentation, setup, or bootstrap routes. Network and identity providers may
still observe traffic metadata even when payloads are encrypted, so production
logging remains off or privacy-minimal and excludes genealogy and secret data.

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
