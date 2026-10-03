# uv_build evaluation

This record compares the production setuptools backend with `uv_build` for the
AncestryLLM 0.6 toolchain. It is an isolated evaluation, not a backend
migration. The checked schema-v1 report records the exact artifact hashes and
semantic differences for the evaluated commit.

## Disposition

| Item | Result |
| --- | --- |
| Production backend | `setuptools.build_meta` with `setuptools>=83` |
| Candidate backend | `uv_build` resolved to 0.12.3 from `uv_build>=0.12.0,<0.13` |
| Build frontend | Verified repository-local `uv 0.12.1` |
| Evaluated source | `b94f5b8338079d05bd0b386df6c5ec9b33b55422` |
| `SOURCE_DATE_EPOCH` | `1786426222` |
| Evaluation status | **Incompatible** |
| 0.7 decision | Adoption rejected/deferred under #305 for this candidate and configuration |

Setuptools remains the production backend. `make package`, release readiness,
and release construction continue to use the existing setuptools build path,
normalization, allowlists, `twine check`, wheel-content checks, installation
smokes, sdist reconstruction, and double-build reproducibility controls.

## Evaluation method

`make evaluate-uv-build` runs `scripts/evaluate_uv_build.py` from the locked
`build` dependency group. The harness:

1. requires a clean Git checkout and exact verified `uv 0.12.1`;
2. creates identical source trees from the same committed archive;
3. changes only the candidate tree's reviewed build-system configuration and
   explicit source include list;
4. uses the same Python, environment allowlist, and `SOURCE_DATE_EPOCH` for both
   backends;
5. builds setuptools once and `uv_build` twice, then reconstructs a wheel from
   each backend's sdist;
6. validates archive members before inspection, rejecting absolute paths,
   traversal, links, devices, duplicate members, and unexpected output files;
7. compares package files and bytes, module and migration paths, project and
   wheel metadata, entry points, `WHEEL`, `RECORD`, allowlists, reconstruction,
   and consecutive candidate hashes; and
8. installs each wheel with verified `uv`, `--no-index`, and `--no-deps` into an
   isolated target before checking import, installed metadata, the exact
   `ancestry` entry point, and `--help`.

Only archive-member ordering and archive metadata timestamps are normalized.
No payload, metadata value, file-set, entry-point, dependency, or `RECORD`
difference is normalized.

## Artifact evidence

| Backend and artifact | SHA-256 |
| --- | --- |
| setuptools sdist | `381606dc8beef94c0119e37149c2889041ed3fff543bc6c633838cb2de92e1e5` |
| setuptools wheel | `70a2cf2d1dcd3f6e0dc465845d09045d76ed9cc3768361a0230128212a7534b8` |
| setuptools wheel reconstructed from sdist | `70a2cf2d1dcd3f6e0dc465845d09045d76ed9cc3768361a0230128212a7534b8` |
| first `uv_build` sdist | `58cc907badcf3da9134d36922ff521a32a6b88adf1e0fbb041d8a8c98f8502d5` |
| second `uv_build` sdist | `58cc907badcf3da9134d36922ff521a32a6b88adf1e0fbb041d8a8c98f8502d5` |
| first `uv_build` wheel | `c02af932310b6a22900668f8ecc22c1df61528d57203a0ff03f0d349290ad60b` |
| second `uv_build` wheel | `c02af932310b6a22900668f8ecc22c1df61528d57203a0ff03f0d349290ad60b` |
| `uv_build` wheel reconstructed from sdist | `c02af932310b6a22900668f8ecc22c1df61528d57203a0ff03f0d349290ad60b` |

The candidate is internally reproducible: consecutive sdist and wheel hashes
match, and reconstruction produces the same wheel. Both backends pass the
isolated install, import, metadata, entry-point, and help smoke. Package payload
bytes, package module paths, Alembic migration paths, project-data paths, and
the `ancestry` entry point also match.

The artifact-equivalence gate nevertheless fails closed:

- the candidate sdist omits `LICENSE`, `setup.cfg`, and the generated egg-info
  records, while adding `pyproject.toml.orig` and changing `PKG-INFO`;
- the candidate wheel omits the installed license and `top_level.txt` and
  changes `METADATA`, `WHEEL`, `entry_points.txt`, and `RECORD`;
- project metadata and wheel metadata values differ semantically; and
- the existing source-distribution allowlist requires `LICENSE` and rejects the
  unexpected private `pyproject.toml.orig` file.

The complete machine-readable result is
[`release-evidence/uv-build-evaluation-v1.json`](../release-evidence/uv-build-evaluation-v1.json).
The report uses a closed schema, stable `UVBEVAL_*` failure codes, sorted
deterministic fields, and sanitized relative artifact paths. It is a checked
tooling evaluation record; it is not a release gate result or a substitute for
exact-candidate release evidence.

## Architecture and security impact

The evaluation adds a maintainer-only build tool and comparison harness. It
does not change the production backend, built application, CLI or REPL
commands, service DTOs, provider contracts, GEDCOM representation, RootsMagic
immutability, storage schema, FastAPI surface, or Electron boundary.

The candidate is acquired only from the complete lock through the verified
bootstrap. The harness passes no repository credentials to builds, performs no
index access during wheel installation, validates archives before extracting or
comparing them, and rejects absolute or local paths in its report. It records no
environment values, usernames, hostnames, temporary paths, response bodies,
genealogy data, or secrets. Missing license material, unexpected private files,
metadata drift, and nondeterminism are failures rather than accepted
normalizations.

## Reproduction and regression evidence

Run the evaluation from a clean checkout:

```bash
make setup
make evaluate-uv-build
```

An incompatible comparison intentionally returns a nonzero status after
writing `build/uv-build-evaluation.json`; inspect the report rather than
masking that status. Set `UV_BUILD_REPORT` to another repository-relative path
when a separate local record is needed.

The focused harness tests began red with the expected missing-module import
error before the implementation existed. They now cover the backend boundary,
candidate overlay, archive safety, exact `uv` version, file and semantic
comparisons, `RECORD` integrity, deterministic schema validation, report
sanitization, locked-runtime installation, stable failure phases, and the
checked fail-closed evidence. Production application behavior is unaffected,
so no application-level behavioral red test applies.
