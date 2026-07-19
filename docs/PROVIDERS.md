# Provider guide

Adapters are available for `none`, Ollama, OpenAI, Anthropic, Gemini, and
OpenRouter. Install only needed extras, for example `pip install '.[anthropic]'`.
The canonical commands for secrets, profiles, consent, and revocation are in
[the CLI reference](CLI.md#providers-and-secrets).

Remote endpoints must use HTTPS. Ollama may use HTTP only on a loopback address.
Models, modules, purposes, data classes, cost limits, token limits, timeouts, and
payload retention are checked before a call. Provider output is data only: it is
schema validated and never executed as SQL, Python, shell, or a tool invocation.

Environment variables listed in `.env.example` are a headless CI fallback. The
application does not load `.env`. Merely setting a provider key cannot select a
provider or initiate a request.
