"""Translate held Windows handles into Python-compatible filesystem identities."""

from __future__ import annotations


def windows_stat_identity(handle: int) -> tuple[int, int]:
    """Read Python's full Windows stat identity from an already-held file or directory."""
    import ctypes
    from ctypes import wintypes

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_uint64), ("file_id", ctypes.c_ubyte * 16)]

    information = _FileIdInfo()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    get_information.restype = wintypes.BOOL
    if not get_information(handle, 18, ctypes.byref(information), ctypes.sizeof(information)):
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise ctypes.WinError(error)  # type: ignore[attr-defined]
    inode = int.from_bytes(information.file_id, "little")
    if inode == 0:
        raise OSError("The file handle has no reliable identity.")
    return int(information.volume), inode
