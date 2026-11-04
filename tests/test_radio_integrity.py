"""Offline audio integrity tests: synthetic bytes, mocked codecs, no services."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from queue import Queue
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from modules.radio import audio_integrity as ai, bulletin_generator as bg
from modules.radio.streamer import Streamer, AndroidStreamer
from tests.test_radio_reproductions import pipeline, SCRIPT


@pytest.fixture
def gen(tmp_path, monkeypatch):
    return pipeline.__wrapped__(tmp_path, monkeypatch)


def publish(tmp_path, data=b'synthetic-valid-audio'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    staged = tmp_path / (uuid.uuid4().hex + '.mp3')
    staged.write_bytes(data)
    target = tmp_path / f'bulletin_20261104_120000_{uuid.uuid4().hex}.mp3'
    return ai.publish_audio(staged, target, ai.AudioInfo(3), 3)


def consumers(tmp_path):
    primary = Streamer.__new__(Streamer)
    primary._bulletin_lock = threading.Lock()
    primary._accepted_bulletins = OrderedDict()
    primary._play_queue = Queue()
    primary._fade_requested = threading.Event()
    android = AndroidStreamer.__new__(AndroidStreamer)
    android._bulletin_lock = threading.Lock()
    android._accepted_bulletins = OrderedDict()
    android._config = {'paths': {'audio_queue': str(tmp_path)}}
    android._current = android._pending = None
    android._current_done = threading.Event()
    return primary, android


def test_manifest_complete_and_preserves_order(gen):
    manifest = []
    work = gen.queue_dir / 'test-work'
    work.mkdir()
    paths = bg.synthesize_all_segments(bg.split_script_by_voice(SCRIPT), gen.voice_map, work, manifest=manifest)
    assert [s.index for s in manifest] == [0, 1, 2]
    assert [s.voice_tag for s in manifest] == ['[VOIX1]', '[VOIX2]', '[VOIX1]']
    assert [s.text for s in manifest] == ['Bonjour.', 'Actualité.', 'Fin.']
    assert all(s.state == 'valid' and s.attempts == 1 and s.voice for s in manifest)
    assert paths == [s.path for s in manifest]


def test_retry_only_failed_segment_and_bounded_backoff(gen, monkeypatch):
    calls, waits = [], []
    def synth(text, voice, output, **kw):
        calls.append(text)
        if text == 'Actualité.' and calls.count(text) < 3:
            output.write_bytes(b'partial')
            return False
        assert not output.exists()
        output.write_bytes(b'valid')
        return True
    monkeypatch.setattr(bg, 'synthesize_segment', synth)
    paths = bg.synthesize_all_segments(bg.split_script_by_voice(SCRIPT), gen.voice_map, gen.tmp_dir,
                                     backoff=2, max_backoff=3, wait=waits.append)
    assert calls == ['Bonjour.', 'Actualité.', 'Actualité.', 'Actualité.', 'Fin.']
    assert waits == [2, 3]
    assert len(paths) == 3


@pytest.mark.parametrize('script', ['', ' ', '[VOIX1]', '[VOIX1] [VOIX2] news',
                                   '[VOIX9] texte', '[voix1] texte', '[VOIX1 texte',
                                   '[VOIX1] ...', '[VOIX1] texte [BAD] fin'])
def test_invalid_structure_refused_before_tts(gen, script):
    assert gen.build([{}], script=script) is None
    bg.synthesize_segment.assert_not_called()


@pytest.mark.parametrize('tag,voice,text', [('[VOIX9]', 'fake', 'news'), ('[VOIX1]', '', 'news'),
                                           ('[VOIX1]', None, 'news'), ('[VOIX1]', 'fake', ''),
                                           ('[VOIX1]', 'fake', '...')])
def test_invalid_manifest_never_starts_generation(gen, tag, voice, text):
    with pytest.raises(ai.AudioIntegrityError):
        bg.synthesize_all_segments([(tag, text)], {tag: voice}, gen.tmp_dir)
    bg.synthesize_segment.assert_not_called()


@pytest.mark.parametrize('kind', ['missing', 'empty', 'corrupt'])
def test_success_flag_is_not_proof_of_valid_segment(gen, monkeypatch, kind):
    def synth(text, voice, path, **kw):
        if kind != 'missing':
            path.write_bytes(b'' if kind == 'empty' else b'bad')
        return True
    monkeypatch.setattr(bg, 'synthesize_segment', Mock(side_effect=synth))
    monkeypatch.setattr(bg, 'inspect_audio', Mock(side_effect=ai.AudioIntegrityError('bad_audio')))
    assert gen.build([{}], script=SCRIPT) is None
    assert bg.synthesize_segment.call_count == 3
    assert not list(gen.queue_dir.glob('bulletin_*.mp3'))
    assert not list(gen.queue_dir.glob('.bulletin-*'))


@pytest.mark.parametrize('step', ['concatenate_segments', 'mix_voice_with_background'])
@pytest.mark.parametrize('failure', [False, OSError('synthetic'), subprocess.TimeoutExpired('ffmpeg', 120)])
def test_assembly_failure_preserves_existing_publication(gen, monkeypatch, step, failure):
    prior = publish(gen.queue_dir)
    original = prior.read_bytes()
    monkeypatch.setattr(bg, step, Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=False))
    assert gen.build([{}], script=SCRIPT) is None
    assert prior.read_bytes() == original and ai.validated_publication(prior)
    assert list(gen.queue_dir.glob('bulletin_*.mp3')) == [prior]
    assert not list(gen.queue_dir.glob('.bulletin-*'))


def test_duration_mismatch_refuses_publication(gen, monkeypatch):
    monkeypatch.setattr(bg, 'inspect_audio', lambda p: ai.AudioInfo(1 if p.name.startswith('seg_') else 30))
    assert gen.build([{}], script=SCRIPT) is None
    assert not list(gen.queue_dir.glob('bulletin_*.mp3'))


def test_incompatible_segment_formats_refused(gen, monkeypatch):
    monkeypatch.setattr(bg, 'inspect_audio', lambda p: ai.AudioInfo(1, sample_rate=24000 if '000' in p.name else 48000, channels=1))
    assert gen.build([{}], script=SCRIPT) is None
    bg.concatenate_segments.assert_not_called()


def test_strict_length_rejects_but_warn_preserves_entire_script(gen):
    script = SCRIPT + ' Toutes les informations de fin restent présentes.'
    assert gen.build([{}], script=script) is None
    gen.bulletins_cfg['length_policy'] = 'warn'
    assert gen.build([{}], script=script)
    assert bg.synthesize_segment.call_args_list[-1].args[0].endswith('présentes.')


def test_flash_profile_keeps_short_single_voice_script(gen, monkeypatch):
    monkeypatch.setattr(bg, 'inspect_audio', lambda p: ai.AudioInfo(1))
    assert gen.build([{}], script='[VOIX1] Flash spécialisé complet.', profile='flash')
    bg.synthesize_segment.assert_called_once()


def test_simultaneous_generations_have_independent_files_and_workdirs(gen):
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: gen.build([{}], script=SCRIPT), range(2)))
    assert all(paths) and paths[0] != paths[1]
    assert all(ai.validated_publication(path) for path in paths)
    assert not list(gen.queue_dir.glob('.bulletin-*'))


def test_atomic_visibility_has_receipt_and_complete_bytes(tmp_path, monkeypatch):
    staged = tmp_path / 'staged.mp3'
    staged.write_bytes(b'complete-audio')
    target = tmp_path / f'bulletin_20261104_120000_{uuid.uuid4().hex}.mp3'
    replace = ai.os.replace
    def observe(src, dst):
        if Path(dst) == target:
            assert not target.exists()
            assert ai.receipt_path(target).exists()
            assert Path(src).read_bytes() == b'complete-audio'
        replace(src, dst)
        if Path(dst) == target:
            assert ai.validated_publication(target)
    monkeypatch.setattr(ai.os, 'replace', observe)
    ai.publish_audio(staged, target, ai.AudioInfo(3), 3)
    assert target.read_bytes() == b'complete-audio' and not staged.exists()


def test_interruption_before_publication_cleans_private_work(gen, monkeypatch):
    prior = publish(gen.queue_dir)
    replace = ai.os.replace
    def interrupt(src, dst):
        if Path(dst).suffix == '.mp3':
            raise KeyboardInterrupt()
        replace(src, dst)
    monkeypatch.setattr(ai.os, 'replace', interrupt)
    with pytest.raises(KeyboardInterrupt):
        gen.build([{}], script=SCRIPT)
    assert list(gen.queue_dir.glob('bulletin_*.mp3')) == [prior]
    assert ai.validated_publication(prior)
    assert not list(gen.queue_dir.glob('.bulletin-*'))
    assert len(list(gen.queue_dir.glob('*.ready.json'))) == 1


def test_name_collision_cannot_overwrite_earlier_publication(tmp_path):
    prior = publish(tmp_path)
    staged = tmp_path / 'new.mp3'
    staged.write_bytes(b'new-audio')
    original = prior.read_bytes()
    with pytest.raises(ai.AudioIntegrityError, match='collision'):
        ai.publish_audio(staged, prior, ai.AudioInfo(3), 3)
    assert prior.read_bytes() == original and ai.validated_publication(prior)


@pytest.mark.parametrize('mutation', ['missing_receipt', 'modified_audio', 'missing_audio', 'partial_name', 'wrong_count'])
def test_both_consumers_refuse_unvalidated_audio(tmp_path, mutation):
    path = publish(tmp_path)
    if mutation == 'missing_receipt': ai.receipt_path(path).unlink()
    if mutation == 'modified_audio': path.write_bytes(b'modified')
    if mutation == 'missing_audio': path.unlink()
    if mutation == 'partial_name': path = path.rename(path.with_suffix('.part'))
    if mutation == 'wrong_count':
        data = json.loads(ai.receipt_path(path).read_text())
        data['valid_segments'] -= 1
        ai.receipt_path(path).write_text(json.dumps(data))
    primary, android = consumers(tmp_path)
    assert primary.enqueue_bulletin(path) is False
    assert android.enqueue_bulletin(path) is False
    assert primary._play_queue.empty() and android._current is None


def test_both_consumers_accept_once_and_android_survives_primary_removal(tmp_path):
    primary, android = consumers(tmp_path)
    path = publish(tmp_path)
    assert android.enqueue_bulletin(path) is True
    assert primary.enqueue_bulletin(path) is True
    assert android.enqueue_bulletin(path) is False
    assert primary.enqueue_bulletin(path) is False
    assert primary._play_queue.qsize() == 1
    primary._stream_file = Mock()
    primary._play_next()
    primary._stream_file.assert_called_once_with(path, is_music=False)
    assert not path.exists()
    assert ai.validated_publication(android._current)
    assert android._current.parent.name == 'android_cache'


def test_android_cache_failure_preserves_previous_audio(tmp_path, monkeypatch):
    _, android = consumers(tmp_path)
    first, second = publish(tmp_path), publish(tmp_path)
    assert android.enqueue_bulletin(first)
    old = android._current
    monkeypatch.setattr('modules.radio.streamer.cache_publication', Mock(side_effect=OSError()))
    assert android.enqueue_bulletin(second) is False
    assert android._current == old and android._pending is None


def test_android_restart_ignores_legacy_partial_and_recovers_valid_cache(tmp_path):
    _, android = consumers(tmp_path)
    valid = publish(tmp_path)
    assert android.enqueue_bulletin(valid)
    valid.unlink()
    (tmp_path / 'bulletin_99999999_999999_old.mp3').write_bytes(b'partial')
    recovered = android._load_last_bulletin(android._config)
    assert recovered == android._current and ai.validated_publication(recovered)


def test_modified_file_rejected_again_at_primary_playback(tmp_path):
    primary, _ = consumers(tmp_path)
    path = publish(tmp_path)
    assert primary.enqueue_bulletin(path)
    path.write_bytes(b'changed after enqueue')
    primary._stream_file = Mock()
    primary._play_next()
    primary._stream_file.assert_not_called()


@pytest.mark.parametrize('stage', ['concat', 'mix'])
@pytest.mark.parametrize('kind', ['nonzero', 'timeout', 'empty'])
def test_ffmpeg_helpers_remove_failed_outputs(tmp_path, monkeypatch, stage, kind):
    source = tmp_path / 'source.mp3'
    source.write_bytes(b'voice')
    bg_dir = tmp_path / 'background'
    bg_dir.mkdir()
    (bg_dir / 'music.mp3').write_bytes(b'music')
    output = tmp_path / 'output.mp3'
    def run(*args, **kwargs):
        output.write_bytes(b'' if kind == 'empty' else b'partial')
        if kind == 'timeout': raise subprocess.TimeoutExpired('ffmpeg', 120)
        return SimpleNamespace(returncode=1 if kind == 'nonzero' else 0)
    monkeypatch.setattr(subprocess, 'run', run)
    result = bg.concatenate_segments([source], output) if stage == 'concat' else bg.mix_voice_with_background(source, bg_dir, .3, output)
    assert result is False and not output.exists()
    assert not output.with_suffix('.txt').exists()


def probe_mock(tmp_path, monkeypatch, *, ffprobe=True):
    path = tmp_path / 'audio.mp3'
    path.write_bytes(b'audio')
    monkeypatch.setattr(ai.shutil, 'which', lambda _: 'ffprobe' if ffprobe else None)
    metadata = {'streams': [{'codec_type': 'audio', 'codec_name': 'mp3', 'sample_rate': '24000', 'channels': 1}],
                'format': {'duration': '3.0'}}
    run = Mock(side_effect=[SimpleNamespace(returncode=0, stdout=json.dumps(metadata)),
                           SimpleNamespace(returncode=0, stdout='out_time_us=3000000\n')] if ffprobe else
                           [SimpleNamespace(returncode=0, stdout='out_time_us=3000000\n')])
    monkeypatch.setattr(subprocess, 'run', run)
    return path, run


@pytest.mark.parametrize('ffprobe', [True, False])
def test_audio_decode_with_optional_ffprobe(tmp_path, monkeypatch, ffprobe):
    path, run = probe_mock(tmp_path, monkeypatch, ffprobe=ffprobe)
    info = ai.inspect_audio(path)
    assert info.duration == 3 and info.codec == 'mp3'
    assert any('-xerror' in call.args[0] for call in run.call_args_list)


@pytest.mark.parametrize('kind', ['probe_failure', 'bad_json', 'no_audio', 'wrong_codec', 'decode_failure', 'timeout', 'no_frames'])
def test_audio_probe_and_decoder_fail_closed(tmp_path, monkeypatch, kind):
    path, run = probe_mock(tmp_path, monkeypatch)
    probe = SimpleNamespace(returncode=0, stdout=json.dumps({'streams': [{'codec_type': 'audio', 'codec_name': 'mp3',
                    'sample_rate': '24000', 'channels': 1}], 'format': {'duration': '3'}}))
    decode = SimpleNamespace(returncode=0, stdout='out_time_us=3000000\n')
    if kind == 'probe_failure': probe.returncode = 1
    if kind == 'bad_json': probe.stdout = '{bad'
    if kind == 'no_audio': probe.stdout = '{"streams":[]}'
    if kind == 'wrong_codec': probe.stdout = probe.stdout.replace('mp3', 'aac')
    if kind == 'decode_failure': decode.returncode = 1
    if kind == 'no_frames': decode.stdout = ''
    run.side_effect = [probe, subprocess.TimeoutExpired('ffmpeg', 120) if kind == 'timeout' else decode]
    with pytest.raises(ai.AudioIntegrityError): ai.inspect_audio(path)


def test_tts_timeout_cancels_async_save(tmp_path, monkeypatch):
    cancelled = []
    class Communicate:
        def __init__(self, text, voice): pass
        async def save(self, path):
            try: await asyncio.sleep(10)
            finally: cancelled.append(True)
    monkeypatch.setattr('edge_tts.Communicate', Communicate)
    assert bg.synthesize_segment('Synthetic text', 'fake', tmp_path / 'out.mp3', timeout=.001) is False
    assert cancelled == [True]


@pytest.mark.parametrize('kind', ['empty', 'invalid'])
def test_final_output_is_validated_before_publication(gen, monkeypatch, kind):
    original_inspect = bg.inspect_audio
    def inspect(path):
        if path.name == 'final.mp3':
            if kind == 'empty': path.write_bytes(b'')
            raise ai.AudioIntegrityError('invalid_final_audio')
        return original_inspect(path)
    monkeypatch.setattr(bg, 'inspect_audio', inspect)
    assert gen.build([{}], script=SCRIPT) is None
    assert not list(gen.queue_dir.glob('bulletin_*.mp3'))


def test_mismatched_segment_count_refuses_assembly(gen, monkeypatch):
    monkeypatch.setattr(bg, 'synthesize_all_segments', lambda *a, **kw: [gen.tmp_dir / 'only-one.mp3'])
    assert gen.build([{}], script=SCRIPT) is None
    bg.concatenate_segments.assert_not_called()


def test_abrupt_mix_interruption_preserves_public_files(gen, monkeypatch):
    prior = publish(gen.queue_dir)
    def interrupt(voice, music, volume, output):
        output.write_bytes(b'partial')
        raise KeyboardInterrupt()
    monkeypatch.setattr(bg, 'mix_voice_with_background', interrupt)
    with pytest.raises(KeyboardInterrupt): gen.build([{}], script=SCRIPT)
    assert ai.validated_publication(prior)
    assert list(gen.queue_dir.glob('bulletin_*.mp3')) == [prior]
    assert not list(gen.queue_dir.glob('.bulletin-*'))


def test_concurrent_same_name_publication_has_one_owner(tmp_path):
    target = tmp_path / f'bulletin_20261104_120000_{uuid.uuid4().hex}.mp3'
    inputs = []
    for i in range(2):
        work = tmp_path / str(i)
        work.mkdir()
        staged = work / 'audio.mp3'
        staged.write_bytes(f'complete-{i}'.encode())
        inputs.append(staged)
    def attempt(path):
        try: return ai.publish_audio(path, target, ai.AudioInfo(1), 1)
        except ai.AudioIntegrityError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, inputs))
    assert sum(p is not None for p in results) == 1
    assert ai.validated_publication(target)


def test_probe_duration_cannot_hide_missing_decoded_audio(tmp_path, monkeypatch):
    path, run = probe_mock(tmp_path, monkeypatch)
    good_probe = run.side_effect
    run.side_effect = [next(good_probe), SimpleNamespace(returncode=0, stdout='out_time_us=100000\n')]
    with pytest.raises(ai.AudioIntegrityError, match='duration'):
        ai.inspect_audio(path)
