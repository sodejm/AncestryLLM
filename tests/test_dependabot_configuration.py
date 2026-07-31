from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"


def _ecosystem_block(configuration: str, ecosystem: str) -> str:
    marker = f'  - package-ecosystem: "{ecosystem}"\n'
    assert marker in configuration
    block = configuration.split(marker, maxsplit=1)[1]
    return block.split("\n  - package-ecosystem:", maxsplit=1)[0]


def test_dependabot_batches_routine_updates_without_delaying_security_fixes() -> None:
    configuration = DEPENDABOT_PATH.read_text(encoding="utf-8")

    assert configuration.startswith("version: 2\nupdates:\n")
    assert configuration.count('package-ecosystem: "') == 2

    for ecosystem in ("pip", "github-actions"):
        block = _ecosystem_block(configuration, ecosystem)
        assert 'interval: "weekly"' in block
        assert "cooldown:\n      default-days: 3" in block
        assert "groups:\n      weekly-batch:" in block
        assert "applies-to: version-updates" in block
        assert 'patterns:\n          - "*"' in block

    # Security updates must not inherit a routine-update label or cadence.
    pip_block = _ecosystem_block(configuration, "pip")
    assert '      - "security"' not in pip_block
