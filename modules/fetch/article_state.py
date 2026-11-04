"""Crash-safe JSON primitives for the legacy article store (no external services)."""
import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
import tempfile
from contextlib import contextmanager
from datetime import datetime


class ArticleAttemptError(ValueError):
    """Only developer-controlled reason codes may be passed to this exception."""


class StorageError(ValueError):
    """Refuse to replace data whose integrity cannot be established."""


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name, delete=False) as out:
            name = out.name
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.flush()
            if os.fstat(out.fileno()).st_size > 64 * 1024 * 1024:
                raise StorageError('json_store_too_large')
            os.fsync(out.fileno())
        os.replace(name, path)
        name = None
        _sync_directory(path.parent)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backup(path):
    """An immutable, content-addressed backup; never log its contents."""
    path = Path(path)
    if not path.exists():
        return
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()[:16]
    target = path.with_name(path.name + '.pre-01e.' + digest + '.bak')
    if not target.exists():
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.backup-', delete=False) as out:
                name = out.name
                with path.open('rb') as source:
                    shutil.copyfileobj(source, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, target)
            name = None
            _sync_directory(path.parent)
        finally:
            if name:
                Path(name).unlink(missing_ok=True)


def _reject_constant(value):
    raise ValueError('nonfinite_json_number')


def read_json(path, kind):
    path = Path(path)
    if not path.exists():
        return kind()
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('size')
        value = json.loads(path.read_text(encoding='utf-8'), parse_constant=_reject_constant)
        if not isinstance(value, kind):
            raise ValueError('shape')
        if kind is list and any(not isinstance(a, dict) for a in value):
            raise ValueError('article shape')
        return value
    except (ValueError, UnicodeError):
        backup(path)
        raise StorageError('invalid_json_store') from None


def calendar_day(value):
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise ValueError('invalid_day')
    return datetime.strptime(text, '%Y%m%d').date()


def valid_article(article):
    return (isinstance(article, dict)
            and all(isinstance(article.get(k), str) and article[k].strip()
                    for k in ('title', 'link', 'summary'))
            and not article['summary'].lstrip().startswith('[')
            and not article.get('no_content'))


@contextmanager
def collection_lock(path):
    """Stable inode, no unlink: independent collectors share this OS lock."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
