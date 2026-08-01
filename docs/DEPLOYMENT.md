# Deployment operations

AncestryLLM is local-first software. This document covers the hosted controls
used to build and publish its desktop installers; it does not describe a
hosted application deployment.

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
- `[RUNNER_REGISTRATION_TOKEN]`, `[RUNNER_PROVIDER]`,
  `[RUNNER_PROVISIONING_COMMAND]`, and `[RUNNER_DESTROY_COMMAND]` for the
  ephemeral Windows 11 runner. No authorized provider or provisioning command
  is defined in the repository, so the signing helper cannot create it.

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
4. Confirm `/bin/bash`, `/usr/bin/base64`, `/usr/bin/security`, `awk`, `chmod`,
   `cmp`, `grep`, `mktemp`, `openssl`, `realpath`, `rm`, `tr`, and `uname` are
   available. Install Xcode Command Line Tools if `xcrun` and Swift are not
   already available:

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

   Do not place these files, their Base64 encodings, passwords, key IDs,
   issuer IDs, or passphrases in this repository, a shell command, a log, or a
   clipboard manager. The script disables shell tracing, masks private text
   entry, uses a mode-`0700` temporary directory and mode-`0600` files, and
   removes generated material on exit.

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
keychain discovery and export, file readability, non-empty values, Base64 round
trips, and public-identity formats, then exits without changing GitHub.

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
- unreadable or empty file: correct its path or permissions; do not weaken
  permissions beyond what is required for the current user.
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

## Ephemeral Windows 11 runner

The release contract requires a clean, one-job Windows 11 x64 VM with labels
`self-hosted`, `Windows`, `X64`, and `ancestryllm-windows-11`. Its runner group
must be restricted to trusted exact-`main` executions of
`desktop-sidecar.yml` and `release.yml`, and the VM must be automatically
destroyed after its job.

Provisioning cannot be made executable until the missing
`[RUNNER_PROVIDER]`, `[RUNNER_REGISTRATION_TOKEN]`,
`[RUNNER_PROVISIONING_COMMAND]`, and `[RUNNER_DESTROY_COMMAND]` are approved
and documented. Do not substitute an invented service, API, command, or
authentication method. Once those decisions exist, add their exact setup,
one-job lifecycle verification, and teardown procedure here before enabling a
production release.
