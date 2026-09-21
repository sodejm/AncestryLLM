"""Native ABI regressions exercised without requiring a Windows test host."""

import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.core import atomic_file, publication
from ancestryllm.gedcom import sync_publication


class NativeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", ctypes.c_ubyte),
        ("root", ctypes.c_void_p),
        ("length", ctypes.c_uint32),
        ("name", ctypes.c_uint16 * 1),
    ]


def test_relative_rename_uses_native_directory_handle(monkeypatch):
    calls = []

    def rename(handle, status, information, size, information_class):
        info = ctypes.cast(information, ctypes.POINTER(RenameInfo)).contents
        assert handle == 101
        assert information_class == 10
        assert info.root == 102
        assert info.replace == 0
        encoded = "tree-🌳.ged".encode("utf-16-le")
        assert info.length == len(encoded)
        assert size >= RenameInfo.name.offset + len(encoded) + 2
        assert (
            ctypes.string_at(ctypes.addressof(info) + RenameInfo.name.offset, len(encoded) + 2)
            == encoded + b"\0\0"
        )
        calls.append(True)
        return 0

    ntdll = SimpleNamespace(NtSetInformationFile=NativeFunction(rename))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: ntdll, raising=False)
    original_import = publication.importlib.import_module
    monkeypatch.setattr(
        publication.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(get_osfhandle=lambda fd: fd + 100)
            if name == "msvcrt"
            else original_import(name)
        ),
    )
    publication._windows_rename_descriptor_no_replace(
        1, SimpleNamespace(descriptor=2), "tree-🌳.ged"
    )
    assert calls == [True]


def test_absolute_directory_rename_buffer_is_terminated(monkeypatch):
    destination = Path("release-🌳")
    calls = []

    def rename(handle, information_class, information, size):
        info = ctypes.cast(information, ctypes.POINTER(RenameInfo)).contents
        encoded = str(destination.absolute()).encode("utf-16-le")
        assert info.length == len(encoded)
        assert size >= RenameInfo.name.offset + len(encoded) + 2
        assert (
            ctypes.string_at(ctypes.addressof(info) + RenameInfo.name.offset, len(encoded) + 2)
            == encoded + b"\0\0"
        )
        calls.append(True)
        return 1

    # Use fixed-width Windows ABI types even on Unix test hosts.
    monkeypatch.setattr(sync_publication.wintypes, "DWORD", ctypes.c_uint32)
    monkeypatch.setattr(sync_publication.wintypes, "WCHAR", ctypes.c_uint16)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *a, **kw: SimpleNamespace(SetFileInformationByHandle=NativeFunction(rename)),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    sync_publication._windows_rename_held_directory(101, destination)
    assert calls == [True]


def test_fingerprint_read_shares_delete_and_transfers_handle(monkeypatch):
    calls = []

    def create(path, access, share, security, creation, flags, template):
        assert path == "marker"
        assert access == 0x80000000
        assert share == 7
        assert creation == 3
        assert flags & 0x00200000
        calls.append(True)
        return 101

    kernel = SimpleNamespace(
        CreateFileW=NativeFunction(create), CloseHandle=NativeFunction(lambda handle: 1)
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(open_osfhandle=lambda handle, flags: 202),
    )
    assert atomic_file._windows_open_fingerprint_descriptor(Path("marker")) == 202
    assert calls == [True]


def test_fingerprint_read_closes_native_handle_if_transfer_fails(monkeypatch):
    closed = []

    def fail_transfer(handle, flags):
        raise OSError("CRT transfer failed")

    kernel = SimpleNamespace(
        CreateFileW=NativeFunction(lambda *args: 101),
        CloseHandle=NativeFunction(closed.append),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=fail_transfer))
    with pytest.raises(OSError, match="CRT transfer failed"):
        atomic_file._windows_open_fingerprint_descriptor(Path("marker"))
    assert closed == [101]
