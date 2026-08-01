# Deployment operations

AncestryLLM is local-first software. This document covers the hosted controls
used to build and publish its desktop installers; it does not describe a
hosted application deployment.

## Reconfigure desktop signing from macOS

The repository helper creates the five required Base64 payloads from existing
signing files, validates every generated payload with a byte-for-byte decode
round trip, securely collects the remaining values, uploads all nine private
environment secrets and four public repository variables, and verifies the
result. It does **not** issue Apple or Windows certificates, create an Apple
notary API key, create GPG keys, or provision a Windows virtual machine. Those
assets must be obtained through their approved issuers before running it.

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

- `[APPLE_CERTIFICATE_SOURCE_FILE]`: the original Developer ID certificate
  payload and its `APPLE_CERTIFICATE_PASSWORD`.
- `[APPLE_API_KEY_SOURCE_FILE]`: the original Apple notary API private-key
  payload, plus `APPLE_API_KEY_ID` and `APPLE_API_ISSUER`.
- `[WINDOWS_CERTIFICATE_SOURCE_FILE]`: the original Authenticode certificate
  payload and its `WINDOWS_CERTIFICATE_PASSWORD`.
- `[LINUX_GPG_PRIVATE_KEY_SOURCE_FILE]` and
  `[LINUX_GPG_PUBLIC_KEY_SOURCE_FILE]`: matching exported GPG key payloads,
  plus `LINUX_GPG_PASSPHRASE`.
- `APPLE_TEAM_ID`, the complete
  `WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT`, and the complete
  `LINUX_GPG_SIGNING_FINGERPRINT`.
- `[GH_INSTALL_COMMAND]` if GitHub CLI is not already installed, and the
  project's approved `[GH_AUTHENTICATION_METHOD]`. The repository does not
  prescribe either one.
- `[RUNNER_REGISTRATION_TOKEN]`, `[RUNNER_PROVIDER]`,
  `[RUNNER_PROVISIONING_COMMAND]`, and `[RUNNER_DESTROY_COMMAND]` for the
  ephemeral Windows 11 runner. No authorized provider or provisioning command
  is defined in the repository, so the signing helper cannot create it.

No environment variables are required by the helper. All paths and values are
entered interactively so private values do not appear in command arguments or
shell history. The helper generates only temporary Base64 representations; it
never generates or replaces the original signing identities.

### macOS prerequisites and secure preparation

1. Use a trusted macOS account and terminal. Disable shell tracing before any
   credential work with `set +x`.
2. Store every certificate, API key, and GPG source file in a private location
   outside the AncestryLLM checkout. The helper rejects repository-local
   sources, including paths reached through symbolic links. Repository ignore
   rules cover common signing formats as a second line of defense, and the
   repository-safety gate rejects them even if they are force-added.
3. Confirm `/bin/bash`, `/usr/bin/base64`, `awk`, `chmod`, `cmp`, `grep`,
   `mktemp`, `realpath`, `rm`, and `tr` are available. These are standard
   macOS tools.
4. Install GitHub CLI with the approved `[GH_INSTALL_COMMAND]` if `gh` is not
   present. Authenticate using `[GH_AUTHENTICATION_METHOD]`; the account must
   be authorized to read `sodejm/AncestryLLM`, update its Actions environment
   secrets, and update repository Actions variables.
5. Confirm `desktop-signing` already exists and has the protections required
   by [the release runbook](RELEASING.md#one-time-repository-setup). The helper
   does not create or alter environment protection rules.
6. Put the five original signing payloads outside the repository in a secure
   directory. Restrict each file before use, for example:

   ```sh
   chmod 600 [APPLE_CERTIFICATE_SOURCE_FILE] \
     [APPLE_API_KEY_SOURCE_FILE] \
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

Supply the five original file paths, five private text values, and three
public identity values when prompted. The helper asks for every private text
value twice. A dry run checks authentication, repository access, file
readability, non-empty values, Base64 round trips, and public-identity formats,
then exits without changing GitHub.

### Upload and verify

After a successful dry run, repeat the prompts in upload mode:

```sh
./scripts/ancestryll-runner-secrets-helper.sh --upload
```

Review the destination summary and type the exact confirmation `UPLOAD` only
when ready. Existing values with the same names will be replaced. The helper
uses the following GitHub CLI operations internally:

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
- authentication or repository-access failure: reauthenticate using
  `[GH_AUTHENTICATION_METHOD]` and confirm the account's repository,
  environment-secret, and variable permissions.
- unreadable or empty file: correct its path or permissions; do not weaken
  permissions beyond what is required for the current user.
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
