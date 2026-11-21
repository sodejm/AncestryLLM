# Desktop CodeQL guards

The main CodeQL workflow preserves the existing Python job and upload identity,
and adds `security-extended` analysis for JavaScript/TypeScript, Actions, and the
Swift signing helper.
JavaScript analysis also runs the two queries in `queries/`. Deliberately unsafe
regression fixtures are excluded from production analysis and evaluated separately
with `codeql test run .github/codeql/tests` before the JavaScript upload.

`RequireAuthorizedDesktopIpc.ql` checks privileged calls in callbacks registered
through `ipc.handle` or `ipcMain.handle` under `desktop/src/main`. A successful
`authorize` result for that callback's event must dominate the operation. This
recognizes the existing shared registration helper, aliases, early returns, and
positive branches. Merely calling `authorize`, authorizing another event, checking
after native work, conditional event replacement, and guards that only run on
one branch produce alerts.

`RequireConfirmedExternalOpen.ql` requires the result of `validateExternalLink` to
reach both confirmation and opening, with successful confirmation dominating the
open. It also checks the injected Electron adapter: the wrapper must resolve to a
guarded implementation, the adapter must forward its normalized parameter, and
the confirmation callback must display that parameter and return only the dialog's
affirmative response (`1`). Ignored confirmation, cancellation, bypass branches,
different destinations, conditional replacements, and confirmation adapters that
always approve produce alerts. Value checks require a unique local source and
reject primitive constants merged into that source, including fabricated guard
success.

These queries enforce repository conventions, rather than proving every possible
Electron integration safe. They trust the implementations of `authorize`,
`validateExternalLink`, and `externalLinkPrompt`, including the prompt's response
mapping; runtime security tests remain necessary. The IPC query uses the explicit
privileged-operation vocabulary in its source and named `ipc`/`ipcMain` receivers.
It checks work in the registered callback; the existing `schedule` boundary covers
its nested operation callbacks. New scheduling APIs, receiver aliases, renamed
guards, or native APIs require query and fixture updates. Confirmation adapter
modeling intentionally accepts the current explicit callback shape; other safe
abstractions can require an additional model.

Run the fixtures with a CodeQL bundle containing JavaScript packs:

```sh
codeql test run .github/codeql/tests --threads=2 --ram=4096
```

The expected files enumerate every unsafe fixture and omit every safe fixture.
Each `.qlref` contains the relative query path, and each `.expected` contains the
CLI's exact result table, including source locations and diagnostic text. These
four parser inputs carry no purpose comments; this README is their authoritative
documentation under the repository's exact-path documentation policy.
For sandboxed local runs, append `--common-caches=.cache/codeql/common`. Production
analysis must also remain clean when changing a query, so the fixtures cannot
silently replace validation against the real wrapper and registration helper.
This tooling change does not change desktop runtime behavior. Release-readiness
retains its separate Python CodeQL evidence contract.
