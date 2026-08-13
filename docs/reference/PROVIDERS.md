# Provider guide

Adapters are available for `none`, Ollama, OpenAI, Anthropic, Gemini, and
OpenRouter. Install only needed extras, for example `pip install '.[anthropic]'`.
The canonical commands for secrets, profiles, consent, and revocation are in
[the CLI reference](CLI.md#providers-and-secrets).

Remote endpoints must use HTTPS. Ollama may use HTTP only on a loopback address.
Models, modules, purposes, data classes, token limits, timeouts, and payload
retention are checked before a call. Provider output is data only: it is
schema validated and never executed as SQL, Python, shell, or a tool invocation.

Environment variables listed in `.env.example` are a headless CI fallback. The
application does not load `.env`. Merely setting a provider key cannot select a
provider or initiate a request.

## Desktop provider settings

Unreleased Issue #108 adds a provider-configuration and consent administration
surface to the desktop shell. It does not expose provider execution. Local
Ollama profiles accept only an explicitly tested loopback endpoint. Cloud
profiles use the exact built-in HTTPS endpoint for the selected provider; the
desktop surface cannot supply an alternate cloud URL.

Endpoint testing does not inherit a proxy or follow redirects. It resolves the
exact hostname, connects directly to the resolved numeric address while
retaining TLS hostname and certificate verification, repeats resolution after
the probe, and returns only a SHA-256 destination identity. Profile save,
consent creation, and execution recheck the same endpoint identity. A failed,
missing, stale, privately routed, link-local, or changed destination fails
closed without returning an address or response body.

Saving a profile requires both the current optimistic revision and the digest
from its explicit endpoint test. Consent creation first requires a complete
preview of the provider, profile, model, modules, purposes, data classes,
retention, warnings, and optional budget, then accepts only that exact preview
against the current consent revision. Secret fields remain blank and
write-only; configuration reports key presence only. A stored key alone cannot
enable a provider, choose a profile, or grant consent.

## Transient chat execution

Unreleased Issue #110 adds a source-level, synchronous transient-chat boundary
behind the authenticated private API. A session names one stored operational
profile and its exact model, purpose, and data classes. Direct provider
identifiers, `provider=none`, missing or incompatible profiles, and conflicting
models fail before provider construction. Session creation preflights the
profile, endpoint, credential, policy, and consent; every run repeats that
preflight and fetches current consent before any remote generation.

The boundary admits at most 32 sessions, including pending creations. Each
session retains at most 32 messages in process memory, each message is limited
to 16,384 characters, and a request is limited to 65,536 context characters,
4,096 output tokens, one safe retry, and 120 seconds. A fixed system instruction
marks user content as untrusted and the response as advisory. Chat requests
have no tool schema or authority to read files, query databases, invoke a shell
or plugin, call another external service, perform genealogy operations, or act
autonomously.

Successful user and assistant messages remain only in the owning `ChatService`
process. Failed runs do not append content, and deletion or application
shutdown clears the session. Audit output contains identifiers, counters,
usage, and one-way payload hashes rather than prompts or responses. Issue #110
itself does not add an Electron renderer or preload bridge, streaming or
cancellation, safe Markdown presentation, or packaged-network evidence. Issue
#111 now supplies the bounded private streaming and cancellation transport;
renderer presentation and packaged-network evidence remain owned by Issues
#112 and #131.

## Audited asynchronous provider streams

Unreleased Issue #56 adds `LLMService.async_stream()` as an internal adapter
for provider SDKs whose stream iterators are synchronous. Named-profile
planning, endpoint and credential policy, consent, and streaming-capability
checks finish before the provider worker starts. Requests with a response
schema are rejected from this path and continue through `generate()`, where
their complete response receives schema validation.

The adapter copies the current cancellation context into an off-loop daemon
worker and connects it to the event loop through a bounded queue. Defaults are
16 queued items and 64 KiB per UTF-8 chunk. Configuration cannot exceed 256
items, 1 MiB per chunk, or 16 MiB of queue capacity. One absolute lifecycle
deadline covers execution admission and provider iteration. Only asynchronous
queue waits are wrapped by the timeout, so work the caller performs between
yielded chunks is not accidentally cancelled; a scheduled stop signal still
prevents the worker from continuing to enqueue after the deadline. Caller
cancellation and early consumer close also signal cooperative stop.

Success, provider failure, timeout, cancellation, and early close each produce
exactly one terminal audit outcome. The default record contains one-way request
and response hashes rather than payloads. Explicit retention consent is still
required to retain content, and cancellation discards partial retained output.
Stable errors omit provider bodies, chunks, secrets, paths, and local identity.
A synchronous SDK may not finish unwinding until its iterator next yields or
returns, so provider-side network timeouts remain necessary. The worker is a
shutdown-safe daemon and holds its execution lease until that unwind completes.
Issue #56 adds no HTTP, IPC, Electron, renderer, or public streaming contract.
Issue #111 consumes this adapter through fixed authenticated start, SSE, and
cancellation routes plus an Electron-Main-owned source bridge. That transport
strictly validates owner-scoped monotonic events, batches within 16 milliseconds
or 4 KiB, pauses above 256 KiB of exact unacknowledged data, cancels after a
15-second acknowledgement stall, and permits one same-run cursor reconnect
without retrying provider output. Renderer chat state and safe model-output
presentation remain Issue #112; packaged adversarial evidence remains #131.

## Application boundary

Only modules under `ancestryllm.llm.providers` initiate LLM network requests.
GEDCOM merge, incremental update, and quality operations build the same
`GenerationRequest` contract and call `LLMService`; deterministic parsing,
scoring, preservation, rollback, and report logic receive only a narrow
provider-neutral resolver. The old GEDCOM and OCR HTTP/SDK implementations and
environment-key auto-selection paths have been removed.

`ChatService` also composes `LLMService`, but owns only bounded transient chat
state and has no genealogy, artifact, file, database, shell, or tool authority.

This makes profile planning, consent, schema validation, timeouts, retries,
bounded scheduling, exact-result caching, and audit metadata apply to every
GEDCOM LLM call. Provider failures, cancellations, timeouts, consent denials,
queue overload, and malformed output use stable sanitized application errors
and do not include provider payloads. `provider=none` does not construct a
resolver or provider adapter, even when credentials, SDKs, profiles, and
local-server settings are present.

## Operational profiles and execution

`--provider` accepts either a built-in identifier plus `--model`, or a named
profile created with `ancestry providers create`. A named profile supplies the
provider, model, endpoint, and validated execution settings. A conflicting
command-line model is rejected. Cloud consent is bound to the exact named
profile as well as provider, module, purpose, data classes, and model, so a
grant for one endpoint cannot authorize another.

A direct remote selection from the CLI or genealogy workflows still executes a
named operational profile: the required `--consent` identifies its linked
profile, and the direct provider and model must match that profile. Resolution
happens before SDK use, so direct syntax cannot bypass the profile's endpoint,
execution settings, or consent scope. The private transient-chat boundary is
narrower and rejects direct provider syntax entirely.

Ollama profiles support safe endpoint selection, `keep_alive`, `num_ctx`,
`num_batch`, `num_thread`, `num_gpu`, `seed`, temperature/output limits,
timeouts, retries, concurrency, total pending requests, and cache bounds.
Loopback endpoints are local. Non-loopback Ollama endpoints require HTTPS and
matching profile-bound consent and are reported as remote. Unknown or
out-of-range settings fail before provider construction. A profile can tighten
module output or timeout bounds; `max_safe_retries` is an explicit opt-in of at
most two pre-output retries for rate-limit or transient failures.

An OpenRouter profile with `zero_data_retention=true` enforces privacy routing
on every generation and streaming request: `zdr=true` restricts routing to ZDR
endpoints, `data_collection=deny` excludes endpoints that may collect data, and
`require_parameters=true` excludes endpoints that do not support every request
parameter. With the setting false, AncestryLLM makes no per-request ZDR claim;
OpenRouter account or guardrail policy may still impose stricter routing.

The registry shares an Ollama adapter per endpoint/profile, and that adapter
shares timed SDK clients until application shutdown. A provider-neutral
coordinator bounds active and total admitted requests per
provider/profile/model, including identical single-flight waiters. Overflow,
queue timeout, cancellation, and shutdown use stable
`PROVIDER_QUEUE_FULL`, `PROVIDER_QUEUE_TIMEOUT`, `PROVIDER_CANCELLED`, and
`PROVIDER_SERVICE_CLOSED` errors. Cancellation interrupts queue, cache, and
retry-backoff waits.

An explicit positive `cache_ttl_seconds` enables the bounded exact-result cache
only for deterministic structured requests. It stores only successful,
schema-valid results, collapses identical concurrent work with single-flight,
uses a process-random HMAC cache key scoped to the application/workspace and
consent ID, and records hits as `cache_hit` audit rows. Cached content is
memory-only, LRU-bounded, and discarded at shutdown; it is never written as an
additional plaintext or database payload.

Identity decisions must contain strict JSON with a finite confidence from zero
through one. Automatic GEDCOM merging requires the documented confidence floor;
lower-confidence decisions remain separate for review. Identity prompts can
include bounded relationship context, so every remote identity-adjudication
request requires explicit `possibly_living_person` consent even when both
candidate records contain death dates.

## Capability differences

| Provider | Network policy | Structured output | Streaming | Usage/cost reporting |
| --- | --- | --- | --- | --- |
| `none` | Strictly offline; generation is disabled | No | No | None |
| Ollama | HTTP only on loopback; HTTPS elsewhere | Native schema format | Yes | Input/output tokens when returned; no cost |
| OpenAI | Built-in HTTPS endpoint only | Native strict JSON schema | Yes | Input/output tokens; no adapter cost estimate |
| Anthropic | Built-in HTTPS endpoint only | Prompted JSON plus local schema validation | Yes | Input/output tokens; no adapter cost estimate |
| Gemini | Built-in HTTPS endpoint only | Native JSON schema | Yes | Input/output tokens; no adapter cost estimate |
| OpenRouter | Built-in HTTPS endpoint only | OpenAI-compatible JSON schema | Yes | Input/output tokens; no adapter cost estimate |

Every request has bounded output tokens, a bounded timeout, and zero SDK retries
by default. A stream is never retried after emitting output. Malformed structured
output and interrupted streams fail with stable, sanitized error codes. Audit
records retain hashes and usage metadata by default, not prompts, responses, or
partial stream content; payload retention requires explicit consent.

`max_cost_usd` is retained with a consent profile for workflows that can perform
a trustworthy price preflight. The generation adapters do not currently estimate
cost before a call and therefore cannot enforce that value by themselves. Use
provider-side account/project spending limits in addition to the request token
budget.

## Opt-in live smoke tests

Live tests are skipped by default. They use fictional public data, do not retain
payloads, and require all three explicit controls below:

```bash
export ANCESTRYLLM_LIVE_PROVIDER_TESTS=1
export ANCESTRYLLM_LIVE_PROVIDER_CONSENT=I_CONSENT_TO_PROVIDER_NETWORK_CALLS
export ANCESTRYLLM_LIVE_MAX_OUTPUT_TOKENS=64
```

Set the relevant key and model variables for each provider you intend to call:
`OPENAI_API_KEY` and `ANCESTRYLLM_LIVE_OPENAI_MODEL`, `ANTHROPIC_API_KEY` and
`ANCESTRYLLM_LIVE_ANTHROPIC_MODEL`, `GEMINI_API_KEY` and
`ANCESTRYLLM_LIVE_GEMINI_MODEL`, or `OPENROUTER_API_KEY` and
`ANCESTRYLLM_LIVE_OPENROUTER_MODEL`. Ollama requires
`ANCESTRYLLM_LIVE_OLLAMA_MODEL`; optionally set
`ANCESTRYLLM_LIVE_OLLAMA_ENDPOINT` (the normal endpoint policy still applies).
Run only the live module with:

```bash
.venv/bin/python -m pytest -v tests/test_llm_providers_live.py
```

Missing provider-specific credentials or models skip only that provider. The
output-token budget must be between 1 and 256. Enabling these tests can incur
provider charges and transmit the fictional test prompt to the selected service.
