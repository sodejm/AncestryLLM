# Ruff rule-expansion evaluation

This record tracks the reviewed Ruff 0.16.1 rule expansion for AncestryLLM
0.6. Each batch starts from captured diagnostics, changes only the named rule
families, and ends with focused regression evidence before the next batch
begins. Ruff remains lock-resolved at 0.16.1 throughout the evaluation.

The expansion deliberately excludes `ALL`, docstring rules (`D`), blanket line
length enforcement (`E501`), and unsafe bulk fixes. CI selects GitHub annotation
output through `RUFF_OUTPUT_FORMAT=github` while continuing to call the
canonical `make lint` target.

## Typing and import rules

The first batch enables `TC` and removes the redundant `target-version =
"py312"`; the project-wide `requires-python` floor remains the Python-version
authority. Ruff initially reported 274 findings:

| Rule | Findings | Disposition |
| --- | ---: | --- |
| `TC001` | 108 | Annotation-only application imports moved behind `TYPE_CHECKING`; runtime constructors, aliases, and factories remained eager. |
| `TC002` | 24 | Annotation-only third-party imports moved behind `TYPE_CHECKING`; Pydantic runtime evaluation remains explicitly characterized. |
| `TC003` | 112 | Annotation-only standard-library imports moved behind `TYPE_CHECKING`; runtime path, subprocess, and parser objects remained eager. |
| `TC004` | 2 | Imports needed at runtime moved out of typing-only blocks. |
| `TC006` | 28 | The selected safe quote fix was previewed, reviewed, and applied. |
| **Total** | **274** | The completed batch has zero Ruff findings and adds no suppression. |

The only global runtime-evaluation exception is
`pydantic.BaseModel`. Focused construction and JSON round-trip tests cover the
affected `GenerationRequest` and `CapabilityManifest` models so a typing import
cannot silently break model creation or serialization. Runtime aliases and
objects used by constructors were kept outside `TYPE_CHECKING`; no module-wide
or package-wide ignore was added.

Provider adapters continue to import optional SDKs only inside the call
boundary that uses them. A static AST contract rejects module-scope imports of
`anthropic`, `google.genai`, `ollama`, or `openai`. Isolated subprocess tests
also create every built-in provider selection and import the CLI while proving
that none of those SDK modules is eagerly loaded. This preserves a network-free
`provider=none` startup and prevents unrelated providers from inheriting an
SDK import cost.

### Startup evidence

`scripts/benchmark_cli_import.py` performs isolated cold imports and emits a
sanitized schema-v1 JSON record. It reports only the Python version, bounded
timing samples, and any provider SDK module names; it contains no environment
values, paths, credentials, provider responses, or genealogy data.

The before and after measurements used Python 3.14.6 on macOS ARM64 with 15
fresh processes each:

| Measurement | Before | After | Change |
| --- | ---: | ---: | ---: |
| Median CLI import | 703.294 ms | 705.395 ms | +2.101 ms (+0.3%) |
| Provider SDK modules loaded | 0 | 0 | No change |

The median change is below both the existing 100 ms absolute and 10% relative
characterization thresholds, so it is not a material regression. The before
run contained one 2,490.432 ms outlier; acceptance is based on the median, as
defined by the existing characterization policy.

### Regression evidence

- Configured Ruff check and format check pass across all 347 Python files.
- Strict mypy passes all 133 source files.
- The focused provider, Pydantic, workflow, and benchmark contract has six
  passing tests.
- All 1,020 tests in Python test modules changed by this batch pass.
- The focused GEDCOM suite passes 343 tests with three expected skips.
- The complete core-contract capture passes all 51 nodes across GEDCOM,
  CLI/REPL, provider consent/offline behavior, incremental recovery, and
  immutable RootsMagic groups. Its semantic digest is
  `d4394e3eb52dba6b0302ad40d87e411981b0da35b715aff4ff885f82ce61799e`.

The capture report was written outside the repository, as required by the
characterization runner. Timing values characterize this one development host;
the supported Python 3.12-3.14 CI matrix remains authoritative.

## Performance and modernization rules

The second batch enables `PERF`, `C4`, and `FURB`. Ruff initially reported 35
findings:

| Rule | Findings | Disposition |
| --- | ---: | --- |
| `PERF401` | 18 | Append-only loops became generator-backed `list.extend` calls after review of source order, filtering, partial mutation, and exception timing. |
| `C420` | 8 | Constant-valued comprehensions became `dict.fromkeys` only where the value is immutable; explicitly sorted key insertion remained sorted. |
| `FURB162` | 6 | ISO timestamp parsing now relies on Python 3.12's native trailing-`Z` support, with existing UTC, offset, naive, and malformed-input tests retained. |
| `FURB192` | 2 | Nonempty string sets use `min` instead of sorting the complete set before selecting the first lexical value. |
| `C408` | 1 | One test-only `dict` constructor became an equivalent literal. |
| **Total** | **35** | The completed batch has zero Ruff findings and adds no suppression. |

No unsafe fix was applied. The complete proposed diff was previewed with Ruff,
then each finding was changed by hand. GEDCOM finding collection, alternate-name
serialization, RootsMagic continuation and family-member output, release-gate
ordering, and architecture/audit violation ordering remain deterministic. The
generator-backed extensions consume each source once and preserve incremental
list population if construction raises.

### Regression evidence

- The focused release, desktop, GEDCOM, sync/recovery, RootsMagic,
  architecture, characterization, benchmark, and Semgrep suites pass all 758
  tests.
- Strict mypy passes all 133 source files, and configured Ruff check passes the
  complete repository.
- The complete core-contract capture again passes all 51 nodes. Its semantic
  digest remains
  `d4394e3eb52dba6b0302ad40d87e411981b0da35b715aff4ff885f82ce61799e`,
  exactly matching the typing-batch capture.

This is a static-policy and semantics-preserving modernization batch, so no new
behavioral red test applies. The Ruff configuration contract was observed
failing before the rule families were selected; existing acceptance,
rejection, adversarial, and characterization tests then guarded the potentially
observable transformations.

## Language and correctness rules

The reviewed `UP`, `SIM`, `RET`, `PTH`, `DTZ`, `LOG`, and `ASYNC` batch is
pending. It will preserve cross-platform path behavior, timezone-aware
semantics, logging redaction, and cancellation boundaries.

## Architecture and security impact

The typing batch changes repository static-analysis policy and annotation-only
import placement; it does not change an ancestry API, CLI command registry,
service DTO, provider contract, GEDCOM representation, storage schema, FastAPI
contract, or Electron boundary. The transport-neutral architecture documented
in `ARCHITECTURE.md` is therefore unchanged. The runtime Pydantic and provider
import contracts specifically guard the two import-placement boundaries that
could otherwise affect application startup.

No runtime dependency, network operation, executable trust root, privilege,
secret flow, or release-evidence schema is added. The verified `uv` bootstrap
and locked `lint` group remain the acquisition and execution boundary for
Ruff. Consequently the threat inventory and control ownership in
`THREAT_MODEL.md` do not change. Existing controls remain intact: cloud calls
require explicit provider selection and consent, `provider=none` stays
network-free, RootsMagic sources remain immutable, and GEDCOM handling remains
loss-minimal.
