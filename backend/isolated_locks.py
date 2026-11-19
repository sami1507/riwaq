"""Nonblocking OS leases for one local data directory.

Lock files are permanent rendezvous points: never unlink or replace them.
The OS releases the open-handle lock after process death. These are lifecycle
locks, not a security boundary against another program running as this OS user.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading


class LeaseError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


_STATE = threading.Lock()
_HELD = {}
_NAMES = {'service': '.riwaq-service.lock', 'worker': '.riwaq-isolated-worker.lock'}


def _path(kind):
    from backend.db import data_dir
    if kind not in _NAMES: raise LeaseError('lock_kind_invalid')
    return data_dir().resolve() / _NAMES[kind]


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 1024)


def _open_locked(path, busy_code):
    """Lock byte zero on Windows, whole file on POSIX; no stale PID guesses."""
    descriptor = None
    try:
        try:
            info = path.lstat()
            if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LeaseError('lock_path_invalid')
        except FileNotFoundError:
            pass
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (_is_link(current) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise LeaseError('lock_path_invalid')
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LeaseError(busy_code) from None
        # msvcrt permits locking beyond EOF. Initialize only AFTER acquisition,
        # so a competing process never writes a currently locked byte.
        if opened.st_size == 0:
            os.write(descriptor, b'\0')
            os.fsync(descriptor)
        return descriptor
    except BaseException:
        if descriptor is not None: os.close(descriptor)
        raise


@contextmanager
def _lease(kind, *, reentrant=False):
    path = _path(kind)
    key = os.path.normcase(str(path))
    owner = (os.getpid(), threading.get_ident())
    busy_code = 'service_already_running' if kind == 'service' else 'isolated_worker_busy'
    root = False
    with _STATE:
        entry = _HELD.get(key)
        if entry:
            if not reentrant or entry['owner'] != owner:
                raise LeaseError(busy_code)
            entry['depth'] += 1
        else:
            descriptor = _open_locked(path, busy_code)
            _HELD[key] = {'owner': owner, 'depth': 1, 'descriptor': descriptor}
            root = True
    try:
        yield
    finally:
        with _STATE:
            entry = _HELD[key]
            entry['depth'] -= 1
            if root:
                try:
                    # Closing releases the lock on both platforms, including
                    # exceptional exits. Do not remove the rendezvous file.
                    os.close(entry['descriptor'])
                finally:
                    del _HELD[key]


def service_lease():
    """Acquire BEFORE schema initialization and hold through final shutdown."""
    return _lease('service')


def worker_lease():
    """Serialize snapshot dispatch, journal completion and recovery.

    Same-thread nesting permits wrapper -> adapter -> cleanup without opening
    a second handle. Other threads/processes fail immediately, never wait.
    """
    return _lease('worker', reentrant=True)
