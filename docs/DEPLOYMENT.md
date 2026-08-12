# Deployment operations

AncestryLLM is local-first software. This document covers the hosted controls
used to build and publish its desktop installers; it does not describe a
hosted application deployment.

## Deployment-profile status

[ADR-0026](ADR-0026-local-first-container-remote-deployment.md) accepts a
future Local Desktop container profile plus explicit Connect Remote and Host
Remote profiles. Unreleased 0.6 source implements the shared profile control
plane and a separate macOS arm64 manager for app-owned Colima/Lima, Docker
Engine, and Compose tools. Issue #349 also supplies a probe-only OCI and Compose
verification topology, but neither component activates a deployment profile or
supported application container. Local Desktop is the preselected, recommended
mode. An omitted profile migrates to that safe local default; an unknown schema,
malformed topology, stale revision, or substituted endpoint fails closed.

Headless tooling can list the reviewed choices, inspect the stored profile,
preview an exact transition, diagnose a profile/runtime mismatch, recover to
Local Desktop, and emit redacted backup or support metadata:

```sh
ancestry --json deployment modes
ancestry --json deployment status
ancestry --json deployment diagnose
ancestry --json deployment metadata --purpose support
```

Every unattended transition requires the current schema and configuration
revision, the exact confirmation returned by a separate preview, and the
literal `--unattended` flag. Only Local Desktop can currently be activated.
Connect Remote activation is reserved for authenticated enrollment in #357;
Host Remote activation remains separate reviewed hosting work. Neither the
#363 host-only container-control foundation nor the #348 runtime-tool manager
activates a profile.
Profile selection never starts a listener, container, supervisor, or remote
session, and never copies, exports, imports, or uploads a family tree.

The stored endpoint origin and endpoint-identity digest are non-secret
configuration, while enrollment credentials remain in the secret store. No
mode is inferred from environment variables, Docker context, port state,
hostname, or service discovery. `provider=none` continues to require the local,
network-free path. A valid non-local profile without its separately authorized
runtime blocks ordinary commands but leaves `deployment status`, `diagnose`,
and Local Desktop recovery available.

This guide does not authorize using the current private sidecar as a network
service or provide a current Host Remote runbook.

## Host control and macOS arm64 runtime tools

Unreleased 0.6 source contains the #363 Electron-Main-only container-control
foundation and the #348 macOS arm64 runtime-tool manager. Packaged Settings and
the packaged executable expose only fixed status, review, and apply operations.
The renderer receives no Docker socket, executable path, Docker context,
environment, arbitrary arguments, process output, or unredacted diagnostics.
Neither component connects the deployment-profile executor, starts an
AncestryLLM application image, or introduces a public or remote runtime.

The closed schema-v1 policy and plan currently accept only native Darwin arm64
with an app-owned runtime profile, Docker context, Unix socket, Docker
configuration directory, working directory, exact Engine identity and
compatibility, and exact Compose project labels. Before and after every
lifecycle action, the supervisor verifies the canonical socket path, owner,
mode, device and inode, endpoint, runtime profile, context, Engine ID, server
and API versions, operating system, architecture, and required security
options. Ambient `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, PATH
selection, and alternate endpoints are not authority. Docker and Compose run
by absolute executable path with a minimal environment, fixed arguments,
bounded input, output, and time, no shell, process-tree termination, and
redacted stable failures.

Validated plans require digest-pinned images, a numeric non-root user,
read-only roots, `cap_drop: [ALL]`, `no-new-privileges`, init, app-owned named
volumes, internal networks, and loopback-only TCP publication. Host paths,
devices, host namespaces, privileged execution, writable roots, extra
capabilities, unowned labels, and ambiguous or colliding resources fail
closed. Discovery and reconciliation use the exact project identity and three
app-owned labels; conflicting resources are reported and never adopted.
Start, repair, and preserve/delete uninstall require a short-lived token bound
to that exact operation. Stop is bounded but does not delete resources.

Stable control failures are `INVALID_POLICY`, `INVALID_PLAN`,
`ENDPOINT_UNTRUSTED`, `ENDPOINT_CHANGED`, `ENGINE_UNTRUSTED`,
`RESOURCE_CONFLICT`, `AUTHORIZATION_REQUIRED`, and `CONTROL_FAILED`. Stable
process failures are `PROCESS_REQUEST_INVALID`, `PROCESS_INPUT_LIMIT`,
`PROCESS_OUTPUT_LIMIT`, `PROCESS_TIMEOUT`, `PROCESS_EXIT`, and
`PROCESS_RESPONSE_INVALID`. Local-runtime management uses the `RUNTIME_*`
codes in [Setup diagnostics](SETUP_DIAGNOSTICS.md#local-runtime-management-failures).
These codes are bounded diagnostic evidence, not an end-user Host Remote
troubleshooting interface.

The sanitized
[`issue-363-macos-arm64-container-supervisor.json`](release-evidence/issue-363-macos-arm64-container-supervisor.json)
record verifies this control subset against an isolated, app-owned Colima
profile. It exercises start, stop, repair, preserve/delete uninstall, exact
inventory, hardening inspection, ambient-selection rejection, and complete
owned-resource cleanup while leaving the default Docker context and engine
unchanged. It proves neither an application image nor a supported runtime.

The #348 manager downloads exact upstream assets into bounded app-owned cache
files, verifies each archive and license by reviewed size and SHA-256, safely
extracts and re-verifies expected components, and then performs resumable
setup, start, stop, repair, and preserve/delete removal. Offline mode never
uses the network and succeeds only from a complete reverified cache. It is
limited to Apple silicon on macOS 13 or later, requires hardware virtualization
and at least 24 GiB free, and uses exact pinned versions, resource limits,
archive names, URLs, licenses, sizes, and digests. It does not use a package
manager, request administrator privileges, install system services, select
ambient tools, or fall back to another mirror. Docker Desktop remains optional
and untouched. See [Desktop shell](DESKTOP_SHELL.md#macos-arm64-local-runtime-management)
for the Settings and noninteractive operator procedures.

## Probe-only OCI and Compose topology

Issue #349 adds [`containers/Dockerfile`](../containers/Dockerfile), a closed
base Compose model, and Local Desktop and Host Remote validation overlays. This
is production-shaped build and lifecycle evidence, not authorization to run
`docker compose up` as a supported application deployment. The deployment
profile executor and #348 host manager do not start it.

The topology contains exactly two defined services:

- `gateway` is mandatory and serves only authenticated health and capability
  probes on loopback inside its container;
- `worker` is optional behind an explicit Compose profile and otherwise remains
  absent. When selected for lifecycle evidence it performs no genealogy work
  and exits cleanly on the platform termination signal.

Neither service publishes or exposes a host port. Both attach only one internal
network, run as numeric UID/GID 65532, use a read-only root filesystem, drop all
capabilities, set `no-new-privileges`, enable init, and have explicit CPU,
memory, PID, log, health, and shutdown bounds. Host paths, devices, privileged
or host namespaces, extra services, mutable image references, unsupported
platforms, and unknown fields fail policy validation. The named data volume is
attached read-only as a placeholder. No service initializes a database, runs a
schema migration, receives a genealogy path, or receives a provider secret.
The random probe credential exists only in a private `/run` tmpfs and is not
written to Compose, environment evidence, logs, receipts, or inventory.

Maintainers can validate the static source contract without starting Docker:

```sh
make container-policy
```

Hosted CI then builds both image targets by exact digest on native Linux amd64
and native Linux arm64 runners; emulation is not accepted as architecture
evidence. It checks the realized image architecture and hardening, authenticated
probe readiness, optional-worker readiness, crash visibility, graceful gateway
and worker shutdown, build/version skew rejection, read-only and disk-full
handling, and log redaction. Source policy separately proves that the probe-only
images have no database initializer or migration entrypoint; this is not an
executed migration-path assertion. The
retained schema-v1 evidence includes every installed Python distribution and
every installed Debian package, with normalized license identities and the
SHA-256 of each retained Debian copyright file. Missing packages, licenses,
fields, or lifecycle assertions fail the job rather than producing partial
evidence.

#350 must add reviewed workload identity and any permitted network publication;
#351 must add the secret broker, SQLCipher data lifecycle, migrations, backup,
restore, and recovery. Until both boundaries and the remaining `G5`/`G7` gates
pass, the images remain verification artifacts, schema migrations remain
disabled, and no Local Desktop or Host Remote activation path is supported.

Remaining work includes workload-capable application surfaces, the host secret
broker, profile activation, grant-authorized read-only family tree mounts,
authenticated workloads, storage migration and backup, runtime upgrade and
rollback policy, final application resource/readiness/listener budgets,
packaged native evidence, the full `G5`/`G7` evidence set, and every additional
OS, architecture, Engine, and Compose row claimed by a future release.

Before any profile release, a separate operator runbook must cover every
claimed native host and architecture, Docker Engine API and Compose
compatibility, Colima/Lima as the open-source macOS default, and Docker Desktop
as an optional separately licensed runtime. It must also cover explicit profile
intent, identity and enrollment, immutable image digests, TLS, authentication,
firewalls and listeners, the narrow host-secret broker, backups, upgrades,
uninstall, monitoring, recovery, and ownership. Host Remote is limited to one
trusted household, is self-supported, and has no project-operated SLA.

Release evidence must meet ADR-0026's quantitative startup, shutdown, memory,
image-size, listener, and zero-egress budgets and the threat model's `G5`-`G7`
gates. Emulated execution must be labeled as emulation and cannot establish
native platform or architecture support.

Full production/trusted binary signing is intentionally deferred until the
first full version release, v1.0.0. Official `0.x` releases default to unsigned
binaries, so none of the signing credentials below are required to build or
publish them. This procedure prepares the mandatory v1.0.0-and-later trusted
signing environment. Signed annotated release tags are likewise deferred until
v1.0.0; protected-branch signed-commit requirements are unchanged.

## Reconfigure desktop signing from macOS

The repository helper searches the current macOS user's keychain for exactly
one valid `Developer ID Application` identity, exports only that identity,
generates a strong one-time PKCS#12 password, and derives the Apple Team ID.
It ignores Xcode's Apple Development and Apple Distribution identities because
they cannot sign a Developer ID release distributed through GitHub. The helper
then creates and validates all five required Base64 payloads, securely collects
the remaining values, uploads all nine private environment secrets and four
public repository variables, and verifies the result.

The helper does **not** issue Apple or Windows certificates, create an Apple
notary API key, create GPG keys, or provision a Windows virtual machine. The
Developer ID identity, notary API key, Windows identity, and GPG keys must exist
before it runs. The Apple notary credentials are used only to notarize a
GitHub-distributed application; this flow does not upload to the App Store.

### Destination and exact configuration

- Repository: `sodejm/AncestryLLM`
- GitHub Actions environment for private secrets: `desktop-signing`
- Repository-level public variables: `sodejm/AncestryLLM`

The nine private environment secrets are:

1. `APPLE_CERTIFICATE_BASE64`
2. `APPLE_CERTIFICATE_PASSWORD`
3. `APPLE_API_KEY_BASE64`
4. `APPLE_API_KEY_ID`
5. `APPLE_API_ISSUER`
6. `WINDOWS_CERTIFICATE_BASE64`
7. `WINDOWS_CERTIFICATE_PASSWORD`
8. `LINUX_GPG_PRIVATE_KEY_BASE64`
9. `LINUX_GPG_PASSPHRASE`

The four public repository variables are:

1. `APPLE_TEAM_ID`
2. `WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT`
3. `LINUX_GPG_SIGNING_FINGERPRINT`
4. `LINUX_GPG_PUBLIC_KEY_BASE64`

### Missing information and required inputs

The following values and procedures are intentionally not stored in this
repository. Obtain them through the project's approved credential-management
process before reconfiguration:

- One valid `Developer ID Application` certificate and private key in the
  current macOS user's unlocked keychain. Xcode or the Apple developer profile
  may install this identity. The helper derives `APPLE_TEAM_ID`, exports the
  identity into its private temporary directory, and generates
  `APPLE_CERTIFICATE_PASSWORD`; neither value needs to be supplied manually.
- `[APPLE_API_KEY_SOURCE_FILE]`: the original Apple notary API private-key
  payload, plus `APPLE_API_KEY_ID` and `APPLE_API_ISSUER`.
- `[WINDOWS_CERTIFICATE_SOURCE_FILE]`: the original Authenticode certificate
  payload and its `WINDOWS_CERTIFICATE_PASSWORD`.
- `[LINUX_GPG_PRIVATE_KEY_SOURCE_FILE]` and
  `[LINUX_GPG_PUBLIC_KEY_SOURCE_FILE]`: matching exported GPG key payloads,
  plus `LINUX_GPG_PASSPHRASE`.
- The complete `WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT` and the complete
  `LINUX_GPG_SIGNING_FINGERPRINT`.
- `[GH_INSTALL_COMMAND]` if GitHub CLI is not already installed, and the
  project's approved `[GH_AUTHENTICATION_METHOD]`. The repository does not
  prescribe either one.
- Access to GitHub's hosted `windows-11-arm` runner for Windows 11 validation.
  No repository runner registration token, provider, provisioning command, or
  destroy command is required.

No environment variables are required by the helper. All remaining paths and
values are entered interactively so private values do not appear in command
arguments or shell history. The helper generates only the temporary Apple
PKCS#12 export, its password, and temporary Base64 representations. It never
generates, replaces, or removes the keychain identity or any user-supplied
source file.

If the correct identity cannot be made available in the current keychain, the
explicit fallback `--apple-certificate-file [APPLE_CERTIFICATE_SOURCE_FILE]`
accepts an existing PKCS#12 file outside the repository. That mode also prompts
for its password and `APPLE_TEAM_ID`.

### macOS prerequisites and secure preparation

1. Use a trusted macOS account and terminal. Disable shell tracing before any
   credential work with `set +x`.
2. Unlock the current user's login keychain. Confirm that Keychain Access shows
   a `Developer ID Application` certificate with its private key, or run this
   read-only check:

   ```sh
   security find-identity -v -p codesigning
   ```

   Exactly one valid line beginning with `Developer ID Application:` must be
   present. Apple Development and Apple Distribution lines do not count. If
   macOS asks whether the helper may access the private key during export,
   verify the selected identity and allow access for that run.
3. Store the Apple notary API key, Windows certificate, and GPG source files in
   a private location outside the AncestryLLM checkout. The helper rejects
   repository-local sources, including paths reached through symbolic links.
   Repository ignore rules cover common signing formats as a second line of
   defense, and the repository-safety gate rejects them even if force-added.
4. Confirm `/bin/bash`, `/usr/bin/base64`, `/usr/bin/python3`,
   `/usr/bin/security`, `awk`, `chmod`, `cmp`, `grep`, `mktemp`, `openssl`,
   `realpath`, `rm`, `tr`, and `uname` are available. Install Xcode Command
   Line Tools if `xcrun` and Swift are not already available:

   ```sh
   xcode-select --install
   ```

   The exporter uses Apple's Security framework and sends the generated
   PKCS#12 password through standard input, not a command argument.
5. Install GitHub CLI with the approved `[GH_INSTALL_COMMAND]` if `gh` is not
   present. Authenticate `sodejm` on `github.com` using
   `[GH_AUTHENTICATION_METHOD]`; that exact account must be authorized to read
   `sodejm/AncestryLLM`, update its Actions environment secrets, and update
   repository Actions variables. The helper rejects an inherited `GH_HOST`
   other than `github.com`, binds every CLI call to that host, and verifies the
   authenticated account and repository identity before collecting any
   credentials. It never resolves `gh` from `PATH`. It checks fixed
   installation locations and executes the canonical file with a minimal
   `PATH`. If the reviewed CLI is elsewhere, pass its absolute, canonical,
   non-symlink path with `--gh-executable`. The executable and every canonical
   parent must be owned by root or the current user and must not be group- or
   world-writable.
6. Confirm `desktop-signing` already exists and has the protections required
   by [the release runbook](RELEASING.md#one-time-repository-setup). The helper
   does not create or alter environment protection rules.
7. Put the four remaining original signing payloads outside the repository in
   a secure directory. Restrict each file before use, for example:

   ```sh
   chmod 600 [APPLE_API_KEY_SOURCE_FILE] \
     [WINDOWS_CERTIFICATE_SOURCE_FILE] \
     [LINUX_GPG_PRIVATE_KEY_SOURCE_FILE] \
     [LINUX_GPG_PUBLIC_KEY_SOURCE_FILE]
   ```

   Each source must be a non-empty regular file owned by root or the current
   user, with exact mode `0400` or `0600`; the final source path must not be a
   symbolic link. Do not place these files, their Base64 encodings, passwords,
   key IDs, issuer IDs, or passphrases in this repository, a shell command, a
   log, or a clipboard manager. The script disables shell tracing, masks
   private text entry, and uses a mode-`0700` temporary directory. It securely
   opens each user-supplied path exactly once with no-follow semantics,
   validates the open descriptor, copies it directly into a mode-`0600`
   snapshot, and then uses only that snapshot. If the open file changes while
   it is copied, the helper aborts and removes the incomplete snapshot. All
   generated material is removed on exit.

### Generate and validate without uploading

From the repository root:

```sh
chmod 700 scripts/ancestryll-runner-secrets-helper.sh
./scripts/ancestryll-runner-secrets-helper.sh --dry-run
```

If the approved GitHub CLI is outside a fixed installation location, use the
canonical path that you reviewed:

```sh
./scripts/ancestryll-runner-secrets-helper.sh --dry-run \
  --gh-executable /reviewed/canonical/path/to/gh
```

The default run discovers and exports the Apple identity first. Supply four
remaining source paths, four private text values, and the Windows and Linux
public identity values when prompted. The helper asks for every user-supplied
private text value twice. A dry run checks the fixed `github.com` host, the
exact authenticated `sodejm` account, the canonical repository identity,
keychain discovery and export, descriptor-bound credential snapshots, non-empty
values, Base64 round trips, and public-identity formats, then exits without
changing GitHub.

For the explicit manual fallback, run:

```sh
./scripts/ancestryll-runner-secrets-helper.sh --dry-run \
  --apple-certificate-file [APPLE_CERTIFICATE_SOURCE_FILE]
```

This fallback prompts for the PKCS#12 password and Apple Team ID in addition to
the remaining values. The certificate file must remain outside the repository.

### Upload and verify

After a successful dry run, repeat the prompts in upload mode:

```sh
./scripts/ancestryll-runner-secrets-helper.sh --upload
```

Review the destination summary and type the exact confirmation
`UPLOAD github.com/sodejm/AncestryLLM desktop-signing AS sodejm` only when
ready. Existing values with the same names will be replaced. The helper prints
the fixed host, approved authenticated account, canonical GitHub CLI path, and
version before authentication, then uses that exact executable for the
following operations:

```sh
gh secret set [SECRET_NAME] -R sodejm/AncestryLLM -e desktop-signing
gh variable set [VARIABLE_NAME] -R sodejm/AncestryLLM
```

Private text is supplied through standard input, never as a command argument.
After all commands succeed, the helper lists the configured names, confirms
all nine secret names and all four variable names exist, and reads back the
four public variables to compare their complete values. GitHub deliberately
does not expose Actions secret values after creation, so secret verification
is limited to successful upload responses and presence of each expected name.

### Independent verification and cleanup

Without displaying secret values, verify the configured names:

```sh
gh secret list -R sodejm/AncestryLLM -e desktop-signing
gh variable list -R sodejm/AncestryLLM
```

Confirm that every name listed above is present. Review the
`desktop-signing` environment in GitHub and confirm its required reviewer and
protected-branch policy remain intact. Then close the terminal, remove any
unneeded local copies using the project's approved secure-deletion process,
and clear clipboard history if it was used. The helper removes only its own
temporary directory; it never removes user-supplied source files.

If setup fails:

- `Required command is missing`: install that prerequisite, then rerun dry
  run.
- host, account, authentication, or repository-identity failure: unset an
  alternate `GH_HOST`, reauthenticate `sodejm` on `github.com` using
  `[GH_AUTHENTICATION_METHOD]`, and confirm that account's repository,
  environment-secret, and variable permissions.
- rejected credential source: use a non-empty regular file outside the
  repository, owned by root or the current user, with exact mode `0400` or
  `0600`; do not use a symbolic link. If the source changed during its
  descriptor-bound copy, stop other writers before retrying.
- no valid Developer ID identity: use Xcode or the Apple developer profile to
  install a `Developer ID Application` certificate and its private key in the
  current user's keychain, then repeat the read-only identity check.
- multiple valid Developer ID identities: remove or archive obsolete identities
  through the approved keychain process, or use the explicit PKCS#12 fallback.
- keychain export failure: unlock the login keychain, confirm the certificate
  has its private key, and allow private-key access when macOS prompts. The
  helper stops without uploading if export fails.
- Base64 round-trip failure: stop and replace the affected source file from
  the approved issuer or backup.
- upload failure: the script stops immediately. Rerun `--upload`; successful
  earlier items may already have been replaced, and repeating the complete set
  restores a consistent configuration.
- verification mismatch: do not run a release. Check repository/environment
  scope and authorization, then rerun the complete upload.

## GitHub-hosted Windows 11 validation

The Windows ARM64 installer is built, installed, and launched natively on
GitHub's hosted `windows-11-arm` image. Workflows assert Windows 11 and the
ARM64 host architecture before accepting validation evidence, then explicitly
select and probe ARM64 Python and Node.js so dependency installation,
packaging, and exercise stay on the shipped `win32-arm64` boundary. The locked
desktop environment installs only the base runtime and the `desktop-build`
extra, which contains the sidecar packager. Third-party packages must resolve
to prebuilt wheels; `uv --no-build` fails the job instead of invoking a compiler
or external library toolchain. The workflow then installs and packages only the
local AncestryLLM application code. Optional remote-provider SDKs are not part
of the provider-`none` desktop sidecar. Evidence records ARM64 for both host and
artifact architecture.

GitHub supplies a fresh hosted VM for each job, so the repository has no
self-hosted runner registration, provider, runner group, provisioning, or
teardown procedure to maintain. If `windows-11-arm` is unavailable to the
repository, the Windows row remains queued or fails and the aggregate desktop
gate cannot pass; do not substitute a Windows Server image for this validation
boundary.
