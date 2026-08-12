# In-Code Documentation Policy

This document defines the repository-wide standard for in-code documentation across all
first-party source files. Its file-level baseline is machine-checkable via
`make code-docs-check`; declaration-level expectations are reviewed manually against the
implementation, call sites, and `ARCHITECTURE.md`.

## Purpose

Documentation is a maintainability contract. Comments and docstrings must explain purpose,
externally relevant behavior, invariants, side effects, failure modes, ownership boundaries,
and privacy/security constraints where those facts are not already obvious from names and
types. Documentation must not merely restate syntax, duplicate type annotations, preserve
dead history, or contradict the code.

`ARCHITECTURE.md` remains authoritative for system architecture. In-code documentation
links to or summarises the applicable contract without redefining it.

## File classification

Every Git-tracked file belongs to exactly one classification. Unknown extensions in
comment-capable categories fail the inventory check. The `check_code_documentation.py`
script enforces this at every CI run.

| Classification | Description | Examples |
|---|---|---|
| `first-party-code` | Production source maintained as code | `src/**/*.py`, `desktop/src/**/*.ts` |
| `first-party-test` | Test source maintained as code | `tests/**/*.py`, `desktop/src/**/*.test.ts` |
| `first-party-script` | Build/release/tooling scripts | `scripts/*.py`, `scripts/*.sh` |
| `first-party-config-exec` | Executable/behaviour-defining config | `*.yml`, `*.yaml`, `Makefile`, `pyproject.toml` |
| `generated-vendor` | Generated or vendored output and exact reviewed vendor patch inputs | `uv.lock`, `pnpm-lock.yaml`, the allowlisted Electron patch |
| `test-data-fixture` | Fictional test data | `tests/fixtures/**/*.ged` |
| `non-code-doc` | Human-readable documentation/content | `docs/**/*.md`, `README.md`, `LICENSE` |
| `non-comment-format` | Formats or strict parser contracts that do not safely permit comments | `*.json`, `*.plist`, strict-JSON Compose manifests |
| `ide-config` | Editor/IDE configuration | `.vscode/**` |

### Non-comment formats

Files whose formats do not safely permit comments (JSON, plist/XML property lists) are
classified as `non-comment-format` and excluded from file-level documentation requirements.
This also applies to the three `containers/compose*.yaml` manifests: despite their suffix,
they intentionally remain strict JSON-compatible YAML so duplicate keys and non-JSON YAML
constructs fail closed. Their semantics must be explained in an adjacent authoritative
document mapped in `NON_COMMENT_FORMAT_MAP` inside `scripts/check_code_documentation.py`.

Vendor patch syntax does not have a portable file-header comment form. Such artifacts are
classified as `generated-vendor` only when their exact repository path appears in
`GENERATED_VENDOR_PATHS`; an unreviewed patch path remains an unknown extension and fails
closed. The Electron patch's purpose and locked integrity hash are documented in the desktop
installation and security guidance.

## Language standards

### Python (`.py`)

- Every module (including `__init__.py` and `__main__.py`) must have a PEP 257-compatible
  module docstring that states its purpose.
  - Exception: an `__init__.py` that contains only `from … import …` re-exports with no
    other logic may use a single-sentence summary docstring.
- Public classes, functions, and methods document semantics, parameters, return values,
    raised exceptions, side effects, invariants, and security/privacy constraints where
    those facts are not obvious.
- Non-public code receives declaration-level documentation when its algorithm, invariant,
    state transition, or safety constraint is not obvious.
- Ruff pydocstyle rules (`D` group) are not currently enabled. The automated gate checks
  module-level docstrings; the declaration-level expectations above are enforced in review.

### TypeScript/TSX (`.ts`, `.tsx`)

- Every module must have a `/** … */` TSDoc block before the first non-import statement
  that states the module's purpose and primary responsibility.
- Exported functions, classes, interfaces, types, constants, React components, hooks,
  IPC contracts, and security-sensitive internal boundaries require TSDoc-style `/** … */`
  documentation.
- Tags (`@param`, `@returns`, `@throws`, `@remarks`, `@example`) are used only when they
  add semantic value beyond what TypeScript types express.

### JavaScript/MJS (`.js`, `.mjs`)

- Every module must have a JSDoc block header.
- Exported declarations require JSDoc documentation.
- Type-bearing tags should be added where JavaScript lacks equivalent static type
  information.
- CLI scripts document inputs, outputs, exit behaviour, filesystem effects, and failure
  modes.

### Swift (`.swift`)

- Swift DocC-compatible `///` comments for the file's purpose and all callable
  declarations.
- Parameter, return, error, and platform/keychain behaviour documented where applicable.

### Shell (`.sh`)

- A file header that describes purpose, invocation, inputs/environment variables, outputs,
  exit statuses, supported platforms, filesystem effects, and secret-handling rules.
- Non-obvious functions and safety-critical command sequences receive inline comments.
- Credential values must never appear in examples.

### HTML/CSS

- File/block comments for renderer/layout responsibility, accessibility expectations,
  security-sensitive resource constraints, and non-obvious styling/layout decisions.
- Individual elements or selectors do not require comments.

### Makefile and comment-capable configuration (`.yml`/`.yaml`, `.toml`)

- Non-obvious targets, triggers, permissions, trust boundaries, platform behaviour,
  generated-source ownership, and release/security assumptions receive comments.
- Simple declarative entries that are self-explanatory do not require comments.

## Automated enforcement

Run `make code-docs-check` locally or in CI. The command:

1. Classifies every Git-tracked file using `scripts/check_code_documentation.py`.
2. Verifies that every first-party source file has a module/file-level purpose statement.
3. Rejects unknown file extensions in comment-capable categories.
4. Emits stable `path:rule` diagnostics and exits non-zero on any violation.

The check is deterministic, offline, and does not call any provider or upload source.

### Legacy baseline

`docs/CODE_DOCUMENTATION_BASELINE.txt` records the known violations present when
this first enforcement slice landed. The checker permits only those exact
`path:rule` diagnostics; any new violation still fails CI. Remove an entry when
its documented file is brought into compliance. This keeps the gate enforceable
while the repository is remediated in focused language/path batches.

## Suppression rules

Suppressions are narrow and justified:

- If a scoped Ruff pydocstyle check is introduced, a `# noqa: D…` comment may suppress a
  single violation only when the policy explicitly allows it (e.g., trivial `__init__.py`
  re-exports or self-documenting test functions). The comment must include a rationale.
- Disabling an entire source or test tree is rejected.
- ESLint `eslint-disable` comments for TSDoc/JSDoc rules require an inline justification.

## Reviewer expectations

Human review is required in addition to automated gates. Reviewers must confirm that:

- Documentation is accurate against the implementation, not merely syntactically valid.
- Comments at security, privacy, persistence, publication, provider, GEDCOM, RootsMagic,
  IPC, and release-signing boundaries are correct and complete.
- No credential values, private genealogy data, prompt/response payloads, local absolute
  user paths, or copied logs/reports appear in any comment, docstring, or example.
- Dead code found during review is removed or tracked in a dedicated issue rather than
  documented as live behaviour.

## Policy ownership

This document is maintained by the repository owners. Changes require a pull request with
evidence from `make code-docs-check`, `make lint`, `make test`, and `make typecheck`.
