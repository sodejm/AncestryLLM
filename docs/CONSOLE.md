# Console guide

Running `ancestry` with no arguments starts the supported interactive console.
The console is the asynchronous prompt-toolkit/Rich REPL installed by the main
package. It is the only interactive console surface; one-shot usage is
unchanged: `ancestry MODULE ACTION ...` continues to parse and dispatch the
documented CLI grammar.

The REPL is an input and presentation adapter over the same transport-neutral
command specifications and application services used by one-shot execution. It
does not provide a shell, Python evaluator, plugin loader, or separate business
logic path.

## Navigation and controls

At the root prompt, use `modules` to inspect available modules or `use MODULE`
to enter a module context. The active-module prompt makes the current context
visible:

```text
ancestry > modules
ancestry > use gedcom
ancestry(gedcom) > info
ancestry(gedcom) > show actions
```

The session controls are:

- `modules` lists enabled modules.
- `use MODULE` enters an enabled module; `back` returns to the root prompt and
  clears the module context.
- `info` and `show actions` describe the active module.
- `set NAME VALUE` saves a non-secret session option; `show options` displays
  saved options and `unset NAME` removes one.
- `run ACTION ...` runs an action using the saved options. Direct module
  actions may also be entered at the root prompt, such as `providers list`.
- `jobs` or `jobs list` shows background work; `jobs show JOB_ID` shows one
  job's timestamps, latest structured progress, result, or stable failure
  code. `help jobs` summarizes these controls.
- `exit`, `quit`, or EOF leaves the REPL.

Long-running RootsMagic queries/exports, GEDCOM operations, OCR extraction, and
database backups run in a bounded background worker pool so the prompt remains
responsive. The start response includes a stable job ID. Jobs move through
`queued`, `running`, `completed`, `failed`, and (when cancellation is
requested) `cancelled` states. Mutating jobs that target the same output,
manifest, or database resource are serialized; independent targets may run in
parallel. The queue rejects new work with `JOB_QUEUE_FULL` at its configured
64-job safety limit.

Active jobs render above the prompt through Rich `Live` while prompt-toolkit's
supported stdout patch keeps asynchronous updates from overwriting input.
Unknown-duration operations show a spinner and current operation. Structured
events with completed/total units render a determinate progress bar. Completion,
failure, and cancellation stop the live display, print one final sanitized
status line, and leave a clean prompt; full results remain available through
`jobs show JOB_ID`.

Progress operation text passes through the same secret-redaction boundary as
job failures. Live rendering is interactive-only: one-shot commands and
`--json` output preserve their existing non-animated, machine-readable
contracts.

## Multiline free text

In the interactive console, omit `--question` from `rootsmagic query` or omit
both `--body` and `--body-file` from `prompts save` to open the multiline
editor. The editor preserves newlines and Markdown exactly and passes the whole
value as one argument; it never parses entered prose as a command. Press
Esc+Enter to submit or Ctrl-C to cancel. Empty input is rejected, input is
limited to 100,000 characters, and multiline content is never written to REPL
history.

```text
ancestry > rootsmagic query --tree family --provider ollama --model llama3
Natural-language question (Esc+Enter to submit):
> Compare the two candidate birth records.
>
> Explain the evidence as a Markdown list.

ancestry > prompts save research-plan --purpose research --variable person
Prompt body (Esc+Enter to submit):
> Build a research plan for $person.
>
> Include sources and unresolved conflicts.
```

One-shot commands remain intentionally non-interactive: they require
`--question`, `--body`, or `--body-file` explicitly.

Secret-like option names are rejected by `set`. Use the dedicated `secrets`
commands and their no-echo prompts for secret operations; secret values are
never stored in session options.

## Parsing and command safety

The REPL uses the shared command specifications and strict command-line
parsing. Quoted values, escaped spaces, typed booleans and enums, repeated
flags, and `NAME=VALUE` forms are supported according to the command
specification. Malformed quoting, unknown commands or options, missing values,
invalid enum values, and invalid module/action combinations are rejected with
the same stable usage errors used by one-shot execution.

The REPL is not a shell or scripting engine. It rejects shell and Python
execution, script loading, aliases/macros, command substitution or other
expansion, pipes, redirects, and related shell syntax. There is no command
path that evaluates user input as Python or generated code.

## Command registration

Built-in modules are registered in the explicit module registry as
`ModuleDescriptor` entries with matching transport-neutral `CommandSpec`
metadata. The same command metadata drives one-shot argparse wiring, REPL
routing, help text, completion, and validation. Interactive commands are
therefore not authored as terminal-specific command classes; they are exposed
through descriptors, action specifications, argument specifications, and thin
service dispatchers that return serializable DTOs or stable coded errors.

A module can be disabled through configuration only when it is present in the
registry. Disabled modules are not available through direct root commands,
module context navigation, or completion.

## Tab completion

Completion is context-aware and read-only. At the root it offers commands and
enabled modules. In an active module it offers valid actions and controls. For
the current action it offers unused option flags, static enum values, enabled
modules, configured profile and consent names from a startup snapshot, and
static secret-reference types. Completion never suggests secret values or
keyring contents.

Completion also intentionally suppresses people, trees, prompts, workspaces,
and other genealogy or session data. Prompt names are suppressed even though
they appeared in the original completion issue description because they can
contain sensitive user data.

For arguments explicitly marked as file paths, completion is restricted to the
current working directory and its descendants. It excludes hidden entries and
symlinks, rejects `..` traversal and absolute paths outside the working
directory, and returns a bounded number of results. Completion does not access
the database, keyring, provider adapters, or network; it uses only command
metadata, the immutable startup snapshot, and the permitted local directory
listing.

## History and privacy

Interactive history is stored with owner-only permissions. Secret entry and
secret-like commands are excluded from history, and persisted history is
redacted defensively. Do not paste credentials, private genealogy records, or
other sensitive values into ordinary commands. Provider selection and consent
remain explicit: `provider=none` is network-free even when keys or SDKs are
installed.
