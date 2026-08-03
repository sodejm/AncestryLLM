# Desktop verification

The `Desktop gate` is the always-reported aggregate for changes that can affect
the bounded 0.5.0 Electron shell. It binds source, security, native sidecar, and
unpublished packaged-runtime evidence to the exact protected `main` commit. The gate
does not publish a release and must not be interpreted as installer, signing,
notarization, or end-user platform approval.

## Exact-head target matrix

The native package job assembles and exercises one `unpacked-native`
application on each exact supported runner below. The assembled application exists only
inside that job; CI uploads its JSON evidence and SBOM, not the application
tree.

| Runner | Bundled sidecar | Intended target | Executed OS | Host architecture | Artifact architecture | `platformValidated` |
|---|---|---|---|---|---|---|
| `macos-15` | `darwin-arm64` | macOS 15 | macOS 15 | arm64 | arm64 | `true` |
| `macos-15-intel` | `darwin-x64` | macOS 15 | macOS 15 | x64 | x64 | `true` |
| `macos-26` | `darwin-arm64` | macOS 26 | macOS 26 | arm64 | arm64 | `true` |
| `macos-26-intel` | `darwin-x64` | macOS 26 | macOS 26 | x64 | x64 | `true` |
| `windows-11-arm` | `win32-x64` | Windows 11 x64 | Windows 11 | arm64 | x64 | `true` |
| `ubuntu-24.04` | `linux-x64` | Ubuntu 24.04 | Ubuntu 24.04 | x64 | x64 | `true` |

The Windows row uses GitHub's hosted `windows-11-arm` runner. The workflow
asserts that the host is Windows 11 on ARM64, installs x64 Python and Node.js,
and builds and launches the `win32-x64` application through Windows x64
emulation. The evidence `arch` field remains the artifact architecture; the
runner identity and host probe bind that artifact to the ARM64 validation
environment. The aggregate records `platformValidated: true` only after all
six exact rows pass.

Every row verifies the checked-out full commit SHA before building. The
aggregate rejects missing, duplicate, wrong-target, or wrong-head evidence.
Workflow-level path filters are not used: the in-workflow classifier may skip
the expensive jobs, but `Desktop gate` still reports a result.

## Installer release matrix and signing boundary

The manually dispatched pre-tag release workflow builds the four installer
rows below for one full commit SHA and stable version. AncestryLLM does not use
full production/trusted binary signing before the first full version release,
v1.0.0. Official `0.x` rows therefore use `binarySigningMode: "unsigned"`;
local/manual `0.x` assembly may instead declare `self-signed`. Starting with
v1.0.0, all rows must declare `trusted` and pass their platform signature gates.

| Runner | Sidecar | Supported target | Installer |
|---|---|---|---|
| `macos-15` | `darwin-arm64` | macOS 15 arm64 | DMG; Developer ID signed and notarized at v1.0.0+ |
| `macos-15-intel` | `darwin-x64` | macOS 15 x64 | DMG; Developer ID signed and notarized at v1.0.0+ |
| `windows-11-arm` | `win32-x64` | Windows 11 x64 through ARM64-hosted emulation | NSIS EXE; Authenticode signed at v1.0.0+ |
| `ubuntu-24.04` | `linux-x64` | Ubuntu 24.04 x64 | DEB; detached GPG signature at v1.0.0+ |

Each row must use the matching native sidecar, install or mount the full
installer, and launch the installed application with an empty runtime `PATH`.
That last check prevents an installed bundle from silently depending on a
system Python, Node.js, or pnpm. At v1.0.0 and later, macOS additionally
requires `codesign` verification, hardened runtime with the reviewed minimal
entitlements, Gatekeeper acceptance, notarization, stapling, and the configured
Apple Team ID. Windows additionally requires a valid Authenticode result from
the configured certificate thumbprint. Ubuntu additionally requires detached-
signature verification against the configured public key and complete
fingerprint in a clean, public-key-only keyring. These v1.0.0+ public signer
identities are enforced again by validation jobs independently of signing jobs.

Validation then runs those four immutable installer artifacts in all six exact
supported environments: macOS 15 arm64 and x64, macOS 26 arm64 and x64,
Windows 11 ARM64 hosting the Windows x64 artifact under emulation, and Ubuntu
24.04 x64. The macOS 26 rows download and validate the same
matching-architecture DMGs built on macOS 15; no second installer is built.
Every validation receipt binds the source Actions artifact ID and digest as
well as the installed file digest, so an approximation or rebuilt copy cannot
substitute for the approved installer. It also records the canonical actual OS
derived from the successful native host probe and requires that value to match
the intended OS before setting `operatingSystemPassed`.

Every target receipt binds the full commit SHA, stable version, OS,
architecture, successful named gates, and SHA-256 identity of its installer,
SBOM, and Linux signature where applicable. Aggregation requires all four rows
and copies only digest-matched regular files into the release directory. It
also emits `desktop-exact-head-evidence.json`,
`desktop-artifact-manifest.json`, one combined `desktop-sbom.json`, and the
desktop-only `SHA256SUMS`. The artifact is uploaded without recompression so
its reported digest can be approved. A later tag run imports that exact
immutable `desktop-release-distributions` artifact, verifies GitHub's artifact
digest plus the internal manifest and checksums, and never rebuilds the signed
installers after the tag is pushed. The tag run then combines those imported
files with the Python distributions, regenerates the final
`release-evidence.md` and one release-wide `SHA256SUMS`, and records build
provenance over the complete release asset set.

## What the hosted gate proves

The source and security job uses locked Python and pnpm dependencies and
records successful API-contract, authentication-before-parsing,
domain-route-absence, IPC-sender-validation, `provider=none` network-free,
redaction, production-build inspection, dependency-audit, secret-scan, and
SBOM gates. The production build scanner also rejects fixture bridge code and
test-hook machinery so packaged-runtime testing cannot weaken the production
main process.

Each native row then:

1. builds and smoke-tests the target-matched sidecar;
2. builds the production Electron assets and assembles an unpublished unpacked
   native application;
3. verifies that the packaged resources contain the expected sidecar;
4. launches the actual packaged executable with isolated fictional app data;
5. verifies first-run welcome, Home and healthy Diagnostics, Settings
   persistence across a new process, corrupt-preference fail-closed behavior,
   clean quit and relaunch, custom-protocol and production CSP behavior,
   sandbox/context-isolation/window controls, the exact six-method bridge,
   renderer zero-egress canaries, keyboard/focus/landmark/label behavior,
   narrow and 200%-zoom layouts, contrast, and reduced motion;
6. records cold-launch, warm-launch, readiness, process-RSS, and renderer
   outbound-request measurements; and
7. exercises sidecar absence/recovery, crash-loop exhaustion/retry/quit, and
   app/sidecar build mismatch against verification-only package copies; and
8. inspects the packaged Electron fuses and ASAR boundary.

The packaged Playwright pass attaches through an ephemeral CDP endpoint bound
to `127.0.0.1` solely so the harness can inspect and close the unpublished
application. It does not pass credentials or expose the endpoint beyond the
runner. A separate launch uses a fresh profile and no remote-debugging or
Node-inspector argument; the test verifies that neither the normal process tree
nor captured output exposes a debugging surface. The normal launch waits for a
constant, non-sensitive lifecycle record emitted by the existing
`ready-to-show` window path, so a sidecar or crash helper cannot satisfy the
renderer-readiness gate. On macOS only, both automation launches pass Chromium's
`--use-mock-keychain` because the ad hoc, unpackaged runner build cannot reliably
use a login keychain. That automation-only switch
is not part of a shipped launch path.

Electron handles the native zoom shortcuts in the browser process, where unit
tests cover every supported level from 50% through 200%, reset, clamping, and
unrelated-key behavior. CDP-injected keyboard events do not traverse that
browser-process hook, so the packaged harness verifies layout at an equivalent
200% renderer scale and states that distinction explicitly instead of claiming
a native shortcut observation it did not make.

On macOS, the verification-only builder overlay applies an ad hoc signature
after electron-builder mutates the Electron executable and fuses. That
signature only permits the unpublished application to launch on the hosted
runner; it is not release identity, installer-signing, or notarization
evidence. The aggregate therefore continues to record
`signingVerified: false` and leaves those claims to #231.

The packaged renderer canary observes attempted HTTP, HTTPS, WebSocket, window,
and service-worker activity and requires zero outbound requests. The separate
API security tests prove the `provider=none` sidecar policy. Together these are
bounded automated controls; they are not a claim of OS-level packet capture.

Each native row now makes disposable copies of the assembled package for three
black-box fault scenarios. It temporarily withholds and restores the real
packaged sidecar to prove degraded Diagnostics and successful manual retry;
kills the real sidecar child repeatedly to prove automatic restart, bounded
exhaustion, manual recovery, and child cleanup on quit; and substitutes a
separately built target-native verification sidecar whose reported build cannot
match the application build. macOS copies are ad hoc re-signed only after the
verification mutation. None of these disposable copies is a release artifact.

The harness changes only those verification package copies and observes the
same packaged UI and process boundaries used by a normal launch. Production
code has no fault environment variable, test IPC, renderer hook, or alternate
sidecar registry. Renderer-visible diagnostics remain sanitized: the scenarios
assert coded states and retry counters without exposing ports, tokens, paths,
process IDs, or stderr.

The performance policy is versioned as `desktop-unpacked-v1` and is a hard gate,
not an informational benchmark:

| Native row | Cold launch | Warm launch | Ready | Process-tree RSS | Renderer egress |
|---|---:|---:|---:|---:|---:|
| macOS and Linux | 30 s | 20 s | 45 s | 1.5 GiB | 0 |
| Windows | 45 s | 30 s | 60 s | 2 GiB | 0 |

Every metric stores the observed value, ceiling, unit, and pass result. Missing,
non-finite, negative, or over-ceiling measurements fail the row. These broad CI
ceilings are regression guards for hosted runners, not end-user performance
targets.

## Machine-readable evidence

The workflow uploads only evidence for the checked commit:

- one `target` JSON document for each of the six rows;
- one `security` JSON document plus the CycloneDX SBOM; and
- one `aggregate` JSON document created only after all prerequisites succeed.

Target and security claims are derived from write-once verification receipts,
not workflow-supplied success booleans. Each receipt records the full Git SHA
before and after its command, the exact executable and arguments, exit status,
stdout and stderr digests, the named gates that command can establish, and
digests for any consumed artifact such as the SBOM, metrics, or fuse inspection.
The wrapper also requires a clean source tree before and after the command and
binds deterministic staged, tracked-worktree, and non-ignored untracked state.
Only declared generated-output paths may change. Evidence generation rejects a
missing, duplicate, failed, wrong-head, legacy unbound, workspace-mutating, or
digest-mismatched receipt.

The three packaged-sidecar fault scenarios have independent receipt gates and
write exact-schema observation documents. Target evidence binds each document
by byte count and SHA-256 to its receipt; the mismatch receipt additionally
binds the target-native wrong-build executable. Aggregation revalidates those
documents and bindings instead of accepting a workflow-provided success flag.
The crash-loop scenario owns the graceful application-quit assertion and proves
that the active real sidecar exits with it. The wrong-build scenario instead
proves `incompatible_build`, consumes the one manual retry without weakening
that result, and requires bounded termination of its verification process.

A target document records the runner, sidecar target, intended and actual OS,
architecture, `packageBoundary: "unpacked-native"`,
`artifactKind: "unpublished-unpacked-native"`, `signingVerified: false`,
`platformValidated`, packaged-runtime controls, and the bounded performance
measurements. A security document records explicit boolean gate results and
the SBOM byte count and SHA-256 digest. The aggregate requires exactly six
targets and one security document from the same full commit SHA.

A successful aggregate uses `status: "passed"` and
`platformValidated: true`. Its `publicationRequirements` object still requires
the separate desktop installer evidence tracked by #231. That evidence records
the version-derived binary-signing mode; it does not require trusted signing
for `0.x`.

Evidence files are written once. A pre-existing output, malformed schema,
failed boolean, invalid metric, nonzero renderer egress count, unexpected
target, or SHA mismatch fails closed.

ASAR evidence is also platform-scoped. All rows require `app.asar` and verify
the eight expected Electron fuse states. macOS additionally compares the
`ElectronAsarIntegrity` SHA-256 in `Info.plist` with the packaged ASAR header.
Windows and Linux record that macOS `Info.plist` metadata check as
`not-applicable`; they do not mislabel that platform-specific metadata as a
cross-platform integrity observation.

## Release boundary and follow-up

Issue #230 establishes cross-platform verification for the unpublished package
boundary. It does not create or approve a release installer. The #231 pre-tag
layer consumes that exact-head input and establishes an installer claim only
when every target-matched installer is manually installed and exercised, and
the provenance and actual supported-OS checks pass under the
[release runbook](RELEASING.md). Trusted signature and macOS notarization checks
join that claim at v1.0.0. A local build, a different Windows runner, or an
incomplete pre-tag run cannot substitute for that proof.

This verification work does not close the broader adversarial assurance issue
#131 or the release-coordination tracker #132. CI success must not be used to
synthesize missing external proof.

## Local reproduction

Run the source-level checks with the canonical repository gates:

```console
make test
make lint
make typecheck
make security
```

The native packaged-runtime check additionally requires the current platform's
target sidecar and unpacked Electron application. CI is the authoritative
six-row execution because one local machine cannot honestly reproduce all
hosted operating-system and architecture rows.
