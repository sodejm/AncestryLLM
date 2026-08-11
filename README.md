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

The desktop shell is not a desktop genealogy application. It does not include
desktop genealogy or domain routes, files, jobs, providers, cloud accounts, or
updater flows. Desktop genealogy workflows are not available yet; use the CLI
or interactive prompt for supported genealogy work.

The current unreleased 0.6 source adds a narrow desktop settings and credential
management foundation. It can update five reviewed non-secret settings and can
set, delete, or report only the presence of allowlisted credentials through the
OS keyring. It cannot read credential values, select consent on your behalf,
make a provider call, or run a genealogy workflow. This development surface is
not part of the released 0.5.0 installer until its packaged verification gates
pass.

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
   confirm healthy Diagnostics. Read the
   [desktop installation and verification procedure](https://github.com/sodejm/AncestryLLM/blob/main/docs/DESKTOP_SHELL.md#installation-and-updates)
   before downloading.

For examples and a complete command reference, read the
[CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CLI.md). For
help using the interactive prompt, read the
[REPL guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md).

## Privacy and provider choices

AncestryLLM works locally by default. Provider `none` is network-free even if
environment keys or provider software are present. A cloud call requires
explicit provider selection and your consent; installed packages and environment
keys never choose a remote provider for you.

Credentials belong in your OS keyring. Keep real genealogy records, exports,
backups, logs, prompts, and secrets out of the repository. Learn the details in
the [privacy and consent guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/PRIVACY_AND_CONSENT.md)
and the [provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/PROVIDERS.md).

## Learn more

- [Understand the released desktop shell and its limits](https://github.com/sodejm/AncestryLLM/blob/main/docs/DESKTOP_SHELL.md).
- [Check GEDCOM compatibility and interoperability limits](https://github.com/sodejm/AncestryLLM/blob/main/docs/GEDCOM_COMPATIBILITY.md).
- [Learn how AncestryLLM protects file imports](https://github.com/sodejm/AncestryLLM/blob/main/docs/FILE_INGRESS.md).
- [Read the documentation site](https://sodejm.github.io/AncestryLLM/) for
  guides and reference material.
- [Contribute to AncestryLLM](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md)
  if you are working on the project itself.
- [Maintain locked dependency environments](https://github.com/sodejm/AncestryLLM/blob/main/docs/DEPENDENCY_MAINTENANCE.md)
  when changing application extras or repository tool groups. Source checkouts
  use `make setup`; the former `dev` extra is no longer an installation path.
