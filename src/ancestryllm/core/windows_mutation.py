"""Native account and private ACL boundary for the Windows mutation journal.

No environment variable selects the coordination namespace. Directories receive
their protected, inheritable DACL at creation, before any journal bytes exist.
Existing objects are checked, never silently repaired or taken over.
"""

from __future__ import annotations

import ctypes
import re
import stat
from ctypes import wintypes
from pathlib import Path

from ancestryllm.core.errors import AncestryError


def _unsafe() -> AncestryError:
    return AncestryError("MUTATION_JOURNAL_UNSAFE", "The mutation journal access policy is unsafe.")


def private_descriptor(descriptor: str, sid: str) -> bool:
    """Accept only an account-owned DACL with no grants to other ordinary users."""

    match = re.fullmatch(r"O:([^:]+?)(?:G:[^:]+?)?D:((?:P|AI|AR)*)(.*)", descriptor)
    if match is None or match[1] != sid:
        return False
    entries = re.findall(r"\(([^()]*)\)", match[3])
    if "".join(f"({entry})" for entry in entries) != match[3]:
        return False
    for entry in entries:
        parts = entry.split(";")
        if len(parts) != 6 or parts[0] not in ("A", "D") or parts[3] or parts[4]:
            return False
        if parts[0] == "A" and parts[5] not in (sid, "BA", "SY", "S-1-5-32-544", "S-1-5-18"):
            return False
    return True


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", ctypes.c_void_p),
        ("inherit", wintypes.BOOL),
    ]


class _Windows:
    """Explicit signatures avoid pointer truncation on 64-bit Windows."""

    def __init__(self) -> None:
        # ctypes exposes these APIs only on Windows; this module is imported lazily there.
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        self.profile = ctypes.WinDLL("userenv", use_last_error=True)  # type: ignore[attr-defined]
        pointer = ctypes.c_void_p
        out_pointer = ctypes.POINTER(pointer)
        out_dword = ctypes.POINTER(wintypes.DWORD)
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [pointer]
        self.kernel.LocalFree.restype = pointer
        self.kernel.CreateDirectoryW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(_SecurityAttributes),
        ]
        self.kernel.CreateDirectoryW.restype = wintypes.BOOL
        self.security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, out_pointer]
        self.security.OpenProcessToken.restype = wintypes.BOOL
        self.security.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            pointer,
            wintypes.DWORD,
            out_dword,
        ]
        self.security.GetTokenInformation.restype = wintypes.BOOL
        self.security.ConvertSidToStringSidW.argtypes = [pointer, out_pointer]
        self.security.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            out_pointer,
            out_dword,
        ]
        self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            pointer,
            wintypes.DWORD,
            wintypes.DWORD,
            out_pointer,
            out_dword,
        ]
        self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
        self.security.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            out_pointer,
            out_pointer,
            out_pointer,
            out_pointer,
            out_pointer,
        ]
        self.security.GetNamedSecurityInfoW.restype = wintypes.DWORD
        self.profile.GetUserProfileDirectoryW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            out_dword,
        ]
        self.profile.GetUserProfileDirectoryW.restype = wintypes.BOOL

    def account(self) -> tuple[Path, str]:
        token = ctypes.c_void_p()
        if not self.security.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
        ):
            raise _unsafe()
        try:
            length = wintypes.DWORD()
            self.profile.GetUserProfileDirectoryW(token, None, ctypes.byref(length))
            if not 1 <= length.value <= 32768:
                raise _unsafe()
            home = ctypes.create_unicode_buffer(length.value)
            if not self.profile.GetUserProfileDirectoryW(token, home, ctypes.byref(length)):
                raise _unsafe()
            size = wintypes.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 1 <= size.value <= 65536:
                raise _unsafe()
            user = ctypes.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
                raise _unsafe()
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is PSID.
            sid_pointer = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
            text = ctypes.c_void_p()
            if not self.security.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
                raise _unsafe()
            try:
                return Path(home.value), ctypes.wstring_at(text)
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    def descriptor(self, path: Path) -> str:
        descriptor = ctypes.c_void_p()
        # SE_FILE_OBJECT; OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
        if self.security.GetNamedSecurityInfoW(
            str(path), 1, 5, None, None, None, None, ctypes.byref(descriptor)
        ):
            raise _unsafe()
        try:
            text = ctypes.c_void_p()
            if not self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 5, ctypes.byref(text), None
            ):
                raise _unsafe()
            try:
                return ctypes.wstring_at(text)
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.LocalFree(descriptor)


def account_home() -> Path:
    """Resolve the process account profile without consulting environment overrides."""
    return _Windows().account()[0]


def validate_private(path: Path) -> None:
    """Reject redirected objects or access granted to another ordinary account."""
    windows = _Windows()
    _, sid = windows.account()
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", stat.FILE_ATTRIBUTE_REPARSE_POINT)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or not private_descriptor(
        windows.descriptor(path), sid
    ):
        raise _unsafe()


def create_private_directory(path: Path) -> None:
    """Create a protected directory and reject unsafe existing directory permissions."""
    windows = _Windows()
    _, sid = windows.account()
    if not path.parent.exists():
        create_private_directory(path.parent)
    descriptor = ctypes.c_void_p()
    if not windows.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)",
        1,
        ctypes.byref(descriptor),
        None,
    ):
        raise _unsafe()
    try:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        if (
            not windows.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
            and ctypes.get_last_error() != 183  # type: ignore[attr-defined]
        ):
            raise _unsafe()
    finally:
        windows.kernel.LocalFree(descriptor)
    validate_private(path)
