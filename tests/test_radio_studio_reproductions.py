"""Crash/admission proofs, codecs and transports simulated; no service started."""
from unittest.mock import Mock
from tests.test_radio_integrity import consumers,publish
from modules.core.scheduler import ThreadRunner,DueTask


def test_failed_primary_pass_must_preserve_publication(tmp_path):
    primary,_=consumers(tmp_path);path=publish(tmp_path)
    primary.enqueue_bulletin(path);primary._stream_file=Mock(return_value=False)
    primary._play_next()
    assert path.exists() # Previously removed regardless of transmission result.


def test_slot_admission_survives_process_state_reconstruction(tmp_path):
    from modules.radio.studio import StudioJournal
    journal=StudioJournal(tmp_path/'data',tmp_path/'audio_queue')
    first=journal.admit('2026-11-06T14:30')
    assert first is not None
    restarted=StudioJournal(tmp_path/'data',tmp_path/'audio_queue')
    assert restarted.admit('2026-11-06T14:30') is None


def test_published_before_notification_recoverable(tmp_path):
    from modules.radio.studio import StudioJournal
    journal=StudioJournal(tmp_path/'data',tmp_path/'audio_queue')
    job=journal.admit('2026-11-06T14:30')
    from modules.radio.audio_integrity import publish_audio,AudioInfo
    directory=tmp_path/'audio_queue';directory.mkdir();staged=directory/'staged.mp3';staged.write_bytes(b'valid synthetic bytes')
    path=publish_audio(staged,directory/job['publication_name'],AudioInfo(3),1)
    journal.attach(job['id'],path)
    restarted=StudioJournal(tmp_path/'data',tmp_path/'audio_queue')
    assert restarted.load()[0]['jobs'][job['id']]['status']=='ready'
