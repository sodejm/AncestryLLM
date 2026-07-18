import os
import subprocess
from pathlib import Path


DOCTOR_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "doctor.sh"


def _write_stub_command(bin_dir: Path, name: str) -> None:
    stub_path = bin_dir / name
    stub_path.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub_path.chmod(0o755)


def _run_doctor(tmp_path: Path, env_contents: str | None) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command_name in ("python3", "docker", "git", "brew"):
        _write_stub_command(bin_dir, command_name)

    if env_contents is not None:
        (tmp_path / ".env").write_text(env_contents)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(DOCTOR_SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_doctor_treats_dotenv_values_as_literal_strings(tmp_path: Path):
    actual_tree_dir = tmp_path / "trees"
    actual_tree_dir.mkdir()
    (actual_tree_dir / "alpha.rmtree").write_text("")

    result = _run_doctor(
        tmp_path,
        env_contents=f"FAMILY_TREES_HOST_DIR=$(printf {actual_tree_dir})\n",
    )

    assert result.returncode == 1
    assert f"[missing] RootsMagic directory: $(printf {actual_tree_dir})" in result.stdout


def test_doctor_accepts_quoted_family_tree_paths(tmp_path: Path):
    actual_tree_dir = tmp_path / "trees"
    actual_tree_dir.mkdir()
    (actual_tree_dir / "alpha.rmtree").write_text("")

    result = _run_doctor(
        tmp_path,
        env_contents=f'FAMILY_TREES_HOST_DIR="{actual_tree_dir}"\n',
    )

    assert result.returncode == 0
    assert "Environment checks passed." in result.stdout
