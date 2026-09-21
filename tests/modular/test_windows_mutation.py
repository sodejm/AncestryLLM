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
