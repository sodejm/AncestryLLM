# ty advisory evaluation

This record evaluates `ty` beside the authoritative strict-mypy gate for the
AncestryLLM 0.6 development toolchain. It records the exact checker, complete
source-tree results, parity evidence, and the disposition of every diagnostic.
It does not authorize the conditional 0.7 type-checker cutover.

## Evaluation contract

- Candidate: `ty 0.0.69` (`1dca8443e`, 2026-08-06), pinned exactly in the
  `typecheck` dependency group and resolved by `uv.lock`.
- Authoritative checker: strict mypy with `pydantic.mypy`, run by
  `make typecheck` over `src/ancestryllm`.
- Advisory checker: `make typecheck-ty`, which runs
  `ty check src/ancestryllm` and preserves ty's real exit status.
- CI: a dedicated Python 3.12 quality step has `continue-on-error: true`. The
  blocking mypy step remains separate and does not mask either checker's exit
  status.
- Evidence profile: the CI-equivalent quality environment installs only `lint`
  and `typecheck`, with no provider extras. A second full-setup run records how
  the advisory changes when `make setup` installs every optional provider SDK.
- Local characterization: Python 3.14.6 on macOS ARM64. The supported Python
  range remains 3.12-3.14, with Python 3.12 as the repository and CI default.

The evaluation started from commit
`a336fbfb88263e256843f6345ff0c32fd3723d11`. Strict mypy passed all 133 source
files before and after the focused defect fix. The final ty run returned status
1 and reported 58 diagnostics in the clean quality profile, as expected for a
visible advisory failure. After `make setup`, the same command reported 53
diagnostics because five optional provider imports became resolvable.

## Diagnostic triage

The first complete quality-profile ty run reported 59 diagnostics. One Genuine
code defect was fixed and protected by a focused regression test; the final
quality-profile count is 58. The full-setup column is a second post-fix run, not
an alternate acceptance profile.

| Category | Initial quality profile | Final quality profile | Full setup | Disposition |
|---|---:|---:|---:|---|
| Genuine code defect | 1 | 0 | 0 | Fixed in a separate behavioral commit with a focused red-green regression test. |
| Pydantic/model behavior gap | 7 | 7 | 7 | Retained as advisory evidence; ty rejects the dynamically typed model-settings expansion in `llm/profiles.py`. |
| Missing third-party typing | 8 | 8 | 3 | The quality profile exposes five optional provider-import diagnostics and three `sqlcipher3.connect` diagnostics; installing all provider extras resolves only the former five. Strict mypy's reviewed module overrides continue to cover them. |
| ty defect or unsupported feature | 43 | 43 | 43 | Retained for checker/platform incompatibilities described below; no broad ignore was added. |
| **Total** | **59** | **58** | **53** | ty does not yet pass the complete source tree in either environment. |

CI deliberately uses the final quality profile because quality jobs cannot gain
provider extras without a demonstrated runtime need. The 58-to-53 change is
therefore an explained dependency-profile effect, not a nondeterministic checker
result. Both runs covered every file under `src/ancestryllm`.

The genuine defect was a structural protocol mismatch:
`_CurrentProgressAdapter.emit` accepted a parameter named `update`, while the
declared `ProgressPort.emit` keyword was `event`. Calling the implementation
through that public keyword raised `TypeError`. The focused test first observed
that failure; the adapter now uses the declared keyword and strict mypy remains
green.

The 43 checker or unsupported-feature diagnostics comprise:

- 32 non-Windows-host diagnostics for guarded `ctypes` and `msvcrt` APIs used
  by cross-platform atomic-publication and immutable-source controls;
- four `list` annotation diagnostics where ty resolves a method named `list`
  instead of the built-in generic;
- three declaration or inference disagreements in GEDCOM identity handling;
- two generic cached-result diagnostics already covered by narrow mypy return
  annotations;
- one synchronization-kernel iterable-narrowing disagreement; and
- one redundant-cast warning.

These classifications describe the current evidence, not permanent
suppressions. A later evaluation must re-run the complete tree and reconsider
each diagnostic against the then-current ty release.

## Parity evidence

`scripts/check_typechecker_parity.py` runs both checkers against intentionally
invalid, isolated fixtures. One fixture is a direct language assignment error;
the other is an invalid Pydantic validator return. Both mypy and ty report the
expected file and line. Stable `TYPEPARITY_DIAGNOSTIC_MISSED` and
`TYPEPARITY_CHECKER_FAILED` codes distinguish a missed defect from a checker
execution failure.

The fixture result is narrow evidence only. It does not close the Pydantic
runtime gap: the full-tree run still produces seven model-construction
diagnostics, and strict mypy continues to use `pydantic.mypy`. Existing strict
mypy defects must remain detected by ty or protected by equivalent focused
tests before any cutover.

## Suppressions

- Source `# type: ignore` comments: 25 before and 25 after the evaluation.
- Source `# ty: ignore` comments: 0 before and 0 after the evaluation.
- New module-wide or package-wide ignores: 0.
- New unchecked packages: 0.

No suppression was added to make advisory output appear cleaner. Existing
mypy configuration, the Pydantic plugin, and editor/cache ownership remain
unchanged.

## Execution time

The clean final quality-profile run took 0.78 seconds elapsed, 1.16 seconds user,
and 0.17 seconds system time. Two consecutive warm final runs produced the
same 58 diagnostics in 0.16 and 0.17 seconds elapsed (1.05 seconds user and
0.07 seconds system each). These local measurements characterize one macOS
ARM64 environment and are not a cross-platform performance guarantee.

## Decision and future gate

The 0.7 cutover gate is not satisfied by this evaluation: ty does not pass the
complete source tree, model behavior is not yet equivalent, and platform-aware
results contain materially misleading diagnostics. The disposition is that
mypy remains authoritative for CI, release readiness, and release evidence.
The release-evidence result is still schema v1 `mypy`; it is not renamed to
`type-check`.

The conditional cutover must remain a separate change. It may proceed only if
the complete gate in issue #309 passes without reduced strictness, broader
suppressions, ignored modules, unsupported Python versions, crashes,
nondeterminism, or unresolved misleading diagnostics.

## Architecture and security impact

The advisory adds one exact, lock-hashed development checker and a nonblocking
quality step. It adds no runtime dependency, network path, privilege boundary,
user installation change, or executable trust exception; the verified `uv`
bootstrap and locked `typecheck` group remain the only acquisition path.

Apart from the keyword-compatible progress-adapter fix, application behavior
is unchanged. No ancestry API, CLI registry, service DTO, provider contract,
GEDCOM representation, RootsMagic write boundary, storage schema, FastAPI
contract, or Electron boundary changes. `provider=none` remains network-free,
cloud calls still require explicit consent, RootsMagic sources remain
immutable, and GEDCOM processing remains loss-minimal. `ARCHITECTURE.md` and
the threat-model evidence record this tooling-only disposition; installation,
release-runbook, and editor instructions are intentionally unchanged during
the advisory period.
