from __future__ import annotations

from pathlib import Path

from scripts.build_verification_sidecar import executable_path, parse_args


def test_verification_sidecar_uses_a_separate_target_native_output(tmp_path: Path) -> None:
    assert executable_path(tmp_path, "darwin-arm64") == (
        tmp_path / "darwin-arm64" / "ancestryllm-wrong-build-sidecar"
    )
    assert executable_path(tmp_path, "win32-x64") == (
        tmp_path / "win32-x64" / "ancestryllm-wrong-build-sidecar.exe"
    )
    assert executable_path(tmp_path, "win32-arm64") == (
        tmp_path / "win32-arm64" / "ancestryllm-wrong-build-sidecar.exe"
    )


def test_verification_sidecar_builder_accepts_an_explicit_target_and_output(tmp_path: Path) -> None:
    arguments = parse_args(["--output-root", str(tmp_path), "--expected-target", "linux-x64"])

    assert arguments.output_root == tmp_path
    assert arguments.expected_target == "linux-x64"
