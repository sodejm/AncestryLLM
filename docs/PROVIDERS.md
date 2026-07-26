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

## Application boundary

Only modules under `ancestryllm.llm.providers` initiate LLM network requests.
GEDCOM merge, incremental update, and quality operations build the same
`GenerationRequest` contract and call `LLMService`; deterministic parsing,
scoring, preservation, rollback, and report logic receive only a narrow
provider-neutral resolver. The old GEDCOM and OCR HTTP/SDK implementations and
environment-key auto-selection paths have been removed.

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

A direct remote selection still executes a named operational profile: the
required `--consent` identifies its linked profile, and the direct provider and
model must match that profile. Resolution happens before SDK use, so direct
syntax cannot bypass the profile's endpoint, execution settings, or consent
scope.

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
