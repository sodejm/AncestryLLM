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
