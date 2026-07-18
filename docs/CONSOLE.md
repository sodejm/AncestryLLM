# Console guide

Run `ancestry` with no arguments for the interactive console. `modules` lists
enabled features; `use gedcom`, `info`, and `show actions` enter and inspect a
module. Save non-secret action values with `set`, execute with `run ACTION`, and
return with `back`. Direct commands such as `gedcom --help` share the same parser
and dispatcher as one-shot use.

Never put a key in `set`: secret-like option names are refused. Use `secrets set`
and enter the value at the no-echo prompt. Shell/Python execution, script loading,
editing shortcuts, pipes, file redirection, and clipboard integration are not
part of the supported console.
