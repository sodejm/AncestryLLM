# AncestryLLM

AncestryLLM helps you explore your family history using your own files — privately,
on your own computer. It reads RootsMagic databases and GEDCOM files, runs
genealogy-specific analysis, and lets you optionally ask questions of a local or
cloud AI model that you choose and configure yourself.

No account, subscription, or internet connection is required to get started.

## What you can do today

The current release provides a **command-line interface (CLI)** and an **interactive
console** (REPL) that work on macOS, Linux, and Windows:

- Read and export data from RootsMagic databases without modifying them
- Work with GEDCOM files: merge, analyze quality, extract subtrees, and export
- Manage research notes and provenance for people in your tree
- Run AI-assisted analysis or OCR extraction — only when you explicitly configure a provider
- Store API keys securely in your operating system's credential store

A desktop application is in development and is not yet available as a supported release.

## Getting started

**Requirements:** Python 3.12–3.14 and a working OS credential store.

Install with [pipx](https://pipx.pypa.io/) for an isolated, self-contained command:

```bash
pipx install ancestryllm
ancestry --version
ancestry --help
```

Run `ancestry` with no arguments to open the interactive console. Run
`ancestry <command> --help` for one-shot use.

For full command reference and examples, see the
[CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CLI.md) and the
[interactive console guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md).

## Privacy and AI providers

AncestryLLM is **local-first**: your genealogy files stay on your computer. The tool
never sends data to any cloud service unless you explicitly set up a provider profile
and invoke a command that uses it.

- **No provider required.** All file-based features work fully offline.
- **Provider selection is explicit.** Installing an optional provider package or setting
  an environment key does not enable cloud calls. You must configure a named profile and
  consent to each use.
- **Supported providers** (all optional): Ollama (local), OpenAI, Anthropic, Gemini, and OpenRouter.
  Install all provider extras with `pipx install 'ancestryllm[all-llm]'`.
- **Credentials** are stored in the OS keyring and never appear in logs or status output.

See [Privacy and consent](https://github.com/sodejm/AncestryLLM/blob/main/docs/PRIVACY_AND_CONSENT.md)
and the [provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/PROVIDERS.md)
for full details.

## Learn more

| Topic | Guide |
|---|---|
| Full command reference and examples | [CLI guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CLI.md) |
| Interactive console usage | [Console guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/CONSOLE.md) |
| Privacy and AI provider consent | [Privacy and consent](https://github.com/sodejm/AncestryLLM/blob/main/docs/PRIVACY_AND_CONSENT.md) |
| Configuring AI providers | [Provider guide](https://github.com/sodejm/AncestryLLM/blob/main/docs/PROVIDERS.md) |
| GEDCOM file compatibility | [GEDCOM compatibility](https://github.com/sodejm/AncestryLLM/blob/main/docs/GEDCOM_COMPATIBILITY.md) |
| Encrypted backup options | [Encrypted backups](https://github.com/sodejm/AncestryLLM/blob/main/docs/ENCRYPTED_BACKUPS.md) |
| File format limits and error codes | [File ingress policy](https://github.com/sodejm/AncestryLLM/blob/main/docs/FILE_INGRESS.md) |
| Security model and threat model | [Threat model](https://github.com/sodejm/AncestryLLM/blob/main/docs/THREAT_MODEL.md) |
| Contributing and architecture | [CONTRIBUTING.md](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md) and [ARCHITECTURE.md](https://github.com/sodejm/AncestryLLM/blob/main/ARCHITECTURE.md) |

The full documentation is published at the
[AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/).
