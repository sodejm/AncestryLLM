# Run an offline GEDCOM merge

Use this guide when you want to run the supported GEDCOM 5.5.5 merge yourself
with the public fictional fixtures. It uses the implemented one-shot CLI and
`provider=none`; no provider, cloud, or network call is made by the merge.

## Prerequisites

- Run `make setup` from the repository root first.
- Use only the public files in `tests/fixtures/gedcom_adversarial/` for this
  guide. Do not paste credentials or private family records into a command.
- Choose a new, disposable output directory. The command below isolates its
  configuration and data under that directory and never modifies the inputs.

## Merge the fictional records

Run these commands from the repository root:

```console
RUN_DIR="$PWD/ancestryllm-offline-merge"
mkdir -p "$RUN_DIR/.ancestryllm/config" "$RUN_DIR/.ancestryllm/data"
ANCESTRYLLM_CONFIG_DIR="$RUN_DIR/.ancestryllm/config" \
ANCESTRYLLM_DATA_DIR="$RUN_DIR/.ancestryllm/data" \
  .venv/bin/python -m ancestryllm gedcom merge \
  tests/fixtures/gedcom_adversarial/xref-source-a.ged \
  tests/fixtures/gedcom_adversarial/xref-source-b.ged \
  --provider none \
  --root-person "Aster Fiction" \
  --quality-report "$RUN_DIR/aster-fiction.quality.md" \
  --output "$RUN_DIR/aster-fiction.ged"
```

## Verify the result

Success creates both of these nonempty files:

- `$RUN_DIR/aster-fiction.ged`, rooted at the fictional `Aster Fiction`;
- `$RUN_DIR/aster-fiction.quality.md`, the quality report for the same merge.

Review both locally before relying on them. The merge is loss-minimal for the
supported GEDCOM 5.5.5 workflow; consult [GEDCOM compatibility and release
checks](../GEDCOM_COMPATIBILITY.md) for the precise format boundaries rather
than assuming vendor-specific data has no limitations.

## Recover safely from an error

If the command fails, do not treat an output or report from a failed attempt as
usable. Keep the coded error, correct only a disposable fixture copy, and rerun
with new result-file names. AncestryLLM stages output so a rejected merge does
not publish a partial result bundle.

`provider=none` remains the safe default even when environment keys are set.
If you intentionally need a remote provider for a different workflow, select
that provider explicitly and record the required consent first; do not turn
this offline command into a cloud request by adding a credential. See
[privacy and consent](../PRIVACY_AND_CONSENT.md) and the [provider guide](../PROVIDERS.md).

For actual family data, preserve the original files and make a tested encrypted
workspace backup before the related workflow. Treat RootsMagic files as
immutable, and keep the backup key separate from the encrypted backup as
described in [encrypted backup and recovery](../ENCRYPTED_BACKUPS.md).

## Cleanup

After verifying the fictional result, remove only the disposable
`ancestryllm-offline-merge` directory you created for this guide. Do not use a
bulk cleanup command in a parent directory that might contain real genealogy
records, databases, backups, or exported reports.
