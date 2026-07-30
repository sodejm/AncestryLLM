# Shared command executor contract

Status: implemented for the `0.3.0` development tree by issue #42.

The one-shot CLI and prompt-toolkit/Rich REPL use one execution path:

```text
CommandSpec -> shared parser -> CommandInvocation -> CommandExecutor
            -> focused command-family executor -> application service
            -> CommandOutcome -> terminal presentation
```

## Ownership

- `core/commands.py` owns command grammar, aliases, route identity, and dispatch
  metadata.
- `terminal/parser.py` generates terminal parsing from that metadata and
  converts parser-owned values to transport-neutral scalars and text tuples.
- `application/executor.py` owns immutable invocation, argument, outcome,
  handler, and registry contracts. It imports no terminal framework,
  provider SDK, storage implementation, configuration object, or host
  filesystem path.
- `execution/` composes focused handlers for modules, RootsMagic, GEDCOM,
  prompts, people, providers, secrets, OCR, and database maintenance.
  Handlers are adapters: they translate arguments and delegate policy and
  algorithms to existing services.
- `terminal/dispatch.py` owns terminal-only composition and presentation.
  `cli.py` and `console/shell.py` are sibling adapters over this path and do
  not import one another.

Every route in the single command specification has exactly one runtime
registration. Duplicate registrations fail construction; an unknown route
raises the stable `COMMAND_UNKNOWN` error with exit code 2. Parser route
metadata is checked against the selected command and action before execution.

## Compatibility and security

`CommandInvocation` contains only strings, integers, floats, booleans, null,
text tuples, a `DispatchKey`, and an optional opaque `SecretGrantRef`. It never
contains `argparse.Namespace`, `Path`, Rich objects, prompt-toolkit objects,
provider clients, database sessions, or secret values.

Secret entry remains a terminal responsibility. The terminal issues a
single-use, process-local grant only for `secrets set`; the focused handler
redeems it once, and dispatch revokes all grants in a `finally` block. The
secret value is never included in the invocation or result.

The migration preserves the shipped one-shot and REPL grammar, human and
`--json` presentation, stable coded errors and exit codes, explicit provider
selection and consent, network-free `none`, immutable RootsMagic handling,
loss-minimal/rooted GEDCOM behavior, and artifact/report result shapes.
Characterization and focused parity tests protect those boundaries.

## Extension rule

A new command is added to `core/commands.py` and the application operation
inventory first, then registered in the appropriate focused executor with
contract and CLI/REPL parity tests. A UI-specific registry, direct adapter
import, presentation call from `execution/`, or duplicated genealogy/provider
policy is an architecture violation.
