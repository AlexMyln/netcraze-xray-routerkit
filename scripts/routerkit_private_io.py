#!/usr/bin/env python3
"""Private local text-file helpers shared by RouterKit entrypoints."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateFileError(Exception):
    """A secret-safe private-file error suitable for user-facing handling."""


class PrivateFileTooLargeError(PrivateFileError):
    pass


class PrivateFileEncodingError(PrivateFileError):
    pass


def _validate_private_metadata(metadata: os.stat_result, *, description: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise PrivateFileError(f"{description} must be a regular, non-symlink file.")
    if metadata.st_nlink != 1:
        raise PrivateFileError(f"{description} must not have hard links.")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PrivateFileError(f"{description} permissions must be owner-only on POSIX.")


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _same_content_metadata(first: os.stat_result, second: os.stat_result) -> bool:
    fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(first, field, None) == getattr(second, field, None) for field in fields)


def read_owner_only_text_file(
    path: Path,
    *,
    maximum_bytes: int,
    description: str = "Private file",
) -> str:
    """Read a bounded owner-only regular file without following symlinks.

    The path and opened descriptor must identify the same file. Metadata is
    checked again after the bounded read so concurrent replacement or mutation
    is rejected instead of being passed to a secret-consuming caller.
    """

    source = Path(path)
    fd = -1
    data = bytearray()
    try:
        path_metadata = source.lstat()
        if stat.S_ISLNK(path_metadata.st_mode):
            raise PrivateFileError(f"{description} must be a regular, non-symlink file.")
        _validate_private_metadata(path_metadata, description=description)
        if path_metadata.st_size > maximum_bytes:
            raise PrivateFileTooLargeError(f"{description} is too large.")

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(source, flags)
        opened_metadata = os.fstat(fd)
        _validate_private_metadata(opened_metadata, description=description)
        if not _same_identity(path_metadata, opened_metadata):
            raise PrivateFileError(f"{description} changed before it could be read safely.")
        if opened_metadata.st_size > maximum_bytes:
            raise PrivateFileTooLargeError(f"{description} is too large.")

        while len(data) <= maximum_bytes:
            chunk = os.read(fd, min(65536, maximum_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > maximum_bytes:
            raise PrivateFileTooLargeError(f"{description} is too large.")

        final_metadata = os.fstat(fd)
        current_path_metadata = source.lstat()
        if (
            not _same_identity(opened_metadata, final_metadata)
            or not _same_content_metadata(opened_metadata, final_metadata)
            or not _same_identity(final_metadata, current_path_metadata)
        ):
            raise PrivateFileError(f"{description} changed before it could be read safely.")
        _validate_private_metadata(final_metadata, description=description)
    except (PrivateFileError, PrivateFileTooLargeError):
        raise
    except OSError:
        raise PrivateFileError(f"Could not read the {description.lower()}.") from None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass

    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise PrivateFileEncodingError(f"{description} must contain UTF-8 text.") from None


def write_private_text_exclusive(path: Path, text: str) -> None:
    """Create one UTF-8 text file exclusively with owner-only permissions."""

    destination = Path(path)
    encoded = text.encode("utf-8")
    fd = -1
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(destination, flags, 0o600)
        created = True
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(fd)
    except OSError:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise PrivateFileError("Could not create the private file.") from None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
