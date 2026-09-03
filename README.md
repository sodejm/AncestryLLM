# AncestryLLM

[![Continuous integration](https://github.com/sodejm/AncestryLLM/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sodejm/AncestryLLM/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/sodejm/AncestryLLM)](LICENSE)

AncestryLLM is a local-first toolset for people researching family history. It
helps you work with RootsMagic and GEDCOM data using predictable local tools,
with optional AI assistance only when you choose it.

The implemented product surfaces in 0.6.0 are the command-line interface
(CLI), an interactive prompt, and a deliberately small desktop control shell.
Your research remains yours: use fictional data while learning, and never put
real family-tree files, reports, or credentials in this repository.

[Quick start](#start-here) · [Desktop shell](#use-the-desktop-control-shell) ·
[Documentation](https://sodejm.github.io/AncestryLLM/) ·
[Contributing](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md)

## Supported today

| Surface | What it is for | Boundary |
| --- | --- | --- |
| Command line and interactive prompt | Run local, command-driven genealogy workflows. | Supported core. |
| RootsMagic and GEDCOM | Inspect RootsMagic sources and use deterministic GEDCOM workflows. | RootsMagic inputs remain immutable; GEDCOM handling is loss-minimal. |
| Desktop control shell (0.6) | Use local Home, Diagnostics, Settings, and capability onboarding. | Supported bounded control surface. |

The supported 0.6.0 desktop core is not a desktop genealogy application. It
provides Home, Diagnostics, Settings, and capability onboarding. The installed
shell packages separately labeled provider and consent configuration, Tasks and
Chat source-level surfaces; those surfaces remain unsupported until their named
target-matched gates pass. Desktop genealogy workflows are not available yet;
use the CLI or interactive prompt for supported genealogy work.

On macOS arm64, Issue #348 adds a source-level local-runtime manager for an
app-owned, checksum-pinned container substrate after explicit review and
confirmation. Docker Desktop remains optional and AncestryLLM does not change
its context. This does not start an application container or make container
hosting a supported product surface; see the
[deployment operations guide](https://sodejm.github.io/AncestryLLM/DEPLOYMENT.html#host-container-control-foundation)
for its trust boundary and recovery procedure.

## Start here

Choose the path that matches how you want to use AncestryLLM.

### Use the CLI or interactive prompt

You will need Python 3.12 through 3.14 and a working OS credential store.

1. Install AncestryLLM as an isolated command with either
   [uv](https://docs.astral.sh/uv/guides/tools/) or
   [pipx](https://pipx.pypa.io/):

   ```console
   uv tool install ancestryllm
   # or
   pipx install ancestryllm
   ancestry --help
   ```

   If you are already working in an activated virtual environment, ordinary
   `pip` remains supported:

   ```console
   python -m pip install ancestryllm
   ```

   For optional AI assistance, use
   `uv tool install 'ancestryllm[all-llm]'`,
   `pipx install 'ancestryllm[all-llm]'`, or the corresponding ordinary `pip`
   command in place of the base install. Installing an extra still does not
   select a provider or authorize a cloud call.

2. Run `ancestry --help` to see available commands, or run `ancestry` to open
   the interactive prompt.

On a minimal or headless system, first follow the
[setup diagnostics](https://github.com/sodejm/AncestryLLM/blob/main/docs/SETUP_DIAGNOSTICS.md).
They cover a supported credential backend and the headless/CI
environment-injection fallback for ephemeral test secrets. Run
`ancestry --json database diagnose` before opening an encrypted workspace.

For command examples, read the
[CLI reference](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/CLI.md)
and [interactive-console guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md).

### Work from source

A source checkout requires a system-supplied Python 3.12 through 3.14. After
following the
[verified uv bootstrap guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/security/verified-uv-bootstrap.md),
run:

```console
git clone https://github.com/sodejm/AncestryLLM.git
cd AncestryLLM
make setup
make test
```

`make setup` verifies the pinned `uv` release and synchronizes application
extras and dependency groups from `uv.lock`. Do not create a separate
`pip`-managed development environment or install `uv` from `PATH`.

### Use the desktop control shell

Desktop installation does not require Python or pipx. Download the
target-matched full installer and `SHA256SUMS` from the same immutable
[official release](https://github.com/sodejm/AncestryLLM/releases). Verify the
checksum and declared `binarySigningMode`, then install, relaunch, and confirm
healthy Diagnostics. If startup is degraded, keep the shell in its read-only
state and follow the component-specific recovery shown in **Diagnostics**.
Read the
[desktop installation and verification procedure](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/DESKTOP_SHELL.md#installation-and-updates)
before downloading.

## Privacy and provider choices

AncestryLLM works locally by default. Provider `none` is network-free even if
environment keys or provider software are present. A cloud call requires
explicit provider selection and your consent; installed packages and environment
keys never choose a remote provider for you.

Credentials belong in your OS keyring. Keep real genealogy records, exports,
backups, logs, prompts, and secrets out of the repository. Learn the details in
the [privacy and consent guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/PRIVACY_AND_CONSENT.md)
and the [provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/PROVIDERS.md).

## Canonical documentation

| Need | Start here |
| --- | --- |
| Product status and architecture | [Architecture](https://github.com/sodejm/AncestryLLM/blob/main/ARCHITECTURE.md) and [v0.6.0 release notes](https://github.com/sodejm/AncestryLLM/blob/main/docs/release-notes/0.6.0.md) |
| CLI, console, installation, or troubleshooting | [CLI reference](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/CLI.md), [console guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md), and [setup diagnostics](https://github.com/sodejm/AncestryLLM/blob/main/docs/SETUP_DIAGNOSTICS.md) |
| Desktop installation or recovery | [Desktop shell](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/DESKTOP_SHELL.md), [first run](https://github.com/sodejm/AncestryLLM/blob/main/docs/tutorials/desktop-first-run.md), and [desktop diagnostics](https://github.com/sodejm/AncestryLLM/blob/main/docs/how-to/desktop-diagnostics.md) |
| Data compatibility and safe file access | [GEDCOM reference](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/GEDCOM_COMPATIBILITY.md) and [file-ingress policy](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/FILE_INGRESS.md) |
| Privacy, providers, or security | [Privacy and consent](https://github.com/sodejm/AncestryLLM/blob/main/docs/explanation/PRIVACY_AND_CONSENT.md), [provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/reference/PROVIDERS.md), [threat model](https://github.com/sodejm/AncestryLLM/blob/main/docs/THREAT_MODEL.md), and [security response](https://github.com/sodejm/AncestryLLM/blob/main/docs/SECURITY_RESPONSE.md) |
| Development, contributing, or releases | [Contributing](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md), [verified uv bootstrap](https://github.com/sodejm/AncestryLLM/blob/main/docs/security/verified-uv-bootstrap.md), and [release runbook](https://github.com/sodejm/AncestryLLM/blob/main/docs/RELEASING.md) |

The current supported boundary is maintained in
[ARCHITECTURE.md](https://github.com/sodejm/AncestryLLM/blob/main/ARCHITECTURE.md);
release scope and evidence are maintained in the
[release notes](https://github.com/sodejm/AncestryLLM/blob/main/docs/release-notes/0.6.0.md)
and [release runbook](https://github.com/sodejm/AncestryLLM/blob/main/docs/RELEASING.md).
Source-level work is not a promise of availability.

## Contribute

Contributions should preserve the local-first, privacy-first boundary. Read the
[contribution guide](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md)
before opening a change, and use the
[release runbook](https://github.com/sodejm/AncestryLLM/blob/main/docs/RELEASING.md)
when preparing a release.
