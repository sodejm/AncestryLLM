# Desktop diagnostics contract

AncestryLLM desktop diagnostics are local, bounded, and deliberately less expressive than ordinary application logs. They exist to correlate startup, readiness, recovery, and verified shutdown without retaining genealogy records or user activity.

## Version 1 event

Every JSON Lines record conforms to `schemas/desktop-diagnostic-v1.schema.json` and contains only:

- a schema version, UTC timestamp, random per-run UUID, stable event code, severity, component, and application version;
- up to eight metadata counters or booleans.

Metadata does not accept strings, arrays, objects, exception text, stack traces, URLs, paths, prompts, names, database identifiers, provider keys, environment values, or genealogy content. A distinct stable event code must represent each useful failure or state instead of copying free-form error details.

## Retention and permissions

Each component writes a separate file (`electron-main.jsonl`, `python-core.jsonl`, or `desktop-sidecar.jsonl`) to avoid cross-process append races. The default limit is 512 KiB per file and three files per component, including the active file. Writers request owner-only directory and file permissions where the platform supports them and refuse symbolic-link targets.

All validation, directory creation, rotation, append, and clear failures are non-blocking. Diagnostics must never alter application startup, sidecar recovery, or verified shutdown behavior.

## Safe support workflow

The lifecycle integration must expose native actions that locate, export, and clear only the dedicated diagnostics directory. Before sharing an export, users should inspect it and confirm that records contain stable codes and bounded numeric metadata only. Diagnostics files must not be committed to the repository or attached to public issues without review.

The current contract module is the foundation for issue #461. Lifecycle event wiring, one Electron-generated run identifier propagated to the sidecar, native locate/export/clear actions, and exact-head packaged evidence remain required before the issue is complete.
