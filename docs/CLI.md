# Command-line reference

This is the canonical reference for the supported `ancestry` command line.
Run `ancestry --help` or append `--help` to a command family for the exact
parser help for the installed version. Use `--config PATH` to select a
non-secret `config.toml`, and `--json` when a script needs serializable output.
All user-selected inputs use the documented
[bounded file-ingress policy](FILE_INGRESS.md); one-shot and interactive
commands return the same stable file error codes.

Run `ancestry` with no arguments to open the interactive console; see
[the console guide](CONSOLE.md). The console and one-shot commands share action
dispatch and stable application-error codes. Parser presentation intentionally
differs: one-shot usage errors use the standard command-line parser, while the
console keeps running and renders a safe REPL usage error.

## Command families

| Family | Supported actions | Purpose |
| --- | --- | --- |
| `modules` | `list`, `enable MODULE`, `disable MODULE` | List or configure built-in console modules. |
| `rootsmagic` | `list`, `query`, `export` | Read RootsMagic data without modifying the source file. |
| `gedcom` | `merge`, `subtree`, `quality`, `sync update`, `sync rebase` | Create loss-minimizing GEDCOM outputs and reports. |
| `prompts` | `list`, `save`, `show`, `render` | Manage versioned prompt templates. |
| `people` | `list`, `add` | Maintain the encrypted research-person workspace. |
| `providers` | `list`, `create`, `consent`, `revoke` | Configure explicitly selected local/cloud profiles and cloud consent. |
| `secrets` | `set`, `delete`, `status` | Manage OS-keyring secret references. |
| `ocr` | `extract` | Extract structured data from an input text file through an approved provider. |
| `database` | `backup DESTINATION` | Create an encrypted workspace backup. |

`modules` only enables or disables built-in modules. It never discovers or
loads third-party code. Disabling a module prevents its actions from running in
both one-shot and console use; re-enable it explicitly before use. The first
command using the research workspace creates
an encrypted SQLCipher database; its random key is stored only in the OS
credential store.

## Exit codes

One-shot commands return `0` on success. A stable, bracketed application error
returns its documented error exit code (normally `1`); parser, input, and local
filesystem failures return `2`. The console keeps running after a failed
command and renders the same stable error code without a traceback. `Ctrl-C`
requests cancellation of the most recently submitted active job without
exiting the console. With active jobs, `exit` or `quit` requires a `wait`,
`cancel`, or `stay` decision; EOF fails safe by waiting and never implicitly
cancels work.

## Common examples

```bash
# Inspect enabled features and known RootsMagic trees.
ancestry modules list
ancestry rootsmagic list

# Produce a local, deterministic GEDCOM quality report.
ancestry gedcom quality tree.ged --output quality.md --root-person "Ada Lovelace"

# Add advisory local-model context through the shared provider service.
ancestry gedcom quality tree.ged --output quality.md --root-person "Ada Lovelace" \
  --provider ollama --model llama3

# Save an operational profile, then reuse its model, endpoint, and limits.
ancestry providers create local-research --provider ollama --model llama3 \
  --setting base_url=http://127.0.0.1:11434 \
  --setting max_concurrency=1 --setting max_pending=8 \
  --setting cache_ttl_seconds=300
ancestry gedcom quality tree.ged --output quality.md --root-person "Ada Lovelace" \
  --provider local-research

# Export without changing the RootsMagic source database.
ancestry rootsmagic export --tree family.rmtree --output family.ged \
  --destination ancestry --scope ancestors --root-person-id I42

# Save and render a versioned prompt without calling a provider.
ancestry prompts save lookup --purpose research --body 'Research {{person}}' --variable person
ancestry prompts render lookup --value person='Ada Lovelace'
```

## Family details

### RootsMagic and GEDCOM

`rootsmagic query` requires `--tree` and exactly one of `--sql` or
`--question`. SQL is restricted to bounded, read-only queries. A natural-language
`--question` is a provider operation and therefore needs an explicit provider
profile and matching consent when its provider is not `none`. The inspected
schema and question are sent as explicitly delimited JSON data under a separate
fixed system policy. Their combined UTF-8 size is bounded by
`file_ingress.prompt_body.max_bytes`; an oversized payload fails locally as
`ROOTSMAGIC_SCHEMA_PROMPT_TOO_LARGE` with exit code `2` before provider
execution. `provider=none` remains network-free even when provider credentials
are available.

`rootsmagic export` requires `--tree` and `--output`; select `portable` or
`preservation` output, GEDCOM `5.5.5` or `5.5.1`, destination, scope, generation
limit, living-person handling, and an optional loss report as needed. The output
and optional report parent directories must already exist; a rejected export
does not create parent directory trees.
Pointer allocation and record ordering use canonical semantic keys rather than
SQLite row order. `portable` contains standard safely representable GEDCOM;
`preservation` additionally contains attributable privacy-safe `_RM_*`
extensions. Duplicate and conflicting representable values remain separate,
while unsupported, blob, or unsafe values are counted without being copied into
the loss report. Living-person exclusion and redaction fail closed across
families, notes, citations, sources, media, custom values, reports, and command
output. GEDCOM and its optional report publish as one rollback-capable pair.
Destination selections are standards-based format targets, not claims of
successful import into a current vendor product.
Close RootsMagic before querying or exporting: the CLI rejects databases with
an existing SQLite `-wal`, `-shm`, or rollback `-journal` sidecar before it
hashes, copies, opens, or sends schema information to a provider.
It also rejects hard-linked database aliases because SQLite sidecars are named
for one pathname; use a standalone stable backup with a link count of one.
Configured family-tree directories are identity-bound for each command. A
parent-directory symbolic-link or junction swap fails before rows, schema, or
export content can be consumed, and malformed export/report path expansion
uses the same sanitized `FILE_INPUT_UNREADABLE` code in one-shot and
interactive use.

`gedcom merge INPUT... --output OUTPUT` accepts optional root-person, quality
report, GEDCOM version, duplicate-similarity threshold, and explicit
`--provider`, `--model`, and `--consent` options.
`gedcom subtree INPUT --output OUTPUT --root-person NAME` accepts connected,
ancestor, or descendant scope and an optional generation limit. `gedcom quality`
requires an input, output, and root person; the same provider options add
bounded advisory explanations without changing deterministic findings. See
[GEDCOM compatibility and release checks](GEDCOM_COMPATIBILITY.md) for
preservation and interoperability rules.

`gedcom sync update` and `gedcom sync rebase` pass their remaining options to
the incremental-sync CLI. Use `ancestry gedcom sync update --help` or
`ancestry gedcom sync rebase --help` before operating on a master or manifest;
these workflows preserve protected and manually curated material by default.
Persisted source bindings and canonical semantic ordering determine pointer
allocation; filesystem, argument, and record order do not. Origins remain
independent unless an existing binding explicitly associates them, and
repeating the same synchronization produces the same semantic bundle.

Automatic reconciliation can remove only source-owned, uncited, unprotected
fact blocks. It never removes complete people, families, relationships, source
records, cited facts, or protected/manual facts. A snapshot import never
revives a tombstone automatically, and a changed fact that collides with a
tombstoned logical identity is retained as a deletion conflict in the update
report rather than restored silently. A reviewed manual rebase that restores
matching content retires that tombstone.
`--accept-manual-deletions` authorizes deletions only for that one reviewed
rebase; it does not authorize later automatic removals.

Before staging output, synchronization rejects malformed or future manifest
schemas, generation gaps, non-monotonic release history, incoherent parent
releases, and inconsistent active-source history as `MANIFEST_INVALID`. A
master or published-artifact fingerprint mismatch is
`MANIFEST_MASTER_MISMATCH`. Publication uses an exclusive final-directory
operation as its serialization point: concurrent losers fail without changing
the winning release, and each failure leaves either the previous complete
release or the new complete release. Prior releases remain immutable and
rollback metadata remains available. Cancellation requested during the
non-interruptible commit or rollback is acknowledged after that boundary; a
real publication failure takes precedence.

The release root itself may be new, but its parent directory must already exist;
a rejected synchronization does not create ancestor directory trees.
On a failed or cancelled POSIX publication, AncestryLLM fails closed rather
than issuing a name-bound directory deletion that could remove a concurrent
replacement. An empty app-owned `.gedcom-*` or
`.ancestryllm-release-root-*` directory can therefore remain for manual
inspection and removal; it contains no committed release. A
`SYNC_PUBLICATION_INCOMPLETE` error (exit `7`) instead means a generation
directory with an ownership marker may remain. Do not use or rename that
generation as a release; follow the coded remediation before retrying.
Update defaults to `--provider none`. Optional identity adjudication accepts
either `--provider PROFILE` (the profile supplies its model and settings) or a
built-in `--provider PROVIDER --model MODEL`. A direct remote selection also
requires `--consent NAME`; AncestryLLM executes the exact operational profile
linked to that consent and rejects a provider or model mismatch before SDK use.
With `--provider none`, synchronization does not construct provider adapters or
make network calls, even when provider credentials are present and SDKs are
importable. Rebase is deterministic and never invokes a provider. The retired
`--ai-backend` and provider-specific model/key options are rejected rather than
routed through legacy network code.

### Prompts, people, backups, and OCR

`prompts save NAME --purpose PURPOSE` requires exactly one of `--body` or
`--body-file`; optional `--variable`, `--schema-file`, and `--tag` may be
repeated. `prompts show` and `prompts render` accept `--version`; rendering uses
one or more `--value NAME=VALUE` arguments.

`people list` and `people add DISPLAY_NAME` accept `--workspace`; adding a
person also accepts `--living-status` and `--notes`. `database backup
DESTINATION` writes an encrypted backup. Keep backups and all genealogy data
outside version control.

`ocr extract --input FILE --provider PROFILE` reads bounded UTF-8 text and
rejects inputs over 5,000,000 bytes by default. Config, JSON manifest/schema,
OCR, and prompt-body inputs are UTF-8; GEDCOM additionally accepts BOM-declared
UTF-16. A built-in provider selected without a named profile also requires
`--model MODEL`. Because OCR sends source material to a provider, a remote
profile requires its exact matching consent unless policy denies the request
first.

### Providers and secrets

Use `secrets set NAME` to enter a value twice at a no-echo prompt. Do not place
secret values in command arguments, console options, files, or shell history.
`secrets status [NAME]` reports only whether a reference exists; `secrets delete
NAME` removes the reference.

Create a profile with `providers create NAME --provider PROVIDER --model MODEL`.
Repeat `--setting NAME=JSON_VALUE` to configure supported operational settings.
All providers accept bounded timeout, output-token, retry, concurrency, queue,
and process-local cache limits. Ollama additionally accepts `base_url`,
`keep_alive`, `num_ctx`, `num_batch`, `num_thread`, `num_gpu`, and `seed`.
OpenRouter accepts only its built-in allowlisted `base_url` and a
`zero_data_retention` declaration. When true, every OpenRouter generation and
streaming request sends provider-routing controls that require ZDR endpoints,
deny data-collecting endpoints, and require support for every request parameter.
When false, AncestryLLM sends no per-request ZDR claim; account-level OpenRouter
privacy controls may still apply. Unknown, out-of-range, or unsafe settings fail
before an SDK client or socket is created. Loopback Ollama endpoints remain
local; every non-loopback Ollama endpoint requires HTTPS and exact
profile-bound consent because it can disclose data off-device. A profile name
may not shadow a built-in provider identifier.

Selecting a profile uses its stored model; supplying a different `--model`
fails with `PROVIDER_PROFILE_MODEL_CONFLICT`. Profile output-token and timeout
settings can tighten, but never enlarge, a module's request bounds.
`max_safe_retries` is a separate explicit profile opt-in, bounded from zero to
two and limited to pre-output rate-limit or transient failures.
Ollama clients are shared per endpoint/profile and closed with the application
context. Requests use per-profile/model concurrency and total-pending limits;
the pending bound includes identical single-flight waiters. Overflow and queue
timeout return `PROVIDER_QUEUE_FULL` and `PROVIDER_QUEUE_TIMEOUT`; cancellation
also interrupts queue, cache, and retry-backoff waits.

`cache_ttl_seconds` opts deterministic, schema-valid requests into a bounded
process-local exact-result cache. `cache_max_entries` sets its LRU bound.
Identical concurrent requests use single-flight execution. The cache stores no
disk payload, is scoped to one application/workspace process and consent ID,
and is discarded at shutdown; audit rows identify hits with `cache_hit`.
Before data may leave the device, create narrowly scoped consent with
`providers consent NAME --profile PROFILE --module MODULE --purpose PURPOSE
--data-class CLASS --model MODEL`; each of the latter four options can be
repeated. Set `--max-cost-usd` and `--retain-payloads` only when intentionally
approved. `providers revoke NAME` withdraws a consent profile. Details of the
provider boundary are in [the provider guide](PROVIDERS.md).

## Offline and privacy guarantees

`none` is the default provider and makes no network requests, even if provider
keys are present in the environment. A key never selects a provider by itself:
remote use requires an explicit provider profile and an active consent profile
for that exact profile/endpoint that permits the module, purpose, data classes,
and model. Living and possibly living people are denied by default.

The application does not load `.env`. Environment values documented in
`.env.example` are headless/CI fallback only. Remote endpoints must use HTTPS,
except loopback Ollama may use HTTP. Provider output is treated as data, schema
validated, and never executed as SQL, Python, shell commands, or tools.
