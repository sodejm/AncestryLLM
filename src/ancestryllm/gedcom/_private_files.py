"""Native privacy checks for worker directories and files."""

from __future__ import annotations

import hashlib
import os
import platform
import stat
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path


def current_user_scope() -> str:
    """Return a filesystem-safe identity for separating users' worker roots."""

    if platform.system() != "Windows":
        getuid = getattr(os, "geteuid", None)
        if getuid is None:
            raise OSError("The current user identity is unavailable.")
        return f"uid-{getuid()}"
    sid = _windows_current_user_sid_string()
    return "sid-" + hashlib.sha256(sid.encode("ascii")).hexdigest()[:16]


def harden_private_directory(path: Path) -> bool:
    """Apply native private-directory protection and verify the result."""

    if platform.system() != "Windows":
        path.chmod(0o700)
    return is_private_directory(path)


def harden_private_file(path: Path) -> bool:
    """Apply native private-file protection and verify the result."""

    if platform.system() != "Windows":
        path.chmod(0o600)
    return is_private_file(path)


def is_private_directory(path: Path) -> bool:
    """Verify a directory is owned by this user and inaccessible to others."""

    return _is_private_path(path, directory=True)


def is_private_file(path: Path) -> bool:
    """Verify a regular file is owned by this user and inaccessible to others."""

    return _is_private_path(path, directory=False)


def _is_private_path(path: Path, *, directory: bool) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        return False
    if platform.system() == "Windows":
        return _windows_path_is_private(path, directory=directory)
    getuid = getattr(os, "geteuid", None)
    if getuid is None or metadata.st_uid != getuid():
        return False
    expected = 0o700 if directory else 0o600
    return stat.S_IMODE(metadata.st_mode) == expected


def _windows_current_user_sid_string() -> str:
    """Read the current token SID through the Windows security API."""

    import ctypes
    from ctypes import wintypes

    advapi32 = cast("Any", ctypes).windll.advapi32
    kernel32 = cast("Any", ctypes).windll.kernel32
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_uint,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("The current Windows token is unavailable.")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise OSError("The current Windows token is invalid.")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, required.value, ctypes.byref(required)
        ):
            raise OSError("The current Windows token is unavailable.")
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents.value
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("The current Windows identity is unavailable.")
        try:
            if sid_text.value is None:
                raise OSError("The current Windows identity is unavailable.")
            return sid_text.value
        finally:
            kernel32.LocalFree(sid_text)
    finally:
        kernel32.CloseHandle(token)


def _windows_path_is_private(path: Path, *, directory: bool) -> bool:
    """Verify owner and allow ACEs using Windows native security descriptors."""

    del directory  # The native ACL rule is identical for files and directories.
    try:
        import ctypes
        from ctypes import wintypes

        class _AclSizeInformation(ctypes.Structure):
            _fields_ = [
                ("AceCount", wintypes.DWORD),
                ("AclBytesInUse", wintypes.DWORD),
                ("AclBytesFree", wintypes.DWORD),
            ]

        class _AceHeader(ctypes.Structure):
            _fields_ = [
                ("AceType", ctypes.c_ubyte),
                ("AceFlags", ctypes.c_ubyte),
                ("AceSize", wintypes.WORD),
            ]

        class _AccessAllowedAce(ctypes.Structure):
            _fields_ = [
                ("Header", _AceHeader),
                ("Mask", wintypes.DWORD),
                ("SidStart", wintypes.DWORD),
            ]

        advapi32 = cast("Any", ctypes).windll.advapi32
        kernel32 = cast("Any", ctypes).windll.kernel32
        advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_uint,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID),
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi32.GetAclInformation.argtypes = [
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_uint,
        ]
        advapi32.GetAclInformation.restype = wintypes.BOOL
        advapi32.GetAce.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        advapi32.GetAce.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
        advapi32.EqualSid.restype = wintypes.BOOL
        advapi32.IsWellKnownSid.argtypes = [wintypes.LPVOID, ctypes.c_int]
        advapi32.IsWellKnownSid.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL

        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        status = advapi32.GetNamedSecurityInfoW(
            str(path),
            1,
            0x00000001 | 0x00000004,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0 or not owner or not dacl or not descriptor:
            return False
        try:
            current_sid = _windows_current_user_sid_string()
            owner_text = _windows_sid_string(owner)
            if owner_text != current_sid:
                return False
            information = _AclSizeInformation()
            if not advapi32.GetAclInformation(
                dacl, ctypes.byref(information), ctypes.sizeof(information), 2
            ):
                return False
            for index in range(information.AceCount):
                ace = wintypes.LPVOID()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    return False
                header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
                if header.AceType == 1:  # ACCESS_DENIED_ACE_TYPE
                    continue
                if header.AceType != 0:  # Fail closed on unparsed allow ACE forms.
                    return False
                if ace.value is None:
                    return False
                sid_address = ace.value + _AccessAllowedAce.SidStart.offset
                sid = wintypes.LPVOID(sid_address)
                if _windows_sid_string(sid) == current_sid:
                    continue
                if advapi32.IsWellKnownSid(sid, 22):  # WinLocalSystemSid
                    continue
                if advapi32.IsWellKnownSid(sid, 26):  # WinBuiltinAdministratorsSid
                    continue
                return False
            return True
        finally:
            kernel32.LocalFree(descriptor)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _windows_sid_string(sid: object) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = cast("Any", ctypes).windll.advapi32
    kernel32 = cast("Any", ctypes).windll.kernel32
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    sid_text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
        raise OSError("The Windows SID is invalid.")
    try:
        if sid_text.value is None:
            raise OSError("The Windows SID is invalid.")
        return sid_text.value
    finally:
        kernel32.LocalFree(sid_text)
