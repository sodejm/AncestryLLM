# Console guide

Run `ancestry` with no arguments for the interactive console. It shares the
one-shot parser and dispatcher in [the CLI reference](CLI.md), so an action such
as `gedcom --help` has the same syntax and errors as `ancestry gedcom --help`.

Start by listing modules and entering a context:

```text
ancestry > modules
ancestry > use gedcom
ancestry(gedcom) > info
ancestry(gedcom) > show actions
```

Within a context, `set NAME VALUE` stores a non-secret option, `show options`
displays saved options, and `unset NAME` removes one. `run ACTION` executes an
action with those options; alternatively set `action` and run without an action.
`back` returns to the top-level prompt and clears the module context. For example:

```text
ancestry > use gedcom
ancestry(gedcom) > set output quality.md
ancestry(gedcom) > set root-person "Ada Lovelace"
ancestry(gedcom) > run quality tree.ged
```

Module commands may also be run directly from the top-level prompt, for example
`providers list` or `secrets status`. Never put a key in `set`: secret-like
option names are refused. Use `secrets set` and enter the value at the no-echo
prompt.

Shell/Python execution, script loading, editing shortcuts, pipes, file
redirection, and clipboard integration are not supported. The console has no
special path around provider policy: `none` remains offline by default, and a
remote call still requires an explicit provider and matching consent.
