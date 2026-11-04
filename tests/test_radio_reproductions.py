"""Written before the Sprint 01F fix; no real TTS, audio tools or streamers."""
from pathlib import Path
from unittest.mock import Mock
import pytest
from modules.radio import bulletin_generator as bg

SCRIPT = '[VOIX1] Bonjour. [VOIX2] Actualité. [VOIX1] Fin.'


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    gen = bg.BulletinGenerator({}, {'root': tmp_path})
    gen.bulletins_cfg.update(min_articles=1, target_words=3, tolerance_words=1, tts_retry_backoff=0)
    def synth(text, voice, path, **kw):
        path.write_bytes(b'synthetic-audio')
        return True
    def concat(paths, output):
        output.write_bytes(b'synthetic-concat')
        return True
    def mix(voice, music, volume, output):
        output.write_bytes(b'synthetic-final')
        return True
    monkeypatch.setattr(bg, 'synthesize_segment', Mock(side_effect=synth))
    monkeypatch.setattr(bg, 'concatenate_segments', Mock(side_effect=concat))
    monkeypatch.setattr(bg, 'mix_voice_with_background', Mock(side_effect=mix))
    # The final implementation will use this injectable validator; no ffmpeg here.
    monkeypatch.setattr(bg, 'inspect_audio', Mock(side_effect=lambda path: bg.AudioInfo(1 if path.name.startswith('seg_') else 3, 'mp3', 24000, 1)), raising=False)
    return gen


@pytest.mark.parametrize('failed', ['Bonjour.', 'Actualité.', 'Fin.'])
def test_one_failed_segment_refuses_entire_bulletin(pipeline, monkeypatch, failed):
    def synth(text, voice, path, **kw):
        if text == failed:
            return False
        path.write_bytes(b'synthetic')
        return True
    monkeypatch.setattr(bg, 'synthesize_segment', synth)
    assert pipeline.build([{}], script=SCRIPT) is None
    assert not list(pipeline.queue_dir.glob('bulletin_*.mp3'))


def test_structurally_invalid_script_never_reaches_tts(pipeline):
    assert pipeline.build([{}], script='[VOIX9] Contenu incorrect.') is None
    bg.synthesize_segment.assert_not_called()


def test_mix_is_private_before_atomic_publication(pipeline, monkeypatch):
    def mix(voice, music, volume, output):
        assert output.parent != pipeline.queue_dir
        assert not list(pipeline.queue_dir.glob('bulletin_*.mp3'))
        output.write_bytes(b'synthetic-final')
        return True
    monkeypatch.setattr(bg, 'mix_voice_with_background', mix)
    assert pipeline.build([{}], script=SCRIPT) is not None


def test_generations_in_same_second_have_unique_names(pipeline, monkeypatch):
    class Clock:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 11, 4, 12)
    monkeypatch.setattr(bg, 'datetime', Clock)
    first = pipeline.build([{}], script=SCRIPT)
    second = pipeline.build([{}], script=SCRIPT)
    assert first is not None and second is not None and first != second
