# Merge fictional GEDCOM records offline

This tutorial produces a meaningful, rooted GEDCOM 5.5.5 result and a quality
report from the repository's public fictional fixtures. It uses the supported
one-shot CLI through the checked-in helper; `provider=none` is fixed in every
merge, so the workflow makes no provider or network call even if credentials
are present in your environment.

Issue #56's internal asynchronous provider adapter does not change this
workflow. With `provider=none`, authorization fails before any provider worker
or stream queue starts, so installed SDKs and ambient credentials cannot turn
the tutorial into a network operation.

## What you will do

You will set up AncestryLLM, merge two deliberately fictional Aster records,
inspect the output paths, and see the same workflow reject malformed input
without publishing a partial file. The helper creates a new timestamped
directory, never changes its inputs, and keeps its temporary configuration and
data directories inside that run directory.

## Prerequisites

- A checkout of this repository and Python 3.12 or newer.
- A shell in the repository root.
- A disposable output parent such as `./ancestryllm-tutorial-output`. Do not
  use a directory containing real genealogy data, a RootsMagic database, a
  backup, or credentials.

The fixture GEDCOM files are public test data only. Do not substitute a
RootsMagic file: RootsMagic files are immutable inputs, and this tutorial does
not open a database.

## 1. Create the supported environment

Create the project environment once:

```console
make setup
```

If dependency installation cannot use your configured package index, stop
here. The tutorial's merge itself is offline, but initial package installation
is a separate setup operation and can contact that configured index.

## 2. Run the fictional merge

Create an empty parent for the disposable run and invoke the helper with the
environment you just created:

```console
mkdir -p ./ancestryllm-tutorial-output
ANCESTRYLLM_PYTHON="$PWD/.venv/bin/python" \
  scripts/gedcom_merge_quickstart.sh --skip-install \
  --output-dir "$PWD/ancestryllm-tutorial-output"
```

The helper runs the implemented one-shot command with two public fixture files,
`--provider none`, and root person `Aster Fiction`. `provider=none` is
network-free by contract; it does not become a cloud call because an API key or
provider SDK happens to be installed.

Do not add a cloud provider flag to this command. A cloud workflow requires an
explicit provider selection and the corresponding recorded consent before it
can send any genealogy-derived content. See [privacy and consent](../explanation/PRIVACY_AND_CONSENT.md)
and the [provider guide](../reference/PROVIDERS.md) before choosing one.

## 3. Verify the result

The final lines identify a newly created directory named
`gedcom-merge-<UTC timestamp>-<process id>` beneath your output parent. It
contains:

- `aster-fiction.ged`, a rooted GEDCOM result;
- `aster-fiction.quality.md`, the merge quality report; and
- isolated `.ancestryllm/config` and `.ancestryllm/data` directories used only
  for this tutorial run.

The helper also intentionally tries a malformed fictional GEDCOM file. A
successful tutorial ends by reporting that this attempt produced **no output
or report**. That is the failure-safe publication check: no partial GEDCOM or
quality report should appear for rejected input.

Open the two reported paths with a local text editor. Their fixtures exercise
the supported GEDCOM 5.5.5 workflow; see [GEDCOM compatibility and release
checks](../reference/GEDCOM_COMPATIBILITY.md) for compatibility limits and release
evidence.

## Recovery and cleanup

- If `make setup` fails, repair the Python or package-index problem and rerun
  it before using `--skip-install`; do not point `ANCESTRYLLM_PYTHON` at an
  unverified interpreter.
- If the merge reports an error, keep the printed error and inspect the
  fictional input named there. A rejected merge invocation publishes neither
  its GEDCOM nor its report. Correct only a disposable copy, then rerun the
  helper to obtain a fresh timestamped directory.
- To clean up, remove only the one timestamped directory printed by this run
  after you finish inspecting it. Keep the parent directory and never use a
  recursive cleanup command against a directory that could contain real data.

For a real-data workflow, make and test an encrypted backup before changing a
workspace, keep its key material separately in the OS credential store, and
follow [encrypted backup and recovery](../ENCRYPTED_BACKUPS.md). GEDCOM inputs
remain loss-minimal, but a backup is still the recovery point for any related
workspace data.
