from datetime import datetime,timezone
from unittest.mock import Mock
import json
import pytest
from modules.radio.studio import StudioJournal,StudioError,lease
from modules.radio import studio_archive as module

@pytest.fixture
def journal(tmp_path):return StudioJournal(tmp_path/'data',tmp_path/'queue',clock=lambda:1767225600)


def completed(journal,slot):
    row=journal.admit(slot)
    journal.transaction(lambda data:data['jobs'][row['id']].update(status='skipped'))
    journal.clock=lambda:1800000000
    return row


def test_manual_archive_frees_quota_without_re_admission_or_audio_delete(journal):
    old=completed(journal,'2026-01-01T12:00');active=journal.admit('2026-01-01T12:30')
    before=datetime(2026,10,1,tzinfo=timezone.utc);preview=module.archive(journal,before=before)
    assert preview['eligible']==1 and old['id'] in journal.load()[0]['jobs']
    result=module.archive(journal,before=before,expected=preview['version'],apply=True)
    assert not result['audio_deleted'] and old['id'] not in journal.load()[0]['jobs'] and active['id'] in journal.load()[0]['jobs']
    assert journal.admit(old['slot']) is None
    assert module.archive(journal,before=before)['eligible']==0


def test_active_runtime_and_stale_version_refused(journal):
    completed(journal,'2026-01-01T12:00');before=datetime(2026,10,1,tzinfo=timezone.utc)
    with pytest.raises(ValueError):module.archive(journal,before=before,expected='bad',apply=True)
    with lease(journal.root/'runtime.lock'):
        with pytest.raises(ValueError):module.archive(journal,before=before,expected=journal.load()[1],apply=True)


def test_crash_after_tombstone_does_not_reopen_old_slot(journal,monkeypatch):
    row=completed(journal,'2026-01-01T12:00');original=module.atomic_write
    def interrupted(path,raw):
        if path==journal.path:raise OSError('synthetic disk full')
        return original(path,raw)
    monkeypatch.setattr(module,'atomic_write',interrupted)
    with pytest.raises(OSError):module.archive(journal,before=datetime(2026,10,1,tzinfo=timezone.utc),expected=journal.load()[1],apply=True)
    assert journal.admit(row['slot']) is None
    assert row['id'] in journal.load()[0]['jobs']
    monkeypatch.setattr(module,'atomic_write',original)
    module.archive(journal,before=datetime(2026,10,1,tzinfo=timezone.utc),expected=journal.load()[1],apply=True)
    assert row['id'] not in journal.load()[0]['jobs']


def test_corrupt_archive_index_is_preserved_and_fails_closed(journal):
    journal.init();path=journal.root/'archive_index.json';path.write_text('{broken')
    with pytest.raises(StudioError):journal.admit('2026-01-01T12:00')
    assert path.read_text()=='{broken'


def test_completed_publication_still_on_disk_is_preserved_for_recovery(journal):
    row=completed(journal,'2026-01-01T12:00')
    journal.queue.mkdir();(journal.queue/row['publication_name']).write_bytes(b'corrupt fixture must remain inspectable')
    def publication(data):
        data['jobs'][row['id']].update(status='completed',publication={'name':row['publication_name'],'receipt':row['publication_name']+'.ready.json','sha256':'a'*64,'duration':1})
    journal.transaction(publication)
    result=module.archive(journal,before=datetime(2026,10,1,tzinfo=timezone.utc))
    assert result['eligible']==0


def test_referenced_archive_corruption_is_reported(journal):
    completed(journal,'2026-01-01T12:00')
    module.archive(journal,before=datetime(2026,10,1,tzinfo=timezone.utc),expected=journal.load()[1],apply=True)
    file=next((journal.root/'archives').glob('*.json'));file.write_text('{broken')
    with pytest.raises(StudioError):module.verify(journal.root)
