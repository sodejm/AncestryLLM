# In-Code Documentation Policy

This document defines the repository-wide standard for in-code documentation across all
first-party source files. The tracked-file inventory and file/module purpose requirements
are machine-checkable via `make code-docs-check`. Python public-declaration requirements
are enforced by the same target through an AST-based semantic check and an explicit scoped
Ruff rule set. TypeScript, JavaScript, and Swift declaration enforcement remains part of
issue #256 and will follow in the desktop increment; manual review is not the accepted final
enforcement state.

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
| `first-party-config-exec` | Executable/behaviour-defining config | `*.yml`, `*.Dockerfile`, `Makefile`, `pyproject.toml` |
| `generated-vendor` | Generated or vendored output and exact reviewed vendor patch inputs | `uv.lock`, `pnpm-lock.yaml`, the allowlisted Electron patch |
| `test-data-fixture` | Fictional test data | `tests/fixtures/**/*.ged` |
| `non-code-doc` | Human-readable documentation/content and reviewed documentation images | `docs/**/*.md`, `docs/**/*.png`, `README.md`, `LICENSE` |
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
  those facts are not obvious. Private-named declarations explicitly listed in a literal
  top-level `__all__` assignment are treated as public exports by the semantic checker.
- Non-public code receives declaration-level documentation when its algorithm, invariant,
    state transition, or safety constraint is not obvious.
- Protocol and abstract methods document the contract they require implementations to
  preserve. Overrides own a local docstring rather than relying on inherited prose that may
  no longer describe specialized behavior.
- Overload signatures do not carry competing docstrings; the concrete implementation owns
  the public documentation. Ruff rule `D418` rejects docstrings on overload stubs.
- Tests use descriptive class and function names as their primary declaration documentation.
  `D101`, `D102`, and `D103` are therefore narrowly ignored only for `tests/**`; module and
  package documentation, empty docstrings, and overload ownership remain enforced there.
- Constructors and magic methods inherit the owning class contract by default, so the
  style-oriented `D105` and `D107` rules are not selected. A non-obvious constructor or
  magic-method invariant still requires focused documentation under the manual policy.

### TypeScript/TSX (`.ts`, `.tsx`)

- Every module must begin with a meaningful `/** … */` TSDoc block that states the
  module's purpose and primary responsibility. A leading license block may precede it.
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

- Swift DocC-compatible `///` comments document the file's purpose and all callable
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

1. Runs Ruff rules `D100`, `D101`, `D102`, `D103`, `D104`, `D418`, and `D419` across
   `src`, `tests`, and `scripts`, with only the documented test-declaration exception.
2. Classifies every Git-tracked file using `scripts/check_code_documentation.py`.
3. Verifies that every first-party source file has a meaningful, language-appropriate
   module/file-level purpose statement: Python docstrings, TSDoc/JSDoc blocks, Swift DocC
   lines, shell/config hash comments, HTML comments, or CSS comments.
4. Verifies meaningful public class, function, and method docstrings in production Python
   and repository scripts, including literal `__all__` exports plus protocol, abstract,
   override, and migration contracts.
5. Rejects empty or placeholder purpose statements, unknown file extensions in
   comment-capable categories, and unmapped non-comment formats.
6. Rejects any permanent documentation-violation baseline.
7. Emits stable `path:rule` diagnostics and exits non-zero on any violation.

The check is deterministic, offline, and does not call any provider or upload source. It
ignores a Git-index entry that has been deleted from the working tree, which lets a deletion
be validated before commit; exact-checkout CI still evaluates every file present in the
candidate commit.

The Python declaration gate adds no baseline or production-tree ignore. The same issue
remains open until public/exported declaration coverage for the desktop languages is
enforced with language-native rules and without blanket ignores.

## Suppression rules

Suppressions are narrow and justified:

- A `# noqa: D…` comment may suppress a single violation only when the policy explicitly
  allows it and the comment includes a rationale. No standing Python declaration
  suppression is required by the current source tree.
- Disabling an entire source or test tree is rejected.
- ESLint `eslint-disable` comments for TSDoc/JSDoc rules require an inline justification.

## Architecture and security impact

This policy and checker do not change an ancestry application API, CLI command, service
DTO, provider contract, GEDCOM representation, storage schema, FastAPI contract, or
Electron boundary. `ARCHITECTURE.md` therefore remains unchanged.

The checker adds no runtime dependency or trust boundary. It reads only tracked paths and
local source text, runs without network access or credentials, and emits path/rule codes
rather than source contents. Removing the baseline narrows the audit gap: a missing purpose
statement, unknown format, or undocumented non-comment file now fails closed instead of
being permanently allowlisted. The repository threat model is otherwise unchanged.

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
