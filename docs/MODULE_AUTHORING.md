# Built-in module authoring

A built-in module is registered through the explicit module registry with a
`ModuleDescriptor` and transport-neutral `CommandSpec`. The descriptor records
the module identity, summary, implementation path, and supported action names.
The command specification records action metadata and typed arguments used by
both one-shot CLI execution and the prompt-toolkit/Rich REPL.

Module authors should add or update the command specification first, then wire
the action to a thin dispatcher that delegates to an application service. The
dispatch layer must not open storage directly, read secrets, call providers, or
implement business rules. Services return serializable DTOs or stable
`AncestryError` instances so terminal, JSON, and future adapters can present the
same result contract.

Long-running REPL actions are submitted through the UI-independent job manager.
Its `JobReporter` accepts a current operation and, when known, validated
completed/total units. The latest redacted progress event becomes part of the
serializable job snapshot; the Rich presentation adapter decides whether to
render it as a spinner or progress bar. Module and service code must never
construct Rich objects, write terminal control sequences, or depend on
prompt-toolkit. One-shot dispatch does not create a reporter or emit animation.

Interactive behavior is derived from the shared metadata:

- one-shot `ancestry MODULE ACTION ...` parsing and validation;
- root-level direct module commands in the REPL;
- active-module `run ACTION ...` routing;
- help text, action listings, and option validation; and
- privacy-filtered completion.

Free-text prompting is also an input-adapter concern. The REPL may collect a
missing natural-language question or saved-prompt body through its reusable
multiline editor, then inject the preserved text as one validated argument.
Command specifications and services continue to receive ordinary strings and
must not import or invoke prompt-toolkit themselves. One-shot commands never
open the editor.

Do not author terminal-specific command classes for new modules. A module
should not depend on prompt-toolkit, Rich, or any console input framework. Rich
rendering belongs in presentation adapters only, and JSON output must remain a
serialization of the same service result.

The user-facing command inventory belongs in [the CLI reference](CLI.md),
rather than module-specific documentation. Modules are built-in only in v1. Add
tests proving disabled modules are not imported, commands are unavailable,
service DTOs serialize, secrets stay out of history, and offline defaults make
no network calls.
