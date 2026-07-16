#!/usr/bin/env python3
"""Private local text-file helpers shared by RouterKit entrypoints."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Callable, Optional


class PrivateFileError(Exception):
    """A secret-safe private-file error suitable for user-facing handling."""


class PrivateFileTooLargeError(PrivateFileError):
    pass


class PrivateFileEncodingError(PrivateFileError):
    pass


class PrivatePublicationDurabilityError(PrivateFileError):
    """Publication changed the directory entry but directory sync failed."""


def _validate_private_metadata(metadata: os.stat_result, *, description: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise PrivateFileError(f"{description} must be a regular, non-symlink file.")
    if metadata.st_nlink != 1:
        raise PrivateFileError(f"{description} must not have hard links.")
    if os.name == "posix":
        if metadata.st_uid != os.geteuid():
            raise PrivateFileError(f"{description} must be owned by the current user on POSIX.")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PrivateFileError(f"{description} permissions must be owner-only on POSIX.")


def _validate_private_directory_metadata(
    metadata: os.stat_result, *, description: str
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise PrivateFileError(f"{description} must be a real directory.")
    if os.name == "posix":
        if metadata.st_uid != os.geteuid():
            raise PrivateFileError(f"{description} must be owned by the current user on POSIX.")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PrivateFileError(
                f"{description} permissions must be exactly 0700 on POSIX."
            )


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _same_content_metadata(first: os.stat_result, second: os.stat_result) -> bool:
    fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(first, field, None) == getattr(second, field, None) for field in fields)


def ensure_private_directory(
    path: Path, *, description: str = "Private directory"
) -> None:
    """Create or validate one current-user private directory."""

    directory = Path(path)
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PrivateFileError(f"{description} must not be a symlink.")
        _validate_private_directory_metadata(metadata, description=description)
    except PrivateFileError:
        raise
    except OSError:
        raise PrivateFileError(f"Could not prepare the {description.lower()}.") from None


def _open_private_directory(path: Path, *, description: str) -> int:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise PrivateFileError(f"{description} must not be a symlink.")
    _validate_private_directory_metadata(metadata, description=description)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    opened = os.fstat(fd)
    if not _same_identity(metadata, opened):
        os.close(fd)
        raise PrivateFileError(f"{description} changed before it could be used safely.")
    _validate_private_directory_metadata(opened, description=description)
    return fd


def _read_validated_entry(
    directory_fd: int,
    name: str,
    *,
    maximum_bytes: int,
    description: str,
    validate_text: Callable[[str], None],
) -> Optional[os.stat_result]:
    try:
        path_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(path_metadata.st_mode):
        raise PrivateFileError(f"{description} must be a regular, non-symlink file.")
    _validate_private_metadata(path_metadata, description=description)
    if path_metadata.st_size > maximum_bytes:
        raise PrivateFileTooLargeError(f"{description} is too large.")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    data = bytearray()
    try:
        opened = os.fstat(fd)
        _validate_private_metadata(opened, description=description)
        if not _same_identity(path_metadata, opened):
            raise PrivateFileError(f"{description} changed before it could be read safely.")
        while len(data) <= maximum_bytes:
            chunk = os.read(fd, min(65536, maximum_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > maximum_bytes:
            raise PrivateFileTooLargeError(f"{description} is too large.")
        final = os.fstat(fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not _same_identity(opened, final)
            or not _same_content_metadata(opened, final)
            or not _same_identity(final, current)
        ):
            raise PrivateFileError(f"{description} changed before it could be read safely.")
        _validate_private_metadata(final, description=description)
    finally:
        os.close(fd)

    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise PrivateFileEncodingError(f"{description} must contain UTF-8 text.") from None
    try:
        validate_text(text)
    except PrivateFileError:
        raise
    except Exception:
        raise PrivateFileError(f"{description} is not a recognized RouterKit file.") from None
    return final


def write_private_bytes_atomic(
    path: Path,
    data: bytes,
    *,
    maximum_bytes: int,
    description: str,
    validate_existing_text: Callable[[str], None],
) -> None:
    """Atomically publish bounded bytes over only a validated prior file."""

    destination = Path(path)
    if len(data) > maximum_bytes:
        raise PrivateFileTooLargeError(f"{description} exceeds its safety bound.")
    directory_fd = -1
    temporary_name: Optional[str] = None
    replaced = False
    try:
        directory_fd = _open_private_directory(
            destination.parent, description=f"{description} parent directory"
        )
        existing = _read_validated_entry(
            directory_fd,
            destination.name,
            maximum_bytes=maximum_bytes,
            description=description,
            validate_text=validate_existing_text,
        )

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = -1
        for _attempt in range(32):
            candidate = ".routerkit-private-%s" % secrets.token_hex(12)
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if fd < 0 or temporary_name is None:
            raise OSError("could not create unique temporary file")
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(fd)
            temporary_metadata = os.fstat(fd)
            _validate_private_metadata(temporary_metadata, description="Temporary private file")
            if temporary_metadata.st_size != len(data):
                raise OSError("temporary file size mismatch")
        finally:
            os.close(fd)

        try:
            current = os.stat(
                destination.name, dir_fd=directory_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            current = None
        if existing is None:
            if current is not None:
                raise PrivateFileError(f"{description} destination changed before publication.")
        elif current is None or not _same_identity(existing, current) or not _same_content_metadata(
            existing, current
        ):
            raise PrivateFileError(f"{description} destination changed before publication.")

        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        replaced = True
        try:
            os.fsync(directory_fd)
        except OSError:
            raise PrivatePublicationDurabilityError(
                f"{description} may already be visible, but its directory could not be synchronized."
            ) from None
    except (PrivateFileError, PrivateFileTooLargeError):
        raise
    except OSError:
        if replaced:
            raise PrivatePublicationDurabilityError(
                f"{description} may already be visible, but publication durability is unknown."
            ) from None
        raise PrivateFileError(f"Could not publish the {description.lower()} safely.") from None
    finally:
        if temporary_name is not None and directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        if directory_fd >= 0:
            try:
                os.close(directory_fd)
            except OSError:
                pass


def remove_private_file_if_valid(
    path: Path,
    *,
    maximum_bytes: int,
    description: str,
    validate_text: Callable[[str], None],
) -> bool:
    """Remove only a recognized private file and synchronize its directory."""

    destination = Path(path)
    directory_fd = -1
    removed = False
    try:
        directory_fd = _open_private_directory(
            destination.parent, description=f"{description} parent directory"
        )
        existing = _read_validated_entry(
            directory_fd,
            destination.name,
            maximum_bytes=maximum_bytes,
            description=description,
            validate_text=validate_text,
        )
        if existing is None:
            return False
        current = os.stat(destination.name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(existing, current) or not _same_content_metadata(existing, current):
            raise PrivateFileError(f"{description} destination changed before removal.")
        os.unlink(destination.name, dir_fd=directory_fd)
        removed = True
        try:
            os.fsync(directory_fd)
        except OSError:
            raise PrivatePublicationDurabilityError(
                f"{description} was removed, but its directory could not be synchronized."
            ) from None
        return True
    except PrivateFileError:
        raise
    except OSError:
        if removed:
            raise PrivatePublicationDurabilityError(
                f"{description} was removed, but removal durability is unknown."
            ) from None
        raise PrivateFileError(f"Could not retire the prior {description.lower()} safely.") from None
    finally:
        if directory_fd >= 0:
            try:
                os.close(directory_fd)
            except OSError:
                pass


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
