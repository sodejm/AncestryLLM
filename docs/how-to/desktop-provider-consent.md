# Configure a desktop provider and consent

Use the v0.6 **Settings** workspace to create a reviewed provider profile and,
for a remote provider, an exact consent grant. Configuration is administrative:
it does not run a prompt, start a genealogy workflow, or disclose data.

Keep `provider=none` for offline work. It remains network-free even when API
keys or provider SDKs are present. A saved credential, profile, or renderer
selection cannot activate a provider by itself.

## Choose a provider boundary

Open **Settings > Provider configuration** and choose one reviewed provider:

- **Ollama** is local only when its tested endpoint explicitly names loopback.
- **OpenAI**, **Anthropic**, **Gemini**, and **OpenRouter** use their reviewed
  built-in HTTPS endpoint. A custom remote endpoint is not accepted.

For a cloud provider, first store the corresponding credential in
**Secrets/Credentials**. Secret entry is write-only: after save, the desktop may
report **Present**, **Missing**, or **Unavailable**, but it cannot read the
value back. The Python secret store and OS keyring remain authoritative.

## Test and save a profile

1. Enter the profile name and supported model.
2. Choose **Test endpoint** before saving.
3. Verify that the test succeeds for the expected local loopback or built-in
   cloud destination.
4. Save the profile against the current settings revision.

Endpoint validation denies redirects and proxy inheritance and returns only a
redacted destination identity. Saving fails closed if the test is missing,
stale, or no longer matches the endpoint. It does not send genealogy data or a
prompt.

## Review cloud consent

Local Ollama use does not need a cloud-disclosure grant. For a remote profile:

1. Open **Settings > Consent** and enter a **Consent name**.
2. Select the exact **Provider profile**, model, and **Module summary**.
3. Keep the reviewed purpose, such as **Genealogy analysis**, and select only
   the necessary data classes.
4. Set the optional **Maximum cost in US dollars**. Leave
   **Allow provider retention** off unless retention is explicitly required.
5. Choose **Review consent**.
6. Read the exact provider, profile, model, module, purpose, data classes,
   retention choice, warnings, and budget in the preview.
7. Choose **Save consent** only when that preview matches the intended use.

Living-person data produces `LIVING_PERSON_DATA_INCLUDED`; a cloud route
produces `REMOTE_PROVIDER_SELECTED`; remote retention produces
`REMOTE_RETENTION_ENABLED`. These are explicit warnings, not blanket approval.
Minimize the disclosure and exclude living or possibly living people whenever
the task permits.

Consent is exact and revocable. It binds the provider, profile, endpoint,
model, modules, purposes, data classes, retention, and budget. The Python
policy checks it again before every disclosure; renderer state cannot bypass
that check.

## Revoke access

Under the active consent, choose **Revoke _consent name_** and confirm the
separate action. The state changes from **Active** to **Revoked**. A new or
continuing remote run must fail once the current grant is revoked; saving a
profile or keeping a credential does not recreate consent.

See [Privacy and consent](../explanation/PRIVACY_AND_CONSENT.md) for the reason
these boundaries exist and [Desktop reference](../reference/DESKTOP.md) for
provider endpoints, data classes, and stable error codes.
