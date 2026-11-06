"""Locate an installed SQLCipher library without assuming one machine's prefix."""

import ctypes
import ctypes.util
import os
import shutil
from pathlib import Path


def library_path():
    configured = os.environ.get("NOTE_BRIDGE_SQLCIPHER_LIBRARY")
    if configured:
        return configured
    binary = shutil.which("sqlcipher")
    if binary:
        directory = Path(binary).resolve().parent.parent / "lib"
        for name in ("libsqlcipher.dylib", "libsqlcipher.so"):
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return ctypes.util.find_library("sqlcipher")


def library_available():
    path = library_path()
    if not path:
        return False
    try:
        ctypes.CDLL(path)
        return True
    except OSError:
        return False
