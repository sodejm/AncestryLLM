"""Contracts for the authoritative WebdriverIO Electron E2E boundary."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
PACKAGE = DESKTOP / "package.json"
WDIO_CONFIG = DESKTOP / "wdio.conf.ts"
WDIO_RUNNER = DESKTOP / "scripts" / "run-wdio.mjs"
NORMAL_LAUNCH_VERIFIER = DESKTOP / "scripts" / "verify-normal-launch.mjs"
PACKAGED_RUNNER = DESKTOP / "scripts" / "run-packaged-tests.mjs"
SOURCE_SPEC = DESKTOP / "e2e" / "shell.wdio.ts"
PACKAGED_SPEC = DESKTOP / "e2e" / "packaged-shell.wdio.ts"
DOCS_SPEC = DESKTOP / "e2e" / "docs-screenshots.spec.ts"
PLAYWRIGHT_CONFIG = DESKTOP / "playwright.config.ts"
PNPM_WORKSPACE = DESKTOP / "pnpm-workspace.yaml"


def test_webdriverio_dependencies_and_commands_are_exactly_locked() -> None:
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    dependencies = package["devDependencies"]

    assert dependencies["@wdio/electron-service"] == "10.0.0"
    for name in (
        "@wdio/cli",
        "@wdio/local-runner",
        "@wdio/mocha-framework",
        "@wdio/spec-reporter",
        "webdriverio",
    ):
        assert dependencies[name] == "9.31.2"
    assert dependencies["@wdio/globals"] == "9.31.1"
    assert "tsx: 4.21.1" in PNPM_WORKSPACE.read_text(encoding="utf-8")

    scripts = package["scripts"]
    assert "run-wdio.mjs source" in scripts["test:e2e"]
    assert "run-wdio.mjs packaged" in scripts["test:e2e:packaged"]
    assert "run-wdio.mjs source" in scripts["test:accessibility"]
    assert "run-wdio.mjs source" in scripts["test:visual"]
    assert "playwright" not in scripts["test:e2e"]
    assert "playwright" not in scripts["test:e2e:packaged"]


def test_wdio_config_owns_source_and_packaged_electron_sessions() -> None:
    source = WDIO_CONFIG.read_text(encoding="utf-8")

    assert "runner: 'local'" in source
    assert "framework: 'mocha'" in source
    assert "browserName: 'electron'" in source
    assert "'wdio:electronServiceOptions'" in source
    assert "shell.wdio.ts" in source
    assert "packaged-shell.wdio.ts" in source
    assert "@wdio/electron-service" in source
    assert "beforeSession" in source
    assert "ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT" in source
    assert "String(Date.now())" in source


def test_product_suites_have_no_playwright_or_manual_cdp_control_plane() -> None:
    combined = "\n".join(
        (SOURCE_SPEC.read_text(encoding="utf-8"), PACKAGED_SPEC.read_text(encoding="utf-8"))
    )

    for forbidden in (
        "@playwright/test",
        "connectOverCDP",
        "newBrowserCDPSession",
        "SystemInfo.getProcessInfo",
        "remote-debugging-port",
        "waitForDevToolsEndpoint",
        "chromium.launch",
    ):
        assert forbidden not in combined
    assert "browser.electron.execute" in combined
    assert "browser.reloadSession" in combined
    assert "browser.electron.execute" not in PACKAGED_SPEC.read_text(encoding="utf-8")


def test_wdio_runner_preserves_multiword_filters_without_a_shell() -> None:
    runner = WDIO_RUNNER.read_text(encoding="utf-8")
    packaged_runner = PACKAGED_RUNNER.read_text(encoding="utf-8")

    assert "@wdio/cli" in runner
    assert "shell: false" in runner
    assert "--mochaOpts.grep" in runner
    assert "runWdioPlan('packaged'" in packaged_runner
    assert "@playwright/test/cli" not in packaged_runner
    assert "ANCESTRYLLM_WDIO_LAUNCH_STARTED_AT" not in runner


def test_playwright_is_bounded_to_reviewed_documentation_screenshots() -> None:
    playwright_config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    assert DOCS_SPEC.is_file()
    assert "docs-screenshots.spec.ts" in playwright_config
    assert not (DESKTOP / "e2e" / "shell.spec.ts").exists()
    assert not (DESKTOP / "e2e" / "packaged-shell.spec.ts").exists()


def test_packaged_suite_keeps_release_and_negative_security_scenarios() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    runner = WDIO_RUNNER.read_text(encoding="utf-8")

    for scenario in (
        "exercises first run, persistence, corrupt preferences, security, and resource evidence",
        "withholds and restores the packaged sidecar through Diagnostics retry",
        "exhausts packaged sidecar restarts and exits cleanly",
        "rejects a substituted packaged sidecar before launch",
        "mediates opaque packaged open and save file grants",
    ):
        assert scenario in source
    normal_scenario = (
        "launches the selected packaged runtime normally without a debugging transport"
    )
    assert normal_scenario not in source
    assert normal_scenario in runner
    assert "verify-normal-launch.mjs" in runner
    assert NORMAL_LAUNCH_VERIFIER.is_file()


def test_packaged_boundary_is_checked_before_leaving_home() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    scenario = source.split(
        "it('exercises first run, persistence, corrupt preferences, security, and resource evidence'",
        maxsplit=1,
    )[1].split("\n  it(", maxsplit=1)[0]

    assert scenario.index("expectProductionBoundary") < scenario.index("click('a=Diagnostics')")


def test_packaged_boundary_reads_the_document_csp_without_fetch_access_to_app_scheme() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")

    assert 'meta[http-equiv="Content-Security-Policy"]' in source
    assert "fetch(location.href)" not in source


def test_packaged_renderer_egress_metric_is_measured_at_the_network_layer() -> None:
    config = WDIO_CONFIG.read_text(encoding="utf-8")
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    scenario = source.split(
        "it('exercises first run, persistence, corrupt preferences, security, and resource evidence'",
        maxsplit=1,
    )[1].split("\n  it(", maxsplit=1)[0]

    assert "'goog:loggingPrefs'" in config
    assert "performance: 'ALL'" in config
    assert "browser.getLogs('performance')" in source
    assert "Network.requestWillBeSent" in source
    assert "Network.webSocketWillSendHandshakeRequest" in source
    assert "performance.getEntriesByType('resource')" not in source
    assert "rendererOutboundRequests: 0" not in scenario
    assert "rendererOutboundRequests," in scenario


def test_packaged_zoom_uses_equivalent_renderer_scale() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    scenario = source.split("async function expectAccessibleShell()", maxsplit=1)[1].split(
        "\n}\n\ndescribe('unpublished unpacked native package'", maxsplit=1
    )[0]

    assert "window.resizeTo(720, 560)" in scenario
    assert "window.outerWidth" in scenario
    assert "window.outerHeight" in scenario
    assert "document.documentElement.style.zoom = '200%'" in scenario
    assert "getComputedStyle(document.documentElement).zoom" in scenario
    assert "document.documentElement.style.removeProperty('zoom')" in scenario
    assert "browser.getWindowSize" not in scenario
    assert "browser.setWindowSize" not in scenario
    assert "browser.keys" not in scenario
    assert "zoomModifier" not in scenario


def test_packaged_warm_launch_timing_starts_at_the_replacement_process() -> None:
    source = PACKAGED_SPEC.read_text(encoding="utf-8")
    scenario = source.split(
        "it('exercises first run, persistence, corrupt preferences, security, and resource evidence'",
        maxsplit=1,
    )[1].split("\n  it(", maxsplit=1)[0]

    assert "async function mainPid(excluded: ReadonlySet<number> = new Set())" in source
    assert "!excluded.has(record.pid)" in source
    assert "async function processStartedAt(pid: number): Promise<number>" in source
    assert "const previousApplicationPid = await mainPid()" in scenario
    assert "await browser.reloadSession()" in scenario
    assert (
        "const replacementApplicationPid = await mainPid(new Set([previousApplicationPid]))"
        in scenario
    )
    assert "const warmLaunchedAt = await processStartedAt(replacementApplicationPid)" in scenario
    assert "const warmLaunchMs = Date.now() - warmLaunchedAt" in scenario
    assert "const warmStartedAt = Date.now()" not in scenario


def test_websocket_csp_evidence_requires_a_matching_policy_violation() -> None:
    for spec in (SOURCE_SPEC, PACKAGED_SPEC):
        source = spec.read_text(encoding="utf-8")
        denied_start = source.index("const webSocketBlocked = await new Promise<boolean>")
        denied_end = source.index("let serviceWorkerBlocked", denied_start)
        denied_source = source[denied_start:denied_end]

        assert "securitypolicyviolation" in denied_source
        assert "event.disposition === 'enforce'" in denied_source
        assert "event.effectiveDirective === 'connect-src'" in denied_source
        assert "event.blockedURI.startsWith('wss://example.invalid')" in denied_source
        assert denied_source.index("document.addEventListener") < denied_source.index(
            "new WebSocket"
        )
        assert "resolve(true)" not in denied_source
