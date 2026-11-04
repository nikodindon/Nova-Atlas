"""Local exclusive LLM resources: stable-inode flock with per-thread ownership.

Linux/local POSIX filesystem. Never unlink/replace the locked inode. Diagnostic
metadata is a separate atomic file and never grants permission to generate.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # No unsafe marker-file fallback on unsupported platforms.
    fcntl = None

LOCAL_EXCLUSIVE_PROVIDERS = frozenset({'ollama', 'llama-server'})
CALLERS = frozenset({'fetch', 'bulletin', 'flash', 'report', 'editions', 'posts', 'atlas'})

# All opens/closes are coordinated with fork. Closing inherited descriptors in the
# child (without LOCK_UN) prevents both parent unlock and inherited-lock leaks.
_registry_guard = threading.RLock()
_open_fds = set()


def _after_fork_child():
    global _registry_guard, _open_fds
    for fd in _open_fds:
        try:
            os.close(fd)
        except OSError:
            pass
    _open_fds = set()
    _registry_guard = threading.RLock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(before=lambda: _registry_guard.acquire(),
                        after_in_parent=lambda: _registry_guard.release(),
                        after_in_child=_after_fork_child)


def safe_caller(caller):
    return caller if isinstance(caller, str) and caller in CALLERS else 'atlas'


def resource_path(directory, resource='local-llm'):
    if not isinstance(resource, str) or not resource.strip() or len(resource) > 128:
        raise ValueError('Invalid local LLM resource')
    directory = Path(directory).resolve()
    if resource == 'local-llm':
        return directory / 'ollama.lock'  # Keep the historical default path.
    identity = hashlib.sha256(resource.encode('utf-8')).hexdigest()[:32]
    return directory / f'llm-{identity}.lock'


class LockUnavailable(RuntimeError):
    """An exclusive section must not execute when admission failed."""


@dataclass(frozen=True)
class _Lease:
    fd: int
    pid: int
    thread_id: int
    token: str
    caller: str


class LocalLLMLock:
    def __init__(self, path, *, clock=None, wait=None):
        path = Path(path)
        self.path = path.parent.resolve() / path.name
        self.metadata_path = self.path.with_name(self.path.name + '.meta')
        self._state = threading.local()
        self._clock = clock or time.monotonic
        self._wait = wait or time.sleep
        self.log = logging.getLogger('nova.llm_lock')

    def _open(self):
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        with _registry_guard:
            fd = os.open(self.path, flags, 0o600)
            _open_fds.add(fd)
        return fd

    @staticmethod
    def _close(fd):
        with _registry_guard:
            try:
                os.close(fd)  # OS also drops the lease if explicit unlock failed.
            except OSError:
                logging.getLogger('nova.llm_lock').error('LLM lock descriptor close failed')
            finally:
                _open_fds.discard(fd)

    def _check_inode(self, fd):
        info = os.fstat(fd)
        path_info = os.stat(self.path, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise OSError('Invalid lock inode')

    def _write_metadata(self, lease, state):
        data = {'version': 1, 'state': state, 'pid': lease.pid,
                'thread_id': lease.thread_id, 'owner_token': lease.token,
                'caller': lease.caller, 'ts': time.time()}
        fd, name = tempfile.mkstemp(prefix='.' + self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.metadata_path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def read_info(self):
        try:
            fd = os.open(self.metadata_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                data = json.loads(stream.read(4096))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def acquire(self, caller='atlas', wait_max=1200, *, cancel_event=None):
        lease = getattr(self._state, 'lease', None)
        if lease is not None and lease.pid != os.getpid():
            self._state.lease = None  # Inherited fd already closed by the fork hook.
            lease = None
        if lease is not None:
            self.log.warning('LLM lock refused: recursive acquisition')
            return False
        if fcntl is None:
            self.log.error('LLM lock unavailable: unsupported platform')
            return False
        try:
            wait_max = float(wait_max)
            if not math.isfinite(wait_max) or wait_max < 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            self.log.error('LLM lock refused: invalid wait budget')
            return False
        deadline = self._clock() + wait_max
        fd, transferred = None, False
        caller = safe_caller(caller)
        try:
            fd = self._open()
            self._check_inode(fd)
            waiting_logged = False
            attempted = False
            while True:
                if attempted and self._clock() >= deadline:
                    self.log.warning('LLM lock wait timeout caller=%s', caller)
                    return False
                if wait_max > 0 and self._clock() > deadline:
                    self.log.warning('LLM lock wait timeout caller=%s', caller)
                    return False
                attempted = True
                if cancel_event is not None and cancel_event.is_set():
                    self.log.info('LLM lock cancelled caller=%s', caller)
                    return False
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EAGAIN, errno.EACCES, errno.EINTR):
                        raise
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self.log.warning('LLM lock wait timeout caller=%s', caller)
                    return False
                if not waiting_logged:
                    self.log.info('LLM lock waiting caller=%s', caller)
                    waiting_logged = True
                delay = min(0.1, remaining)
                if cancel_event is not None:
                    cancel_event.wait(delay)
                else:
                    self._wait(delay)
            self._check_inode(fd)
            if wait_max > 0 and self._clock() > deadline:
                return False
            if cancel_event is not None and cancel_event.is_set():
                return False
            lease = _Lease(fd, os.getpid(), threading.get_ident(), uuid.uuid4().hex, caller)
            self._write_metadata(lease, 'held')
            if wait_max > 0 and self._clock() > deadline:
                self.log.warning('LLM lock acquisition exceeded wait budget caller=%s', caller)
                return False
            self._state.lease = lease
            transferred = True
            self.log.info('LLM lock acquired caller=%s', caller)
            return True
        except (OSError, ValueError):
            self.log.error('LLM lock acquisition failed caller=%s', caller)
            return False
        finally:
            if fd is not None and not transferred:
                self._close(fd)

    def release(self):
        lease = getattr(self._state, 'lease', None)
        if lease is None or lease.pid != os.getpid() or lease.thread_id != threading.get_ident():
            self.log.warning('LLM lock release refused: not owner')
            return False
        self._state.lease = None
        try:
            try:
                self._write_metadata(lease, 'released')
            except (OSError, ValueError):
                self.log.warning('LLM lock metadata update failed on release')
            fcntl.flock(lease.fd, fcntl.LOCK_UN)
            self.log.info('LLM lock released caller=%s', lease.caller)
            return True
        except OSError:
            self.log.error('LLM lock explicit release failed; closing descriptor')
            return False
        finally:
            self._close(lease.fd)

    @contextmanager
    def hold(self, caller='atlas', wait_max=1200, *, cancel_event=None):
        if not self.acquire(caller, wait_max, cancel_event=cancel_event):
            raise LockUnavailable('Local LLM resource unavailable')
        try:
            yield True
        finally:
            self.release()
