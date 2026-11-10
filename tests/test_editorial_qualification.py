"""Synthetic evidence only; real state and network must never be needed."""
import json
import os
from pathlib import Path
import pytest
from deploy import editorial_qualification as q

@pytest.fixture
def installation(tmp_path):
    state=tmp_path/'state';(state/'data/articles').mkdir(parents=True);(state/'config').mkdir()
    (state/'config/config.yaml').write_text('llm:\n  provider: llama-server\n  model: /private/model.gguf\n  api_key: SECRET-CONFIG\n')
    repo=tmp_path/'repo';(repo/'modules/fetch').mkdir(parents=True);(repo/'.git').mkdir();(repo/'.git/HEAD').write_text('ref: refs/heads/test')
    (repo/'modules/fetch/atlas_fetch.py').write_text('def _summarize():\n    return "candidate prompt"\n')
    rows=[{'title':'Titre '+str(i),'category':'cat'+str(i%4),'source':'source', 'summary':'Résumé réel Unicode 日本語 '+str(i),
           'link':'https://example.org/'+str(i),'hash':str(i),'timestamp':'2026-11-10T09:00:00','pub_date':'raw'} for i in range(30)]
    path=state/'data/articles/20261110_articles.json';path.write_text(json.dumps(rows))
    return state,repo,path


def test_private_export_balanced_exact_and_never_touches_source(installation,tmp_path):
    state,repo,path=installation;before=path.read_bytes();out=tmp_path/'private-report'
    result=q.export(state,repo,out)
    assert result=={'status':'prepared_private','examples':20,'categories':4,'scripts_recovered':0,'uploaded':False}
    report=json.loads((out/'private.json').read_text())
    assert len(report['examples'])==20
    assert all(e['raw_llm_output'] is None and e['generation_metrics'] is None for e in report['examples'])
    assert report['observed_model']['model_basename']=='model.gguf'
    assert 'SECRET-CONFIG' not in (out/'private.json').read_text()
    assert path.read_bytes()==before
    assert all(p.stat().st_mode&0o777==0o600 for p in out.iterdir())
    assert out.stat().st_mode&0o777==0o700
    assert 'Résumé' not in (out/'share-draft.md').read_text()
    assert 'candidate prompt' not in (out/'private.json').read_text()


def test_export_excludes_full_source_content_and_unknown_keys(installation,tmp_path):
    state,repo,path=installation;rows=json.loads(path.read_text())
    for row in rows:row.update(content='FULL SOURCE SECRET',reasoning_content='INTERNAL REASONING')
    path.write_text(json.dumps(rows));q.export(state,repo,tmp_path/'report')
    raw=(tmp_path/'report/private.json').read_text()
    assert 'FULL SOURCE SECRET' not in raw and 'INTERNAL REASONING' not in raw

@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_unsafe_input_refused(installation,tmp_path,kind):
    state,repo,path=installation;other=tmp_path/'original';path.rename(other)
    if kind=='symlink':path.symlink_to(other)
    elif kind=='hardlink':os.link(other,path)
    else:os.mkfifo(path)
    with pytest.raises(ValueError):q.export(state,repo,tmp_path/'report')


def test_corruption_is_not_silently_omitted(installation,tmp_path):
    state,repo,path=installation;path.write_text('{broken')
    with pytest.raises(q.ExportError):q.export(state,repo,tmp_path/'report')
    assert path.read_text()=='{broken'

@pytest.mark.parametrize('place',['repo','state','existing'])
def test_output_cannot_write_state_git_or_existing(installation,tmp_path,place):
    state,repo,_=installation
    out={'repo':repo/'report','state':state/'report','existing':tmp_path/'existing'}[place]
    if place=='existing':out.mkdir()
    with pytest.raises(ValueError):q.export(state,repo,out)


def test_no_attributed_script_is_invented_from_audio_or_cache(installation,tmp_path):
    state,repo,_=installation;(state/'audio_queue').mkdir();(state/'audio_queue/a.mp3').write_bytes(b'not audio')
    cache=state/'data/llm_cache';cache.mkdir();(cache/'fake.txt').write_text('[VOIX1] Possible cache output')
    q.export(state,repo,tmp_path/'report');radio=json.loads((tmp_path/'report/private.json').read_text())['radio']
    assert radio['scripts_recovered']==0 and radio['voice_tagged_cache_candidates']==1
    assert radio['audio_transcription_performed'] is False


def test_no_share_without_review_and_hash_match(installation,tmp_path):
    state,repo,_=installation;q.export(state,repo,tmp_path/'report')
    with pytest.raises(q.ExportError,match='no_reviewed'):q.share(tmp_path/'report/private.json',tmp_path/'report/review.json',tmp_path/'share')
    review=json.loads((tmp_path/'report/review.json').read_text());review['private_sha256']='wrong'
    (tmp_path/'review.json').write_text(json.dumps(review))
    with pytest.raises(q.ExportError,match='version_conflict'):q.share(tmp_path/'report/private.json',tmp_path/'review.json',tmp_path/'share')


def test_reviewed_share_is_allowlisted_and_inert(installation,tmp_path):
    state,repo,path=installation;rows=json.loads(path.read_text())
    for row in rows:
        row['summary']='<script>alert(1)</script> ![tracking](https://evil.example/x?token=PRIVATE) person@example.org'
        row['link']+='?token=PRIVATE#fragment'
    path.write_text(json.dumps(rows));out=tmp_path/'report';q.export(state,repo,out)
    review=json.loads((out/'review.json').read_text());identity=next(iter(review['approvals']))
    review['approvals'][identity]={'privacy_reviewed':True,'output_rights_reviewed':True,'review_note':'Synthetic text, test author permission'}
    review_path=tmp_path/'review.json';review_path.write_text(json.dumps(review));q.share(out/'private.json',review_path,tmp_path/'share')
    report=json.loads((tmp_path/'share/share.json').read_text());assert len(report['examples'])==1
    raw=(tmp_path/'share/share.md').read_text()
    assert '?token=' not in raw and 'person@example.org' not in raw
    assert '\n    output: <script>' in raw # inert Markdown code block, no trusted HTML
    assert report['publication_authorized'] is False

@pytest.mark.parametrize('url',['file:///secret','https://user:password@example.org','javascript:alert(1)','https://x\\y/a','https://x/a\n'])
def test_sensitive_or_unsafe_links_refused(url):
    assert q.safe_url(url) is None


def test_repeated_output_refused(installation,tmp_path):
    state,repo,_=installation;q.export(state,repo,tmp_path/'report')
    with pytest.raises(q.ExportError):q.export(state,repo,tmp_path/'report')


def test_summary_redacts_reasoning_and_known_tokens():
    assert q.redact('before <think>hidden</think> after')=='before [REASONING OMITTED] after'
    assert 'hidden' not in q.redact('<think>hidden')
    assert 'PRIVATE' not in q.redact('token=PRIVATE')


def test_alternative_thought_channel_and_credentials_are_not_exported():
    assert 'private' not in q.redact('<|channel>thought private<channel|>final')
    assert q.redact('https://user:password@example.org/a') == '[URL OMITTED]'


def test_failed_publication_leaves_source_and_existing_report_intact(installation,tmp_path,monkeypatch):
    state,repo,path=installation;before=path.read_bytes();out=tmp_path/'report'
    def unavailable(stage,target):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(q,'publish',unavailable)
    with pytest.raises(OSError):q.export(state,repo,out)
    assert not out.exists() and path.read_bytes()==before
    assert not list(tmp_path.glob('.editorial-*'))


def test_atomic_publish_cannot_replace_a_concurrent_report(installation,tmp_path,monkeypatch):
    state,repo,_=installation;out=tmp_path/'report';original=q.publish
    def raced(stage,target):
        target.mkdir();(target/'operator-file').write_text('preserve')
        original(stage,target)
    monkeypatch.setattr(q,'publish',raced)
    with pytest.raises(ValueError):q.export(state,repo,out)
    assert (out/'operator-file').read_text()=='preserve'

@pytest.mark.parametrize('count',[14,21,True])
def test_sample_bounds(installation,tmp_path,count):
    state,repo,_=installation
    with pytest.raises(q.ExportError):q.export(state,repo,tmp_path/'report',count)


def test_changed_input_is_not_exported_as_a_consistent_snapshot(tmp_path,monkeypatch):
    from types import SimpleNamespace
    p=tmp_path/'input';p.write_text('source')
    original=q.os.fstat;counter=[0]
    def changing(fd):
        info=original(fd);counter[0]+=1
        return SimpleNamespace(st_mode=info.st_mode,st_nlink=info.st_nlink,
            st_size=info.st_size,st_ino=info.st_ino,st_mtime_ns=info.st_mtime_ns+(counter[0]>1))
    monkeypatch.setattr(q.os,'fstat',changing)
    with pytest.raises(q.ExportError,match='changed'):q.read(p)
