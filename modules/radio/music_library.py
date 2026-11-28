"""Incremental MP3 tag index shared by the radio writer and Web readers."""
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

MUSIC_TRACK_LIMIT = 100_000

UNKNOWN = 'Non renseigné'
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@contextmanager
def open_track(root, relative):
    candidate = Path(relative)
    parts = candidate.parts
    if not parts or candidate.is_absolute() or '..' in parts:
        raise ValueError('invalid_music_path')
    if candidate.suffix.lower() != '.mp3':
        raise ValueError('invalid_music_extension')
    directory = os.open(root, FILE_FLAGS | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, FILE_FLAGS | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], FILE_FLAGS, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise ValueError('invalid_music_file')
            yield stream, info
    finally:
        os.close(directory)


def genre_tags(stream):
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen import MutagenError

    header = stream.read(10)
    if header.startswith(b'ID3') and (len(header) != 10 or header[6] > 0):
        return [UNKNOWN]  # Bound ID3v2 metadata to less than 2 MiB.
    stream.seek(0)
    try:
        tags = ID3(stream)
    except (ID3NoHeaderError, MutagenError, ValueError, EOFError):
        return [UNKNOWN]
    cleaned = {}
    for tag in tags.getall('TCON'):
        for genre in tag.genres:
            label = ' '.join(str(genre).split())[:80]
            if label and label.isprintable():
                cleaned.setdefault(label.casefold(), label)
            if len(cleaned) >= 12:
                return list(cleaned.values())
    return list(cleaned.values()) or [UNKNOWN]


class MusicLibrary:
    def __init__(self, data, root):
        self.database = Path(data) / 'radio-music.sqlite3'
        self.root = Path(root)

    def connect(self, mode):
        if self.database.is_symlink():
            raise ValueError('unsafe_music_index')
        connection = sqlite3.connect(self.database.absolute().as_uri() + '?mode=' + mode, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def scan(self, stop, reader=genre_tags):
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError('unavailable_music_directory')
        complete = True
        found = 0

        def mark_incomplete(_):
            nonlocal complete
            complete = False

        with closing(self.connect('rwc')) as db:
            self.database.chmod(0o600)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tracks (
                  id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, mtime INTEGER NOT NULL,
                  size INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS genres (
                  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                  key TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY (track_id, key)
                );
                CREATE INDEX IF NOT EXISTS genres_key ON genres(key);
            """)
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('CREATE TEMP TABLE scanned (id TEXT PRIMARY KEY)')
            with db:
                for parent, dirs, files in os.walk(self.root, followlinks=False, onerror=mark_incomplete):
                    dirs.sort()
                    for filename in sorted(files):
                        if stop.is_set():
                            db.rollback()
                            return None
                        if not filename.lower().endswith('.mp3'):
                            continue
                        if found >= MUSIC_TRACK_LIMIT:
                            complete = False
                            break
                        relative = (Path(parent) / filename).relative_to(self.root).as_posix()
                        identity = hashlib.sha256(os.fsencode(relative)).hexdigest()
                        try:
                            with open_track(self.root, relative) as (stream, info):
                                found += 1
                                old = db.execute('SELECT mtime,size FROM tracks WHERE id=?', (identity,)).fetchone()
                                if not old or tuple(old) != (info.st_mtime_ns, info.st_size):
                                    labels = reader(stream)
                                    db.execute('INSERT OR REPLACE INTO tracks VALUES (?,?,?,?)',
                                               (identity, relative, info.st_mtime_ns, info.st_size))
                                    db.executemany('INSERT INTO genres VALUES (?,?,?)',
                                                   [(identity, label.casefold(), label) for label in labels])
                                db.execute('INSERT INTO scanned VALUES (?)', (identity,))
                        except (OSError, ValueError):
                            continue
                    if found >= MUSIC_TRACK_LIMIT:
                        complete = False
                        break
                if complete or found >= MUSIC_TRACK_LIMIT:
                    db.execute('DELETE FROM tracks WHERE id NOT IN (SELECT id FROM scanned)')
                if stop.is_set():
                    db.rollback()
                    return None
            return found

    def genres(self):
        with closing(self.connect('ro')) as db:
            return [row[0] for row in db.execute(
                'SELECT MIN(label) FROM genres GROUP BY key ORDER BY COUNT(*) DESC, key LIMIT 100'
            )]

    def choose(self, genre, previous):
        with closing(self.connect('ro')) as db:
            if genre:
                row = db.execute(
                    'SELECT track_id FROM genres WHERE key=? AND track_id<>? ORDER BY RANDOM() LIMIT 1',
                    (genre.casefold(), previous),
                ).fetchone()
            else:
                row = db.execute('SELECT id FROM tracks WHERE id<>? ORDER BY RANDOM() LIMIT 1', (previous,)).fetchone()
            return row[0] if row else None

    def track(self, identity):
        with closing(self.connect('ro')) as db:
            return db.execute('SELECT path,mtime,size FROM tracks WHERE id=?', (identity,)).fetchone()
