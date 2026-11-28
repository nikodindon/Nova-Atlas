"""Expose the last bulletin admitted to local primary playback."""
from pathlib import Path
import time

from modules.fetch.article_state import atomic_json, read_json
from modules.radio.audio_integrity import PUBLIC_NAME, validated_publication


def record_playback(data, queue, name):
    path = Path(queue) / 'android_cache' / name
    if validated_publication(path):
        atomic_json(Path(data) / 'personal-bulletin.json', {'name': name, 'started': int(time.time())})


def most_recent(data):
    marker = Path(data) / 'personal-bulletin.json'
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1024:
        return None
    row = read_json(marker, dict)
    name, started = row.get('name'), row.get('started')
    if isinstance(name, str) and PUBLIC_NAME.fullmatch(name) and type(started) is int:
        return row
    return None
