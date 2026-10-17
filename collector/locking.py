"""Process wide lock used by collector writers.

The collector is deliberately a command line process.  A lock therefore has
to be held by an open file handle, rather than inferred from a marker file:
the latter leaves stale locks behind when a process is killed.  ``fcntl`` is
used on POSIX and ``msvcrt`` on Windows so the same interface works for the
scheduled task and for a manually started collector.
"""

from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
from typing import Iterator, Union


class BusyError(RuntimeError):
    """Raised when another collector process owns the lock."""


PathLike = Union[str, os.PathLike[str]]


def lock_path(data_root: PathLike) -> Path:
    """Return the lock path for a data directory.

    Creating this file is harmless and does not create the SQLite business
    database.  ``is_running`` only probes an existing file so a status read
    does not leave even a lock marker behind.
    """

    return Path(data_root) / ".collector.lock"


class CollectorLock:
    """An exclusive, non-blocking inter-process lock.

    The default is non-blocking because callers need to turn contention into
    the stable CLI exit code 3.  The descriptor remains open for the entire
    context, which is what makes the lock survive a process that has not
    cleaned up its temporary files.
    """

    def __init__(self, path: PathLike):
        self.path = Path(path)
        self._file = None
        self._locked = False

    def acquire(self) -> "CollectorLock":
        if self._locked:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ``a+b`` avoids truncating another process's marker.  The marker is
        # informational only; ownership is represented by the OS lock below.
        handle = self.path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            self._lock_descriptor(handle)
        except (OSError, IOError) as exc:
            handle.close()
            if self._is_contention(exc):
                raise BusyError(f"collector is already running: {self.path}") from exc
            raise
        self._file = handle
        self._locked = True
        return self

    @staticmethod
    def _is_contention(exc: BaseException) -> bool:
        # Windows reports EACCES/EDEADLK for a locked byte; POSIX flock uses
        # EACCES/EAGAIN.  Treat both as contention while allowing unrelated
        # I/O errors to propagate to the caller.
        return getattr(exc, "errno", None) in {
            errno.EACCES,
            errno.EAGAIN,
            errno.EDEADLK,
            errno.EPERM,
        }

    @staticmethod
    def _lock_descriptor(handle) -> None:
        if os.name == "nt":
            import msvcrt

            # msvcrt.locking works on a byte range at the current file
            # position and requires a byte to exist in the file.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_descriptor(handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                # Closing the descriptor also releases a Windows lock.  This
                # fallback keeps release idempotent after an interrupted exit.
                pass
            return
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def release(self) -> None:
        handle = self._file
        self._file = None
        self._locked = False
        if handle is None:
            return
        try:
            self._unlock_descriptor(handle)
        finally:
            handle.close()

    def __enter__(self) -> "CollectorLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def try_lock(data_root: PathLike) -> CollectorLock:
    """Acquire the data-root lock or raise :class:`BusyError`."""

    return CollectorLock(lock_path(data_root)).acquire()


def is_running(data_root: PathLike) -> bool:
    """Return whether another process currently owns the collector lock.

    A status read must not create the SQLite database.  It also should not
    create a lock marker merely to answer this question, so a missing lock
    file is treated as not running.
    """

    path = lock_path(data_root)
    if not path.exists():
        return False
    probe = CollectorLock(path)
    try:
        probe.acquire()
    except BusyError:
        return True
    finally:
        probe.release()
    return False


# Friendly aliases for callers that prefer a verb or a predicate name.  Keep
# the canonical implementation above so the CLI and tests share one lock
# protocol.
acquire_lock = try_lock
lock_held = is_running
FileLock = CollectorLock


@contextlib.contextmanager
def locked(data_root: PathLike) -> Iterator[CollectorLock]:
    """Context manager shorthand for :func:`try_lock`."""

    handle = try_lock(data_root)
    try:
        yield handle
    finally:
        handle.release()


__all__ = [
    "BusyError",
    "CollectorLock",
    "FileLock",
    "acquire_lock",
    "is_running",
    "lock_held",
    "lock_path",
    "locked",
    "try_lock",
]
