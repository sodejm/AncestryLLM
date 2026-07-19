# REPL architecture and compatibility boundary

The interactive console is a local UI over the same application services used
by the one-shot CLI.  This document defines the migration boundary before a
`prompt_toolkit` REPL replaces the legacy console.

## Layers

1. Input owns terminal reads, multiline editing, completion, history, and EOF.
   It never interprets shell syntax or accesses databases/providers directly.
2. Session routing owns active-module state and non-secret saved options.  It
   routes a parsed invocation to an executor and never renders terminal text.
3. Command execution validates typed arguments and calls application services.
   It returns serializable DTOs or stable `AncestryError` instances.
4. Services enforce consent, endpoint policy, immutable source handling, and
   provider `none` offline behavior.  They do not import UI libraries.
5. Presentation renders DTOs, progress, and coded errors.  Rich objects stay
   in this layer; JSON remains a serialization of the same DTOs.

## Compatibility contract

- One-shot commands, `--json` output, stable error codes, and documented exit
  codes remain supported throughout the migration.
- The REPL cannot execute shell commands, Python, scripts, pipes, redirects,
  aliases, or macros.
- Secret entry is no-echo, secrets never enter history, and diagnostic output
  must redact registered sensitive values.
- Provider selection and consent stay explicit.  `provider=none` remains
  network-free even when keys or provider SDKs are installed.
- Long-running work reports structured progress and may run asynchronously,
  but cancellation must preserve atomic GEDCOM and workspace writes.

## Allowed dependencies

`input -> routing -> execution -> services` is the only execution direction.
Presentation receives results from execution but is never imported by services.
The one-shot CLI and REPL are sibling adapters over the same execution and
service contracts.
