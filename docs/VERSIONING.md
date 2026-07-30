# Versioning and compatibility

AncestryLLM follows [Semantic Versioning 2.0.0](https://semver.org/). Package
versions use `MAJOR.MINOR.PATCH`; Git tags add the conventional `v` prefix, so
package version `0.4.0` is tagged `v0.4.0`.

## Public API

The supported public API is:

- the `ancestry` and `python -m ancestryllm` command grammar documented in the
  CLI and console guides;
- documented process exit codes, stable coded errors, and `--json` payloads;
- documented `config.toml` keys and meanings;
- documented GEDCOM, manifest, encrypted backup, and database compatibility
  guarantees.

Internal Python modules are implementation details unless a document explicitly
declares an importable interface public.

## Version increments

During initial development (`0.y.z`), compatible fixes increment `PATCH`.
Backward-compatible features and intentional compatibility breaks increment
`MINOR`, reset `PATCH` to zero, and include migration guidance. Version `1.0.0`
will declare the public API stable; after that point incompatible public-API
changes increment `MAJOR`.

A published version is immutable. Its package files, Git tag, and release assets
must never be replaced, moved, deleted, or reused. A correction is always a new
version. Deprecations are documented in a minor release before removal whenever
the current compatibility line permits it.

Production publishing accepts stable `vMAJOR.MINOR.PATCH` tags only. Pre-release
publishing requires a separately reviewed workflow and is not enabled.
