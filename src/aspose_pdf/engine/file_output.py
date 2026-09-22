"""Writing a finished document to disk without risking the one already there.

``Path.write_bytes`` opens its target for writing, which truncates it, and only
then writes. Saving a document over the file it was loaded from -- the usual
way to edit a file, and what ``overwrite=True`` exists for -- therefore
destroyed the original the moment the write began: a full disk, a killed
process or a lost volume left a truncated file, often an empty one, and the
document it had held was gone.

:func:`write_file_atomically` stages the bytes in a sibling file and moves it
over the target in one step, so a failure at any point leaves the target
exactly as it was.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
from pathlib import Path

__all__ = ["write_file_atomically"]

_STAGING_ATTEMPTS = 16


def write_file_atomically(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace *path* with *data*, all at once or not at all.

    The bytes go to a new file in the target's own directory -- the same
    filesystem, so the final ``os.replace`` is a rename, which POSIX and
    Windows both perform atomically -- and are flushed to the device before
    that rename, so a power loss cannot leave a renamed but empty file either.
    Until the rename, the target is untouched; if anything fails, the staged
    file is removed and the error propagates.

    What ``write_bytes`` did that is kept:

    * a **symlink** is written through, not replaced -- the file it names is
      the one that changes;
    * an existing target keeps its **permission bits**, and a new one gets the
      mode ``open()`` would give it (``0o666`` less the umask);
    * a target that is **not writable** is refused, as opening it was, rather
      than silently renamed over.

    What cannot be kept: a replaced file is a new inode, so other **hard
    links** to the old one keep the old content. If the directory cannot hold
    a staging file, the write fails without touching the target.
    """
    target = Path(os.path.realpath(path))
    if target.exists() and not os.access(target, os.W_OK):
        raise PermissionError(f"Permission denied: '{target}'")

    descriptor, staging_path = _open_staging_file(target)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copymode(target, staging_path)
        os.replace(staging_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(staging_path)
        raise
    _sync_directory(target.parent)


def _open_staging_file(target: Path) -> tuple[int, str]:
    """Create an empty sibling of *target* to stage into, or raise.

    Created with ``os.open`` and mode ``0o666`` so the process umask applies
    exactly as it would to ``open(target, "wb")`` -- ``tempfile.mkstemp``
    would make it ``0o600``, and a document saved for others to read would
    quietly stop being readable by them.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(_STAGING_ATTEMPTS):
        candidate = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(candidate, flags, 0o666), str(candidate)
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not create a unique staging file beside {target}")


def _sync_directory(directory: Path) -> None:
    """Make the rename itself durable where the platform allows it."""
    if not hasattr(os, "O_DIRECTORY"):
        return  # Windows: a directory cannot be opened to sync
    with contextlib.suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
