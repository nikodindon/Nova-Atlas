"""Validation and atomic publication shared by the radio and Android consumers."""
from dataclasses import dataclass
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

MAX_AUDIO_BYTES = 256 * 1024 * 1024
PUBLIC_NAME = re.compile(r'bulletin_[0-9]{8}_[0-9]{6}_[0-9a-f]{32}\.mp3\Z')


class AudioIntegrityError(ValueError):
    """Only fixed reason codes, never subprocess stderr or private paths."""


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    codec: str = 'mp3'
    sample_rate: int | None = None
    channels: int | None = None


def _regular_audio(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise AudioIntegrityError('missing_or_empty_audio')


def inspect_audio(path, *, timeout=None):
    """Full MP3 decode is mandatory; ffprobe enriches metadata when available."""
    path = Path(path)
    _regular_audio(path)
    deadline=time.monotonic()+timeout if timeout is not None else None
    def budget(default):
        remaining=default if deadline is None else min(default,deadline-time.monotonic())
        if remaining<=0:raise AudioIntegrityError('audio_validation_timeout')
        return remaining
    info = None
    try:
        if shutil.which('ffprobe'):
            probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mp3', '-show_entries',
                'stream=codec_type,codec_name,sample_rate,channels:format=duration',
                '-of', 'json', str(path.resolve())], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=budget(15))
            if probe.returncode:
                raise AudioIntegrityError('probe_failed')
            data = json.loads(probe.stdout)
            streams = data.get('streams', [])
            if len(streams) != 1 or streams[0].get('codec_type') != 'audio' or streams[0].get('codec_name') != 'mp3':
                raise AudioIntegrityError('not_mp3_audio')
            stream = streams[0]
            duration = float(data['format']['duration'])
            rate, channels = int(stream['sample_rate']), int(stream['channels'])
            if not math.isfinite(duration) or duration <= 0 or not 8000 <= rate <= 192000 or channels not in (1, 2):
                raise AudioIntegrityError('invalid_audio_metadata')
            info = AudioInfo(duration, 'mp3', rate, channels)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-err_detect', 'explode',
            '-protocol_whitelist', 'file,pipe', '-f', 'mp3', '-i', str(path.resolve()), '-map', '0:a:0', '-progress', 'pipe:1',
            '-nostats', '-f', 'null', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=budget(120))
        if result.returncode:
            raise AudioIntegrityError('audio_decode_failed')
        # Decode progress also rules out streams with headers but no decoded frames.
        times = re.findall(r'^out_time_us=(\d+)$', result.stdout, re.M)
        duration = int(times[-1]) / 1_000_000 if times else 0
        if duration <= 0:
            raise AudioIntegrityError('no_decoded_audio')
        if info and abs(info.duration - duration) > .25:
            raise AudioIntegrityError('probe_decode_duration_mismatch')
        return info or AudioInfo(duration)
    except AudioIntegrityError:
        raise
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise AudioIntegrityError('audio_validation_failed') from None


def compatible_duration(info, expected, *, segments=1):
    # Account for MP3 frame padding, not large missing pieces of speech.
    if abs(info.duration - expected) > 0.25 + 0.05 * segments:
        raise AudioIntegrityError('audio_duration_mismatch')


def _digest(path):
    _regular_audio(path)
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def receipt_path(path):
    return Path(str(path) + '.ready.json')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_audio(staged, target, info, segment_count):
    # Serialize only publication, including the collision check; never generation.
    with (Path(target).parent / '.publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _publish_audio(staged, target, info, segment_count)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _publish_audio(staged, target, info, segment_count):
    """Receipt first, then atomic audio rename: no visible unvalidated MP3."""
    staged, target = Path(staged), Path(target)
    if not PUBLIC_NAME.fullmatch(target.name) or target.exists() or receipt_path(target).exists():
        raise AudioIntegrityError('publication_name_collision')
    if (not 1 <= segment_count <= 256 or info.codec != 'mp3'
            or not math.isfinite(info.duration) or info.duration <= 0):
        raise AudioIntegrityError('invalid_publication_metadata')
    os.chmod(staged, 0o600)
    digest = _digest(staged)
    receipt = {'version': 1, 'name': target.name, 'sha256': digest,
               'size': staged.stat().st_size, 'duration': info.duration,
               'codec': 'mp3', 'expected_segments': segment_count, 'valid_segments': segment_count}
    receipt_tmp = staged.with_suffix('.ready.json')
    published = False
    try:
        with staged.open('rb') as source:
            os.fsync(source.fileno())
        with receipt_tmp.open('x', encoding='utf-8') as out:
            json.dump(receipt, out, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(receipt_tmp, 0o600)
        os.replace(receipt_tmp, receipt_path(target))
        _sync_dir(target.parent)
        os.replace(staged, target)  # staging MUST be on the destination filesystem.
        published = True
        _sync_dir(target.parent)
        return target
    finally:
        receipt_tmp.unlink(missing_ok=True)
        if not published:
            receipt_path(target).unlink(missing_ok=True)


def validated_publication(path):
    """Cheap consumer gate: receipt + exact bytes, no TTS/FFmpeg at enqueue."""
    try:
        path = Path(path)
        if not PUBLIC_NAME.fullmatch(path.name):
            return False
        _regular_audio(path)
        marker = receipt_path(path)
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
            return False
        data = json.loads(marker.read_text(encoding='utf-8'))
        return (isinstance(data, dict) and data.get('version') == 1
                and data.get('name') == path.name and data.get('codec') == 'mp3'
                and type(data.get('expected_segments')) is int
                and 0 < data['expected_segments'] <= 256
                and type(data.get('valid_segments')) is int
                and data['valid_segments'] == data['expected_segments']
                and isinstance(data.get('duration'), (int, float))
                and math.isfinite(data['duration']) and data['duration'] > 0
                and data.get('size') == path.stat().st_size
                and data.get('sha256') == _digest(path))
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def cache_publication(path, cache_dir):
    """Publish a private Android copy atomically, never fall back to the source."""
    path, cache_dir = Path(path), Path(cache_dir)
    if not validated_publication(path):
        raise AudioIntegrityError('unvalidated_publication')
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / path.name
    if validated_publication(target):
        return target
    data = json.loads(receipt_path(path).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='.copy-', dir=cache_dir) as tmp:
        staged = Path(tmp) / 'audio.mp3'
        shutil.copyfile(path, staged)
        if _digest(staged) != data['sha256']:
            raise AudioIntegrityError('cache_copy_changed')
        publish_audio(staged, target, AudioInfo(data['duration']), data['expected_segments'])
    return target
