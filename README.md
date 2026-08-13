# AncestryLLM

AncestryLLM is a local-first toolset for people researching family history. It
helps you work with RootsMagic and GEDCOM data using predictable local tools,
with optional AI assistance only when you choose it.

The implemented product surfaces in 0.5.0 are the command-line interface (CLI),
an interactive prompt, and a deliberately small desktop control shell. Your
research remains yours: use fictional data while learning, and never put real
family-tree files, reports, or credentials in this repository.

## What you can do today

- Use the CLI for one task at a time, or open the interactive prompt for a
  local session.
- Work with genealogy research workflows that preserve RootsMagic inputs and
  handle GEDCOM data carefully.
- Install the released desktop control shell, which provides Home, Diagnostics,
  Settings, and capability onboarding.

The released 0.5 desktop shell is not a desktop genealogy application. It does
not include desktop genealogy or domain routes, files, jobs, providers, cloud
accounts, or updater flows. Desktop genealogy workflows are not available yet;
use the CLI or interactive prompt for supported genealogy work.

The current unreleased 0.6 source adds a narrow desktop settings and credential
management foundation. It can update five reviewed non-secret settings and can
set, delete, or report only the presence of allowlisted credentials through the
OS keyring. It cannot read credential values, select consent on your behalf,
make a provider call, or run a genealogy workflow. This development surface is
not part of the released 0.5.0 installer until its packaged verification gates
pass.

Unreleased 0.6 source also adds separate Local Providers, Cloud Providers, and
Consent & Privacy settings. Saving a provider profile requires an explicit
endpoint test and an optimistic-revision match. Creating consent requires a
complete preview of the provider, profile, model, purpose, data classes,
retention, warnings, and optional budget; living-person and remote-retention
choices receive explicit warnings. A stored key alone cannot enable a provider,
and this configuration surface does not execute provider requests.

Unreleased Issue #109 adds a **Tasks** destination for backend-owned work. It
reloads sanitized snapshots, follows bounded monotonic events, distinguishes
cancelling, pending-safe-point, cancelled, and terminal outcomes, and displays
only safe artifact metadata. The renderer stores no job state and receives no
path or artifact authority. This surface admits no work and adds no provider or
genealogy operation.

The unreleased desktop first run now recommends **Local Desktop** and keeps
**Connect Remote** and **Host Remote** visible but unavailable. Before it
enables settings or credential changes, the shell checks a sanitized schema-v1
startup report for configuration, SQLCipher, keyring, and workspace readiness.
If a required component is blocked, the application stays open in read-only
Diagnostics and gives stable recovery guidance; it does not repair
configuration, initialize a database, replace a key, or fall back to plaintext.
The packaged sidecar reads credentials only from the OS keyring. The documented
environment fallback remains limited to explicit CLI and headless use.

Unreleased 0.6 source also introduces the deployment-profile control plane.
Local Desktop is the preselected, recommended mode. The CLI can inspect,
preview, diagnose, and explicitly recover the versioned profile without
discovering a mode from the network, environment, Docker, or ambient services.
Connect to Remote and Host Remote Server remain advanced, unavailable runtime
choices until their separate enrollment, host-bootstrap, and release gates
pass; selecting a profile never starts a listener or moves genealogy data.

Unreleased Issue #348 adds a user-visible macOS arm64 local-runtime manager on
top of that host-only control foundation. After explicit review and
confirmation it can acquire exact checksum-pinned Colima/Lima, Docker CLI,
Compose, and Buildx artifacts into an app-owned profile and context, then
start, stop, repair, or remove that substrate. Docker Desktop remains optional
and compatible; AncestryLLM neither selects nor changes its context. Docker
authority never enters the renderer, preload bridge, sidecar, or managed
containers. This work does not start an AncestryLLM application container,
activate a deployment profile, mount genealogy data, or weaken the network-free
`provider=none` contract. See the
[published deployment operations guide](https://sodejm.github.io/AncestryLLM/DEPLOYMENT.html#host-container-control-foundation)
for the trust boundary, recovery procedure, and residual risk.

Issue #349 adds a minimal, production-shaped OCI and Compose verification
topology for one probe-only gateway and an optional dormant worker. The images
are built and exercised natively on Linux amd64 and arm64 in CI, expose no host
ports, run non-root with read-only roots and bounded resources, and contain no
genealogy or provider route. A read-only named-volume attachment is a policy
placeholder only: storage initialization, schema migration, secret delivery,
and profile activation remain blocked until their separate #350 and #351
controls pass. The topology is release evidence, not a supported deployment
runbook or application-container availability claim.

## Start here

Choose the path that matches how you want to use AncestryLLM.

### Use the CLI or interactive prompt

You will need Python 3.12 through 3.14 and a working OS credential store.

1. Install AncestryLLM as an isolated command with either
   [uv](https://docs.astral.sh/uv/guides/tools/) or
   [pipx](https://pipx.pypa.io/):

   ```bash
   uv tool install ancestryllm
   # or
   pipx install ancestryllm
   ancestry --version
   ```

   If you are already working in an activated virtual environment, ordinary
   `pip` remains supported:

   ```bash
   python -m pip install ancestryllm
   ```

   For optional AI assistance, use
   `uv tool install 'ancestryllm[all-llm]'`,
   `pipx install 'ancestryllm[all-llm]'`, or the corresponding ordinary
   `pip` command in place of the base install. Installing an extra still does
   not select a provider or authorize a cloud call.

2. Run `ancestry --help` to see available commands, or run `ancestry` to open
   the interactive prompt.

On a minimal or headless system, first follow the
[setup diagnostics](https://github.com/sodejm/AncestryLLM/blob/main/docs/SETUP_DIAGNOSTICS.md).
They cover a supported credential backend and the headless/CI
environment-injection fallback for ephemeral test secrets. Run
`ancestry --json database diagnose` before opening an encrypted workspace.

### Work from source

A source checkout requires a system-supplied Python 3.12 through 3.14. The
checked-in `.python-version` selects 3.12 by default; repository `uv` policy
never downloads Python. After authenticating as described in the
[verified `uv` bootstrap guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/security/verified-uv-bootstrap.md), run:

```bash
make setup
make test
```

`make setup` verifies exactly `uv` 0.12.1 and synchronizes all application
extras and dependency groups from `uv.lock`. Do not create a separate
`pip`-managed development environment or install `uv` from `PATH`.

### Use the desktop control shell

Desktop installation does not require Python or pipx. Download the
target-matched full installer and `SHA256SUMS` from the same immutable
   [official release](https://github.com/sodejm/AncestryLLM/releases). Verify
   the checksum and declared `binarySigningMode`, then install, relaunch, and
   confirm healthy Diagnostics. If startup is degraded, keep the shell in its
   read-only state and follow the component-specific recovery shown in
   **Diagnostics** before retrying once or relaunching. Read the
   [desktop installation and verification procedure](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/DESKTOP_SHELL.md#installation-and-updates)
   before downloading.

For examples and a complete command reference, read the
[CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/CLI.md). For
help using the interactive prompt, read the
[REPL guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md).

## Privacy and provider choices

AncestryLLM works locally by default. Provider `none` is network-free even if
environment keys or provider software are present. A cloud call requires
explicit provider selection and your consent; installed packages and environment
keys never choose a remote provider for you.

Credentials belong in your OS keyring. Keep real genealogy records, exports,
backups, logs, prompts, and secrets out of the repository. Learn the details in
the [privacy and consent guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/PRIVACY_AND_CONSENT.md)
and the [provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/PROVIDERS.md).

## Learn more

- [Understand the released desktop shell and its limits](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/DESKTOP_SHELL.md).
- [Check GEDCOM compatibility and interoperability limits](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/GEDCOM_COMPATIBILITY.md).
- [Learn how AncestryLLM protects file imports](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/FILE_INGRESS.md).
- [Read the documentation site](https://sodejm.github.io/AncestryLLM/) for
  guides and reference material.
- [Contribute to AncestryLLM](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md)
  if you are working on the project itself.
- [Maintain locked dependency environments](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/DEPENDENCY_MAINTENANCE.md)
  when changing application extras or repository tool groups. Source checkouts
  use `make setup`; the former `dev` extra is no longer an installation path.
