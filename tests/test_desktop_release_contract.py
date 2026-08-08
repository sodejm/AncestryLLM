"""Contracts for version-aware supported desktop release artifacts."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
BUILDER_CONFIG = ROOT / "desktop" / "electron-builder.release.yml"
PACKAGE_JSON = ROOT / "desktop" / "package.json"
ENTITLEMENTS = ROOT / "desktop" / "resources" / "entitlements.mac.plist"
ASSEMBLER = ROOT / "scripts" / "assemble_desktop_release.py"
PYPROJECT = ROOT / "pyproject.toml"
RELEASE_CONFIG = ROOT / ".github" / "release-config.json"
SIDECAR_MODULE = ROOT / "src" / "ancestryllm" / "api" / "sidecar.py"


def test_release_sources_share_the_exact_stable_build_identity() -> None:
    expected = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))["release"]
    desktop_version = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
    with PYPROJECT.open("rb") as handle:
        python_version = str(tomllib.load(handle)["project"]["version"])

    module = ast.parse(SIDECAR_MODULE.read_text(encoding="utf-8"))
    sidecar_builds = [
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SIDECAR_BUILD" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]

    assert sidecar_builds == [expected]
    assert python_version == expected
    assert desktop_version == expected


def test_release_request_validates_every_build_identity_before_pretag_exit() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    validation = workflow[workflow.index("      - id: release\n") : workflow.index("\n  build:\n")]
    pretag_exit = validation.rindex('if test "$EVENT_NAME" = "workflow_dispatch"; then')

    assert '"pyproject.toml"' in validation[:pretag_exit]
    assert '"desktop/package.json"' in validation[:pretag_exit]
    assert '"src/ancestryllm/api/sidecar.py"' in validation[:pretag_exit]
    assert "$version-dev" not in validation


def test_release_workflow_builds_and_verifies_the_supported_installer_matrix() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_rows = (
        ("macos-15", "darwin-arm64", "macOS 15", "arm64", "dmg"),
        ("macos-15-intel", "darwin-x64", "macOS 15", "x64", "dmg"),
        ("windows-11-arm", "win32-arm64", "Windows 11", "arm64", "nsis"),
        ("ubuntu-24.04", "linux-x64", "Ubuntu 24.04", "x64", "deb"),
    )
    for runner, sidecar_target, expected_os, arch, installer_target in expected_rows:
        assert runner in workflow
        assert f"sidecar_target: {sidecar_target}" in workflow
        assert f'expected_os: "{expected_os}"' in workflow
        assert f"arch: {arch}" in workflow
        assert f"installer_target: {installer_target}" in workflow

    for runner, sidecar_target, expected_os, arch in (
        ("macos-26", "darwin-arm64", "macOS 26", "arm64"),
        ("macos-26-intel", "darwin-x64", "macOS 26", "x64"),
    ):
        assert runner in workflow
        assert f"sidecar_target: {sidecar_target}" in workflow
        assert f'expected_os: "{expected_os}"' in workflow
        assert f"arch: {arch}" in workflow

    assert "desktop-installers:" in workflow
    assert "runner: windows-2025" not in workflow
    assert "runs_on: '\"windows-2025\"'" not in workflow
    assert "runner: windows-11-arm" in workflow
    assert "runs_on: '\"windows-11-arm\"'" in workflow
    assert "host_arch: arm64" in workflow
    assert workflow.count("runtime_arch:") == 6
    assert workflow.count("architecture: ${{ matrix.runtime_arch }}") == 2
    assert "EXPECTED_HOST_ARCH: ${{ matrix.host_arch || matrix.arch }}" in workflow
    assert workflow.count("\n          HOST_ARCH: ${{ matrix.host_arch || matrix.arch }}") == 1
    assert "[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture" in workflow
    assert workflow.count("name: Verify native ARM64 runtimes on Windows") == 2
    assert workflow.count("process.arch") >= 2
    assert workflow.count("sysconfig.get_platform()") >= 2
    assert "platform.machine()" not in workflow
    assert workflow.count("expected arm64 Node.js runtime") == 2
    assert workflow.count("expected win-arm64 Python runtime") == 2
    assert "Microsoft.VisualStudio.Component.VC.Tools.ARM64" not in workflow
    assert "OPENSSL_DIR" not in workflow
    assert "dumpbin /headers" not in workflow
    assert (
        workflow.count("uv sync --locked --extra desktop-build --no-install-project --no-build")
        == 1
    )
    assert workflow.count("uv pip install --python .venv --no-deps --editable .") == 1
    assert workflow.count("uv run --no-sync") == 3
    assert workflow.count('--host-arch "$HOST_ARCH"') == 1
    assert "self-hosted" not in workflow
    assert "ancestryllm-windows-11" not in workflow
    assert "electron-builder.release.yml" in workflow
    assert "scripts/smoke_sidecar.py" in workflow
    assert "ANCESTRYLLM_PACKAGED_RUNTIME_PATH" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "codesign --verify --deep --strict" in workflow
    assert "spctl --assess" in workflow
    assert "stapler validate" in workflow
    assert "gpg --detach-sign" in workflow
    assert "--status-fd 1 --verify" in workflow
    assert workflow.count("APPLE_TEAM_ID: ${{ vars.APPLE_TEAM_ID }}") >= 2
    assert (
        workflow.count(
            "WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT: "
            "${{ vars.WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT }}"
        )
        >= 2
    )
    assert (
        workflow.count("LINUX_GPG_SIGNING_FINGERPRINT: ${{ vars.LINUX_GPG_SIGNING_FINGERPRINT }}")
        >= 2
    )
    assert "LINUX_GPG_PUBLIC_KEY_BASE64: ${{ vars.LINUX_GPG_PUBLIC_KEY_BASE64 }}" in workflow
    assert 'grep -Fxq "TeamIdentifier=$APPLE_TEAM_ID"' in workflow
    assert "$signature.SignerCertificate.Thumbprint" in workflow
    assert "$applicationSignature.SignerCertificate.Thumbprint" in workflow
    assert "ancestryllm-signing-gnupg" in workflow
    assert "ancestryllm-verification-gnupg" in workflow
    assert "ancestryllm-public-verification-gnupg" in workflow
    assert '--local-user "$expected_fingerprint!"' in workflow
    assert "--status-fd 1 --verify" in workflow
    assert 'grep -Fq "[GNUPG:] VALIDSIG $expected_fingerprint "' in workflow
    assert "verification keyring unexpectedly contains a private key" in workflow


def test_desktop_build_profile_contains_only_the_sidecar_packager() -> None:
    with PYPROJECT.open("rb") as handle:
        optional_dependencies = tomllib.load(handle)["project"]["optional-dependencies"]

    assert optional_dependencies["desktop-build"] == ["pyinstaller>=6.17,<7"]


def test_electron_builder_includes_the_nsis_binary_extraction_fix() -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    development_dependencies = package["devDependencies"]

    # electron-builder 26.15.6 fixed its NSIS archive filter so the bundled
    # install-time extractor no longer drops PE binaries on x64 or ARM64.
    # Keep the paired Squirrel package on the same stable patch line.
    assert development_dependencies["electron-builder"] == "26.15.7"
    assert development_dependencies["electron-builder-squirrel-windows"] == "26.15.7"


def test_release_packaged_smoke_forwards_the_playwright_filter_without_a_pnpm_separator() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    scenario = (
        "exercises first run, persistence, corrupt preferences, security, and resource evidence"
    )

    assert workflow.count("test:e2e:packaged") == 6
    assert workflow.count(f'--grep "{scenario}"') == 6
    assert re.search(r"test:e2e:packaged --\s", workflow) is None


def test_ubuntu_signing_secrets_are_not_exposed_to_runtime_verification() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    signing_start = workflow.index("      - name: Sign Ubuntu installer\n")
    verification_start = workflow.index(
        "      - name: Verify, install, and exercise Ubuntu application\n"
    )
    cleanup_start = workflow.index(
        "      - name: Remove temporary Ubuntu signing and installation state\n"
    )
    signing_step = workflow[signing_start:verification_start]
    verification_step = workflow[verification_start:cleanup_start]

    assert "LINUX_GPG_PRIVATE_KEY_BASE64" in signing_step
    assert "LINUX_GPG_PASSPHRASE" in signing_step
    assert "gpg --detach-sign" in signing_step
    assert "trap cleanup_signing_material EXIT" in signing_step
    assert 'gpgconf --homedir "$signing_home" --kill all' in signing_step
    assert "pnpm" not in signing_step
    assert "xvfb-run" not in signing_step
    assert "sudo dpkg" not in signing_step

    assert "LINUX_GPG_PRIVATE_KEY_BASE64" not in verification_step
    assert "LINUX_GPG_PASSPHRASE" not in verification_step
    assert "ancestryllm-release-key.asc" not in verification_step
    assert "ancestryllm-signing-gnupg" not in verification_step
    assert "--status-fd 1 --verify" in verification_step
    assert 'sudo apt-get install -y "./$INSTALLER"' in verification_step
    assert "xvfb-run --auto-servernum pnpm" in verification_step


def test_release_installer_runtime_validation_uses_platform_native_copy_and_install() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count('ditto --noqtn "${mounted_apps[0]}" "$installed_app"') == 2
    assert workflow.count('sudo apt-get install -y "./$INSTALLER"') == 2
    assert 'sudo dpkg -i "$INSTALLER"' not in workflow


def test_pretag_installer_locator_is_portable_to_hosted_macos_bash() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    validation_start = workflow.index("  desktop-installer-validation:\n")
    locator_start = workflow.index(
        "        name: Locate the one previously built installer\n",
        validation_start,
    )
    locator_end = workflow.index("      - id: macos-host\n", locator_start)
    locator = workflow[locator_start:locator_end]

    assert "shopt -s globstar" not in locator
    assert 'python - "$INSTALLER_TARGET" "$GITHUB_OUTPUT" <<\'PY\'' in locator
    assert 'Path("desktop-installer-source").rglob(f"*.{extension}")' in locator
    assert "expected exactly one" in locator
    assert 'if "\\n" in installer or "\\r" in installer:' in locator
    assert 'raise SystemExit("installer path contains a line break")' in locator


def test_release_workflow_separates_pretag_installer_gates_from_tag_publication() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "release_version:" in workflow
    assert "commit_sha:" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "desktop-installer-validation:" in workflow
    assert "desktop-release-distributions" in workflow
    assert "import-desktop-release-distributions:" in workflow
    assert "Desktop-Release-Run-ID:" in workflow
    assert "Desktop-Release-Artifact-ID:" in workflow
    assert "Desktop-Release-Artifact-Digest:" in workflow
    assert "steps.desktop_release.outputs.artifact-digest" in workflow
    assert "/actions/artifacts/$APPROVED_ARTIFACT/zip" in workflow
    assert 'test "$(jq -r \'.name\' "$metadata")" = "$EXPECTED_NAME"' in workflow
    assert "desktop artifact manifest digest mismatch" in workflow
    assert "github.rest.actions.downloadArtifact" not in workflow
    assert "compression-level: 0" in workflow
    assert (
        "environment: ${{ needs.validate.outputs.binary_signing_mode == 'trusted' "
        "&& 'desktop-signing' || 'desktop-prerelease' }}"
    ) in workflow
    assert "scripts/release_signing_policy.py --version" in workflow
    assert "BINARY_SIGNING_MODE: ${{ needs.validate.outputs.binary_signing_mode }}" in workflow
    assert "RELEASE_TAG_MODE: ${{ needs.validate.outputs.release_tag_mode }}" in workflow
    assert "assemble-release-distributions:" in workflow
    assert "release-distributions" in workflow

    tag_import = workflow.index("  import-desktop-release-distributions:")
    tag_assemble = workflow.index("  assemble-release-distributions:")
    assert tag_import < tag_assemble
    for publisher in (
        "publish-build-provenance",
        "draft-github-release",
        "publish-testpypi",
        "publish-pypi",
        "publish-github-release",
    ):
        job = workflow[workflow.index(f"  {publisher}:") :]
        preamble = job.split("runs-on:", maxsplit=1)[0]
        assert "assemble-release-distributions" in preamble
        assert "github.event_name == 'push'" in preamble


def test_release_packaging_defaults_to_unsigned_manual_full_installers() -> None:
    builder = BUILDER_CONFIG.read_text(encoding="utf-8")
    entitlements = ENTITLEMENTS.read_text(encoding="utf-8")

    assert "appId: org.ancestryllm.desktop" in builder
    assert "forceCodeSigning: false" in builder
    assert "identity: null" not in builder
    assert "target: dmg" in builder
    assert "target: nsis" in builder
    assert "target: deb" in builder
    assert "hardenedRuntime: false" in builder
    assert "entitlements.mac.plist" in builder
    assert "notarize: false" in builder
    assert "signAndEditExecutable: true" in builder
    assert "signExecutable: false" in builder
    assert "oneClick: false" in builder
    assert "allowToChangeInstallationDirectory: true" in builder
    assert "differentialPackage: false" in builder
    assert "publish: null" in builder
    assert "generateUpdatesFilesForAllChannels: false" in builder
    assert "com.apple.security.cs.allow-jit" in entitlements
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in entitlements
    assert "com.apple.security.cs.disable-library-validation" not in entitlements


def test_pre_1_release_path_proves_unsigned_installers_and_annotated_tag() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert (
        'release_tag_mode="$(python scripts/release_signing_policy.py --version "$version" --tag-mode)"'
        in workflow
    )
    assert 'echo "release_tag_mode=$release_tag_mode" >> "$GITHUB_OUTPUT"' in workflow
    assert "export CSC_IDENTITY_AUTO_DISCOVERY=false" in workflow
    assert "--config.mac.identity=null" in workflow
    assert "--config.mac.hardenedRuntime=false" in workflow
    assert "$env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'" in workflow
    assert "--config.win.forceCodeSigning=false" in workflow
    assert "--config.win.signAndEditExecutable=false" not in workflow
    assert "--config.win.signAndEditExecutable=true" in workflow
    assert "--config.win.signExecutable=false" in workflow
    assert "--config.win.signExecutable=true" in workflow
    assert 'codesign --verify --strict --verbose=4 "$INSTALLER"' in workflow
    assert 'codesign --verify --strict "$INSTALLER"' in workflow
    assert 'codesign --verify --deep --strict "${mounted_apps[0]}"' in workflow
    assert "if ($signature.Status -ne 'NotSigned')" in workflow
    assert "if ($applicationSignature.Status -ne 'NotSigned')" in workflow
    assert 'test ! -e "$INSTALLER.asc"' in workflow
    assert "gates+=(unsignedArtifactPassed)" in workflow
    assert 'test "$(jq -r \'.verification.reason\' <<<"$tag_json")" = "unsigned"' in workflow
    assert 'test "$(jq -r \'.verification.signature\' <<<"$tag_json")" = "null"' in workflow
    assert 'test "$(jq -r \'.verification.payload\' <<<"$tag_json")" = "null"' in workflow
    assert 'test "$(jq -r \'.verification.verified\' <<<"$tag_json")" = "true"' in workflow
    assert '"releaseTagMode": os.environ["RELEASE_TAG_MODE"]' in workflow
    assert '"signedTag":' not in workflow


def test_release_package_declares_linux_distribution_metadata() -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert package["description"]
    assert package["homepage"] == "https://github.com/sodejm/AncestryLLM"
    assert package["author"]["name"] == "Justin Soderberg"
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", package["author"]["email"])


def test_release_builder_defers_macos_architecture_to_the_matrix_row() -> None:
    builder = BUILDER_CONFIG.read_text(encoding="utf-8")
    mac_config = builder[builder.index("mac:\n") : builder.index("win:\n")]

    assert "arch:" not in mac_config
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("package_args: --mac --arm64") == 1
    assert workflow.count("package_args: --mac --x64") == 1


def test_windows_release_installer_targets_arm64() -> None:
    builder = BUILDER_CONFIG.read_text(encoding="utf-8")

    assert re.search(
        r"win:\n  target:\n    - target: nsis\n      arch:\n        - arm64\n",
        builder,
    )


def test_windows_installer_verification_uses_the_nsis_current_user_default() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # Exercise electron-builder's stock current-user destination instead of a
    # workflow-owned /D override or an assumed fallback path. Resolve the exact
    # install root from NSIS's quoted QuietUninstallString metadata instead of
    # treating DisplayIcon as a stable application-path contract. Then verify
    # exactly one packaged resources marker beneath that root, derive the
    # sibling executable from Electron's packaged layout, and retain the root
    # for bounded cleanup in both verification paths.
    assert workflow.count("-ArgumentList @('/S', '/currentuser') -Wait -PassThru") == 4
    assert workflow.count("HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall") == 4
    assert workflow.count('$displayName = "AncestryLLM $env:VERSION"') == 4
    assert workflow.count("$registration.DisplayName -eq $displayName") == 4
    assert workflow.count("$registration.DisplayVersion -eq $env:VERSION") == 4
    assert workflow.count(".QuietUninstallString") == 4
    assert workflow.count('$uninstallCommand -notmatch \'^"(?<path>[^"]+)"(?:\\s|$)\'') == 4
    assert (
        workflow.count("Get-ChildItem -LiteralPath $installRoot -Filter 'app.asar' -File -Recurse")
        == 2
    )
    assert workflow.count("$_.Directory.Name -ieq 'resources'") == 2
    assert workflow.count("if ($resourceMarkers.Count -ne 1)") == 2
    assert workflow.count("$resourcesPath = $resourceMarkers[0].Directory.FullName") == 2
    assert (
        workflow.count(
            "$application = Join-Path (Split-Path -Parent $resourcesPath) 'ancestryllm.exe'"
        )
        == 2
    )
    assert "$application = Join-Path $installRoot 'ancestryllm.exe'" not in workflow
    assert "DisplayIcon" not in workflow
    assert workflow.count("'ancestryllm-install-root.txt'") == 4
    assert workflow.count("if ($installRoots.Count -eq 0)") == 2
    assert workflow.count("unexpected install root outside LocalApplicationData") == 2
    assert workflow.count("refusing cleanup outside LocalApplicationData") == 2
    assert "/D=$installRoot" not in workflow
    assert "$installRoot = Join-Path $env:RUNNER_TEMP 'AncestryLLM'" not in workflow
    assert workflow.count("$uninstaller = Join-Path $installRoot 'Uninstall AncestryLLM.exe'") == 2


def test_release_workflow_binds_installers_evidence_sboms_and_provenance() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "assemble_desktop_release.py target" in workflow
    assert "assemble_desktop_release.py aggregate" in workflow
    assert "desktop-artifact-manifest.json" in workflow
    assert "desktop-sbom.json" in workflow
    assert "desktop-exact-head-evidence.json" in workflow
    assert 'pnpm --dir desktop pkg set version="$VERSION"' in workflow
    assert workflow.index('pnpm --dir desktop pkg set version="$VERSION"') < workflow.index(
        "pnpm --dir desktop run sbom"
    )
    assert "pnpm --dir desktop sbom" not in workflow
    assert "download-artifact" in workflow
    assert "desktop-evidence-aggregate" in workflow
    assert "subject-path: dist/*" in workflow
    assert "generate_release_checksums.py --directory dist" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$COMMIT_SHA"' in workflow
    assert "RAW_ARTIFACT_DIGEST: ${{ steps.desktop_release.outputs.artifact-digest }}" in workflow
    assert 'ARTIFACT_DIGEST="sha256:${RAW_ARTIFACT_DIGEST,,}"' in workflow
    assert '[[ "$ARTIFACT_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]' in workflow
    assert "Desktop-Release-Artifact-Digest: $ARTIFACT_DIGEST" in workflow
    assert "id: macos-host" in workflow
    assert "id: windows-host" in workflow
    assert "id: linux-host" in workflow
    assert workflow.count('echo "actual_os=$actual_os" >> "$GITHUB_OUTPUT"') >= 2
    assert '"actual_os=$actualOs" >> $env:GITHUB_OUTPUT' in workflow
    assert "steps.macos-host.outputs.actual_os" in workflow
    assert "steps.windows-host.outputs.actual_os" in workflow
    assert "steps.linux-host.outputs.actual_os" in workflow
    assert '--actual-os "$ACTUAL_OS"' in workflow
    assert "--gate operatingSystemPassed" in workflow
    desktop_aggregate = workflow.index("assemble_desktop_release.py aggregate")
    desktop_checksums = workflow.index(
        "generate_release_checksums.py --directory dist", desktop_aggregate
    )
    tag_assemble = workflow.index("  assemble-release-distributions:")
    final_manifest = workflow.index("scripts/create_release_evidence.py", tag_assemble)
    final_checksums = workflow.index("generate_release_checksums.py --directory dist", tag_assemble)
    assert desktop_aggregate < desktop_checksums < tag_assemble
    assert tag_assemble < final_manifest < final_checksums
    for publisher in (
        "publish-build-provenance",
        "draft-github-release",
        "publish-testpypi",
        "publish-pypi",
        "publish-github-release",
    ):
        job = workflow[workflow.index(f"  {publisher}:") :]
        assert "assemble-release-distributions" in job.split("runs-on:", maxsplit=1)[0]

    tag_import = workflow[workflow.index("  import-desktop-release-distributions:") :]
    assert 'row["actualOs"]' in tag_import
    assert '("macos-26", "darwin-arm64", "macOS 26", "macOS 26", "arm64", "arm64")' in tag_import
    assert 'row["hostArch"]' in tag_import


def test_release_docs_define_the_exact_matrix_and_manual_upgrade_contract() -> None:
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    desktop = (ROOT / "docs" / "DESKTOP_VERIFICATION.md").read_text(encoding="utf-8")
    desktop_shell = (ROOT / "docs" / "DESKTOP_SHELL.md").read_text(encoding="utf-8")
    release_notes = (ROOT / "docs" / "release-notes" / "0.5.0.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    normalized = " ".join(
        (
            releasing
            + "\n"
            + desktop
            + "\n"
            + desktop_shell
            + "\n"
            + architecture
            + "\n"
            + changelog
            + "\n"
            + release_notes
        ).split()
    )
    normalized_shell = " ".join(desktop_shell.split())

    for expected in (
        "macOS 15 arm64",
        "macOS 15 x64",
        "Windows 11 ARM64",
        "Ubuntu 24.04 x64",
        "SHA256SUMS",
        "Authenticode",
        "Gatekeeper",
        "notarization",
        "detached GPG signature",
        "full installer",
    ):
        assert expected in normalized
    assert "no updater feed" in normalized
    assert "no background update" in normalized
    assert "no staged rollout" in normalized
    assert "no automatic rollback" in normalized
    assert (
        "native ARM64 Python and Node.js to build and validate the shipped Windows ARM64 application"
        in normalized
    )
    assert (
        "The supported 0.5.0 targets are macOS 15 and 26 on arm64 and x64, "
        "Windows 11 on arm64, and Ubuntu 24.04 on x64." in normalized_shell
    )
    assert 'binarySigningMode: "unsigned"' in releasing
    assert 'releaseTagMode: "unsigned-annotated"' in releasing
    assert "git tag --no-sign -a" in releasing
    assert "git tag -s" in releasing
    assert "Release-tag mode for this release: `unsigned-annotated`" in release_notes
    assert "Windows 11 | arm64 | NSIS executable" in release_notes
    assert "Windows 11 on x64" not in normalized
    assert "may be self-signed" not in normalized


def test_desktop_release_assembler_rejects_incomplete_target_evidence(tmp_path: Path) -> None:
    installer = tmp_path / "AncestryLLM-0.5.0-ubuntu-x64.deb"
    installer.write_bytes(b"installer")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ASSEMBLER),
            "target",
            "--git-head",
            "a" * 40,
            "--version",
            "0.5.0",
            "--signing-mode",
            "unsigned",
            "--target",
            "linux-x64",
            "--expected-os",
            "Ubuntu 24.04",
            "--arch",
            "x64",
            "--installer",
            str(installer),
            "--sbom",
            str(sbom),
            "--output",
            str(tmp_path / "evidence.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode != 0
    assert "missing required verification gate" in completed.stderr
