"""Windows journal access checks, with native checks on Windows runners."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from ancestryllm.core.errors import AncestryError
from ancestryllm.core.mutation import coordinator_namespace
from ancestryllm.core.windows_mutation import private_descriptor

if TYPE_CHECKING:
    from pathlib import Path

SID = "S-1-5-21-100-200-300-1001"


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows file timestamps")
def test_windows_publication_path_and_descriptor_agree(tmp_path: Path) -> None:
    from ancestryllm.core.publication import _identity, _PathIdentity

    path = tmp_path / "fictional"
    path.write_bytes(b"old")
    with path.open("r+b") as stream:
        stream.write(b"new fictional content\r\n\x1a")
        stream.flush()
        os.fsync(stream.fileno())
        held = _PathIdentity.from_stat(os.fstat(stream.fileno()))
        assert held.pristine(_identity(path)), (held, _identity(path))
    assert held.pristine(_identity(path))


@pytest.mark.parametrize(
    "descriptor",
    [
        f"O:{SID}D:P(A;OICI;FA;;;{SID})",
        f"O:{SID}G:SYD:AI(A;ID;FA;;;{SID})(A;ID;FA;;;BA)(A;ID;FA;;;SY)",
        f"O:{SID}D:(D;;FW;;;WD)(A;;FA;;;{SID})",
    ],
)
def test_private_windows_acl(descriptor: str) -> None:
    assert private_descriptor(descriptor, SID)


@pytest.mark.parametrize(
    "descriptor",
    [
        f"O:{SID}D:NO_ACCESS_CONTROL",
        f"O:{SID}",
        f"O:BAD:(A;;FA;;;{SID})",
        f"O:{SID}D:(A;;FR;;;WD)",
        f"O:{SID}D:(A;OICIIO;FA;;;AU)",
        f"O:{SID}D:(OA;;FA;;;{SID})",
        f"O:{SID}D:(XA;;FA;;;{SID};(TRUE))",
        f"O:{SID}D:(A;;FA;;;{SID})unparsed",
    ],
)
def test_unsafe_windows_acl(descriptor: str) -> None:
    assert not private_descriptor(descriptor, SID)


def test_windows_acl_resolves_native_sid_aliases() -> None:
    aliases = {"LA": SID, "BA": "S-1-5-32-544", "SY": "S-1-5-18"}

    def resolve(value: str) -> str:
        return aliases.get(value, value)

    descriptor = "O:LAD:P(A;OICI;FA;;;LA)(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)"
    assert private_descriptor(descriptor, SID, resolve_sid=resolve)
    assert not private_descriptor(descriptor, SID + "0", resolve_sid=resolve)
    assert not private_descriptor(f"O:{SID}D:(A;;FA;;;LA)", SID)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows ACL APIs")
def test_windows_private_directory_descriptor_round_trip(tmp_path: Path) -> None:
    from ancestryllm.core.windows_mutation import _Windows, create_private_directory

    directory = tmp_path / "private"
    windows = _Windows()
    _, sid = windows.account()
    try:
        create_private_directory(directory)
    except AncestryError:
        # Only this fictional test directory is inspected; application errors
        # remain path-free and never disclose account or ACL metadata.
        pytest.fail(
            f"Private directory creation failed: account={sid}, "
            f"descriptor={windows.descriptor(directory)}, "
            f"attributes={directory.lstat().st_file_attributes}"
        )
    assert private_descriptor(windows.descriptor(directory), sid, resolve_sid=windows.resolve_sid)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows account and ACL APIs")
def test_windows_namespace_ignores_environment_and_has_private_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.core.mutation import LocalMutationCoordinator
    from ancestryllm.core.windows_mutation import validate_private

    original = coordinator_namespace()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(tmp_path / "redirected"))
    assert coordinator_namespace() == original
    namespace = tmp_path / "journal"
    with LocalMutationCoordinator(namespace):
        validate_private(namespace)
        for path in namespace.rglob("*"):
            validate_private(path)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows ACL APIs")
def test_windows_rejects_inherited_public_acl(tmp_path: Path) -> None:
    from ancestryllm.core.windows_mutation import validate_private

    # Explicitly supply a public ACL so the test does not depend on runner defaults.

    public = tmp_path / "public"
    public.mkdir()
    subprocess.run(
        ["icacls", str(public), "/grant", "*S-1-1-0:(OI)(CI)R"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(AncestryError) as failure:
        validate_private(public)
    assert failure.value.code == "MUTATION_JOURNAL_UNSAFE"
