# Use transient desktop chat

The v0.6 **Chat** workspace is a narrow, transient presentation over a reviewed
provider profile. It is not a genealogy command surface, autonomous agent, or
evidence system. Model output is advisory, not evidence, and must be verified against
authoritative sources.

Do not paste real genealogy records, government identifiers, credentials,
private notes, database contents, local paths, logs, or other sensitive material
while learning the interface. Use fictional text and the minimum permitted data
classes.

## Start a conversation

1. Open **Chat**.
2. If the workspace says **Configure a provider to start**, choose
   **Open provider settings** and follow
   [Configure a desktop provider and consent](desktop-provider-consent.md).
3. Select the exact provider profile and model.
4. Choose a reviewed purpose: **Genealogy analysis**, **Source analysis**, or
   **Writing assistance**.
5. Review **Provider and privacy scope**. It identifies **Provider**, **Model**,
   **Purpose**, **Data shared**, and **Consent**.
6. Choose **New conversation** only when the scope matches your intent.

For a loopback Ollama profile, the status is **Local** and consent reports
**Not required for local provider**. Cloud profiles are **Remote** and require a
current compatible grant; **No compatible consent** blocks conversation
creation. `provider=none` stays network-free and cannot generate chat output.

## Send and control a message

The workspace labels the session **Transient conversation** and **Not saved**.
Type no more than 16,384 characters, then choose **Send message** or press
<kbd>Ctrl+Enter</kbd>. Use **Stop response** to request cooperative
cancellation. After a completed response, **Regenerate response** repeats the
last eligible turn within the same reviewed scope.

The composer and transcript remain in memory only. The service bounds each
session to 32 messages, 65,536 characters of context, 4,096 output tokens, a
120-second timeout, and one safe retry before output starts. Tools, files,
databases, shells, plugins, external services, and autonomous actions are
disabled.

The status can be **Not started**, **Streaming**, **Stopping**,
**Completed**, **Interrupted**, or **Failed**. A screen reader receives one
polite status announcement path; the transcript itself is not a live region.

## Treat output as untrusted display data

Rendered Markdown uses a closed CommonMark/GFM allowlist. Raw HTML, images,
embeds, implicit links, and executable actions are disabled. To retain text,
choose **Copy response**; only plain text reaches the clipboard.

An explicit HTTPS link shows its normalized destination and must pass through a
native Electron confirmation before opening. The renderer cannot navigate or
call the network directly. Never treat a generated claim as evidence: compare
it with the source transcription, GEDCOM provenance, and other authoritative
records.

## End or recover a conversation

Choose **Close conversation** when finished. Closing the session, tearing down
the workspace, or shutting down the sidecar clears its in-memory content. Audit
records retain reviewed identifiers, counters, usage, and one-way hashes—not
prompt or response payloads.

If the stream is interrupted, use the displayed stable code and remediation.
Do not repeatedly submit after output begins: replay and acknowledgement are
bounded, and generation is never retried after visible output. A revoked
consent, changed endpoint, missing credential, owner mismatch, cursor gap, or
stalled renderer fails closed.

See [Desktop reference](../reference/DESKTOP.md) for limits and error codes and
[Privacy and consent](../explanation/PRIVACY_AND_CONSENT.md) for the disclosure
boundary.
