---
name: docs-screenshot-regeneration
description: Safely regenerate one reviewed AncestryLLM documentation screenshot scenario or surface and report the result without changing Git or GitHub state.
license: MIT
---

# Documentation screenshot regeneration

Use this skill only when a maintainer asks to regenerate a selected documentation
screenshot scenario or surface. It is an orchestration workflow: the checked-in
manifest, Make targets, and capture adapters retain ownership of validation,
rendering, publication, and drift detection.

## Authority and boundaries

- Treat `config/docs-screenshot-manifest.json` as the single source of truth for
  scenarios, fixtures, owning documentation, and the `output_allowlist`.
- Regeneration authority permits writes only to the selected manifest-declared
  screenshot destinations. It does not authorize edits to the manifest,
  fixtures, application code, owning documentation, or any other path.
- There are no staging, commits, pushes, or pull requests in this workflow.
  Request separate authorization for any later Git or GitHub action.
- Never accept a changed baseline merely to make a gate pass, and never invoke
  an adapter directly or recreate the capture pipeline.

## Preflight

1. Read the repository instructions, `config/docs-screenshot-manifest.json`, and
   `docs/DOCS_AUTHORING.md`. Resolve the requested selector to declared scenario
   IDs, their surfaces, exact output paths, and owning documentation.
2. Run `git status --short --untracked-files=all`. If unrelated work overlaps a
   selected output or its owning documentation, the requested output is already
   modified, the worktree boundary is unclear, or safe attribution is otherwise
   impossible, stop before capture. Do not clean, reset, stash, or revert user
   work.
3. Fail closed unless every selected scenario is in the manifest, every output
   is an exact member of `output_allowlist`, and every fixture is fictional with
   provider `none` and network `disabled`. Unknown selectors, unsafe or
   symlinked destinations, and writes outside the repository are failures.
4. On macOS, require a running Docker Desktop or compatible engine capable of
   native Linux containers. In CI, require the reviewed Linux setup. Treat a
   missing engine, unsupported architecture, missing dependency, or incomplete
   platform result as a failure rather than a pass.
5. The canonical pipeline must verify the pinned rendering tool versions before
   publication and enforce its privacy canary, isolated temporary state,
   deterministic double capture, and allowlisted-write controls. Do not bypass
   or relax a failed preflight.

## Capture the reviewed selection

For one scenario, run exactly:

```console
make docs-screenshots DOCS_SCREENSHOT_SCENARIO=<scenario-id>
```

For every scenario on one surface, run exactly:

```console
make docs-screenshots DOCS_SCREENSHOT_SURFACE=<surface>
```

Use an ID or surface declared by the manifest. Preserve the command's real exit
status. A nonzero status, interrupted capture, or incomplete result stops the
workflow.

## Review and full drift validation

1. Re-run `git status --short --untracked-files=all` and inspect the selected
   image diff. Fail if any undeclared path changed or if a selected output is not
   attributable to the completed capture.
2. Visually review each selected fictional image against its owning
   documentation. Classify each scenario as `changed/regenerated` when its
   committed bytes changed or `unchanged` when the deterministic output already
   matched.
3. Run the complete, unfiltered gate:

   ```console
   make docs-screenshots-check
   ```

   This full-manifest check is required even after a focused capture. Preserve
   its real status and treat changed pixels, missing images, privacy canaries,
   invalid PNGs, broken ownership, or incomplete architecture coverage as
   failures.
4. Confirm once more that only the selected allowlisted images changed. Leave
   all changes unstaged.

## Final report

Report:

- the selected scenario IDs and surfaces;
- each exact asset path and its owning documentation;
- `changed/regenerated` or `unchanged` for every selected scenario;
- prerequisite and pinned-tool verification outcomes;
- the full-manifest privacy and drift result; and
- every blocker or incomplete platform result.

Do not include fixture contents, pixels, transcripts, response bodies,
environment values, credentials, usernames, hostnames, or absolute paths.
