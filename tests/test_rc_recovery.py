import fcntl
import json
import os
from pathlib import Path
from unittest.mock import Mock
import pytest
from deploy.init import initialize
from deploy import recovery,preflight

@pytest.fixture
def installation(tmp_path):
    root=tmp_path/'installation';initialize(root,'synthetic-admin','synthetic-password-not-a-real-credential')
    (root/'data/articles/20261108_articles.json').write_text(json.dumps([{'hash':'a'*32,'title':'Fixture','category':'custom'}]))
    (root/'data/article_processing.json').write_text('{"version":1,"records":{}}')
    (root/'audio_queue/fixture.mp3').write_bytes(b'synthetic audio, not for playback')
    (root/'audio_queue/fixture.mp3.ready.json').write_text('{"fixture":true}')
    return root


def test_dry_run_never_opens_secret_values_or_creates_output(installation,tmp_path,monkeypatch):
    original=os.open
    def guarded(path,*args,**kwargs):
        assert 'secrets' not in Path(path).parts
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',guarded)
    out=tmp_path/'snapshot';report=recovery.snapshot(installation,out,include_secrets=True)
    assert report['dry_run'] and not out.exists()
    result=preflight.check(installation,min_free=0)
    assert not any(r['status']=='fail' for r in result['checks'])
    assert next(r for r in result['checks'] if r['check']=='secret_values')['status']=='not_tested'


def test_roundtrip_all_files_and_modes_to_new_directory(installation,tmp_path):
    before=recovery.inventory(installation,hashes=True);snap=tmp_path/'snapshot';restored=tmp_path/'restored'
    recovery.snapshot(installation,snap,include_secrets=True,apply=True,stopped=True)
    assert recovery.manifest(snap)==before
    assert recovery.restore(snap,restored)['dry_run'] and not restored.exists()
    result=recovery.restore(snap,restored,apply=True)
    assert result['deployment_env']=='manual_retarget_required' and not result['services_started']
    assert recovery.inventory(restored,hashes=True)==before
    with pytest.raises(ValueError):recovery.restore(snap,restored,apply=True)
    assert recovery.inventory(installation,hashes=True)==before


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_unsafe_files_refused(installation,tmp_path,kind):
    path=installation/'data/unsafe'
    if kind=='symlink':path.symlink_to(installation/'data/articles/20261108_articles.json')
    elif kind=='hardlink':os.link(installation/'data/articles/20261108_articles.json',path)
    else:os.mkfifo(path)
    with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True)


def test_active_producer_refused_and_lock_inode_preserved(installation,tmp_path):
    path=installation/'data/service-news.lock';path.touch();ino=path.stat().st_ino
    with path.open() as held:
        fcntl.flock(held,fcntl.LOCK_EX)
        with pytest.raises(recovery.RecoveryError,match='producer_active'):recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True,stopped=True,apply=True)
    assert path.stat().st_ino==ino and not (tmp_path/'snapshot').exists()


def test_corruption_and_traversal_manifest_refused(installation,tmp_path):
    snap=tmp_path/'snapshot';recovery.snapshot(installation,snap,include_secrets=True,stopped=True,apply=True)
    (snap/'payload/data/articles/20261108_articles.json').write_text('corrupt')
    with pytest.raises(ValueError):recovery.restore(snap,tmp_path/'restored',apply=True)
    value=json.loads((snap/'manifest.json').read_text());value['entries']['../escape']=value['entries'].pop('compose.env');(snap/'manifest.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):recovery.restore(snap,tmp_path/'restored',apply=True)
    assert not (tmp_path/'restored').exists()


def test_interrupted_copy_preserves_source_and_publishes_nothing(installation,tmp_path,monkeypatch):
    before=recovery.inventory(installation,hashes=True)
    monkeypatch.setattr(recovery.shutil,'copyfileobj',Mock(side_effect=OSError('synthetic ENOSPC')))
    with pytest.raises(OSError):recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True,stopped=True,apply=True)
    assert recovery.inventory(installation,hashes=True)==before and not (tmp_path/'snapshot').exists()
    assert not list(tmp_path.glob('.atlas-snapshot-*'))


def test_secret_permissions_corruption_quota_and_disk_are_reported_without_values(installation,monkeypatch):
    secret=installation/'secrets/session_secret';secret.chmod(0o644)
    (installation/'data/article_processing.json').write_text('{broken')
    monkeypatch.setattr(preflight.shutil,'disk_usage',lambda _:Mock(free=0))
    report=preflight.check(installation,inspect_data=True)
    assert report['status']=='fail' and 'synthetic-password' not in json.dumps(report)
    assert next(r for r in report['checks'] if r['check']=='free_space')['status']=='fail'
    assert (installation/'data/article_processing.json').read_text()=='{broken'


def test_apply_requires_stopped_and_explicit_secret_scope(installation,tmp_path):
    with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'alias'/'..'/'installation'/'new',include_secrets=True)
    with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'snapshot',apply=True)
    with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'snapshot',apply=True,include_secrets=True)


def test_optional_pgdata_requires_explicit_exclusion_and_separate_dump(installation,tmp_path):
    pg=installation/'postgresql';pg.mkdir();(pg/'PG_VERSION').write_text('18')
    with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True)
    recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True,stopped=True,apply=True,exclude_pgdata=True)
    assert not (tmp_path/'snapshot/payload/postgresql').exists()


def test_synthetic_restore_load_exercise_small_contract():
    from deploy.rc_validation import run
    report=run(articles=6,sources=4)
    assert report['restoration'].startswith('pass') and report['articles']==6
    assert report['machine_a']==report['machine_b']==report['24_hour']=='not_tested'


def test_unreadable_subtree_never_becomes_successful_partial_snapshot(installation,tmp_path):
    hidden=installation/'data/unreadable';hidden.mkdir();(hidden/'must_not_be_lost').write_bytes(b'synthetic data')
    hidden.chmod(0)
    try:
        with pytest.raises(ValueError):recovery.snapshot(installation,tmp_path/'snapshot',include_secrets=True)
    finally:hidden.chmod(0o700)
    assert not (tmp_path/'snapshot').exists()
