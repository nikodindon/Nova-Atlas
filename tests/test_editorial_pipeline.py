"""End-to-end article attempts, publication/recovery and private trace isolation."""
import json
from unittest.mock import Mock,patch
import pytest
from tests.test_article_integrity import engine,record,ITEM
from modules.fetch import atlas_fetch
from modules.fetch.editorial_trace import save


def enable(engine,**settings):
    config=engine.config
    config['fetch']={'editorial':{'prompt_version':'editorial-v2',**settings}}
    engine._apply_config(config)
    return engine


def test_published_text_does_not_pass_through_tts_markup_cleaner(engine):
    engine._summarize.return_value='Le langage C et le modèle foo_bar conservent **leurs marqueurs**.'
    with patch('modules.utils.helpers.clean_for_tts',side_effect=AssertionError('TTS must not rewrite article')):
        assert engine.run()==1
    assert engine._load_today()[0]['summary']==engine._summarize.return_value


def test_observation_does_not_block_an_ambiguous_article(engine):
    enable(engine)
    engine._fetch_content.return_value='Le budget du conseil est de 120 euros. Les élus confirment cette annonce.'
    engine._summarize.return_value='Le budget du conseil est de 130 euros.'
    assert engine.run()==1
    assert record(engine)['editorial_review']['status']=='needs_review'
    assert record(engine)['editorial_review']['factually_verified'] is False
    assert engine._load_today()[0]['title_original']==ITEM['title']


def test_strict_numeric_disagreement_uses_existing_retry_journal(engine):
    enable(engine,checks='strict')
    engine._fetch_content.return_value='Le budget du conseil est de 120 euros. Les élus confirment cette annonce.'
    engine._summarize.return_value='Le budget du conseil est de 130 euros.'
    assert engine.run()==0
    assert record(engine)['status']=='retryable' and record(engine)['reason']=='editorial_needs_review'
    assert engine._load_today()==[] and not engine._load_seen()
    engine._summarize.return_value='Le budget du conseil est de 120 euros.'
    engine.advance(minutes=30)
    assert engine.run()==1 and record(engine)['status']=='succeeded'
    assert len(engine._load_today())==1


def test_insufficient_contract_recovers_without_publishing_marker(engine,monkeypatch):
    enable(engine)
    monkeypatch.setattr(engine,'_summarize',atlas_fetch.ArticleFetcher._summarize.__get__(engine))
    replies=['{"status":"insufficient_source","summary":""}', '{"status":"ok","summary":"Le conseil annonce une décision."}']
    with patch('modules.fetch.atlas_fetch.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',side_effect=replies):
        assert engine.run()==0
        assert record(engine)['reason']=='summary_source_insufficient' and not engine._load_seen()
        engine.advance(minutes=30)
        assert engine.run()==1
    assert engine._load_today()[0]['summary']=='Le conseil annonce une décision.'


def test_trace_disabled_creates_no_directory(engine):
    assert engine.run()==1
    assert not (engine.seen_file.parent/'editorial_traces').exists()


def test_trace_records_exact_stages_privately_without_changing_api_schema(engine,monkeypatch):
    enable(engine,trace={'enabled':True,'include_source':True,'include_raw_response':True})
    monkeypatch.setattr(engine,'_summarize',atlas_fetch.ArticleFetcher._summarize.__get__(engine))
    raw='{"status":"ok","summary":"Voici un résumé : Le conseil publie un rapport."}'
    with patch('modules.fetch.atlas_fetch.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',return_value=raw):
        assert engine.run()==1
    root=engine.seen_file.parent/'editorial_traces';files=list(root.glob('*.json'))
    assert len(files)==1 and root.stat().st_mode&0o777==0o700 and files[0].stat().st_mode&0o777==0o600
    data=json.loads(files[0].read_text());gen=data['summary_generation']
    assert gen['raw_response']==raw and gen['source_transmitted']==engine._fetch_content.return_value[:3000]
    assert gen['cleaned_summary']=='Le conseil publie un rapport.'
    assert gen['tokens'] is None and gen['finish_reason'] is None and gen['cache_hit'] is None
    assert data['final_article']['summary']==gen['cleaned_summary'] and data['pipeline_status']=='succeeded'
    assert set(engine._load_today()[0])=={'hash','timestamp','category','title','title_original','link','source','pub_date','summary'}
    assert engine._summary_evidence=={} # Do not keep raw evidence on the idle collector.


def test_trace_source_and_raw_response_require_explicit_opt_in(engine):
    enable(engine,trace={'enabled':True})
    assert engine.run()==1
    data=json.loads(next((engine.seen_file.parent/'editorial_traces').glob('*.json')).read_text())
    assert 'extracted_text' not in data and 'source_transmitted' not in data['summary_generation']
    assert 'raw_response' not in data['summary_generation']


def test_trace_failure_never_prevents_publication(engine,monkeypatch):
    enable(engine,trace={'enabled':True})
    monkeypatch.setattr('modules.fetch.editorial_trace.save',Mock(side_effect=OSError('private detail')))
    assert engine.run()==1 and record(engine)['status']=='succeeded'
    assert not any('private detail' in str(x) for x in engine.log.mock_calls)


def test_quota_retention_and_stable_lock(tmp_path):
    cfg={'enabled':True,'max_records':2};clock=[100000.]
    for n in range(3):
        assert save(tmp_path,{'kind':'fixture','value':n},cfg,clock=lambda:clock[0])
        clock[0]+=1
    root=tmp_path/'editorial_traces';inode=(root/'archive.lock').stat().st_ino
    rows=[json.loads(p.read_text()) for p in root.glob('*.json')]
    assert sorted(r['value'] for r in rows)==[1,2]
    clock[0]+=8*86400
    assert save(tmp_path,{'kind':'fixture','value':3},cfg,clock=lambda:clock[0])
    assert len(list(root.glob('*.json')))==1 and (root/'archive.lock').stat().st_ino==inode


def test_corrupt_trace_is_preserved_and_not_overwritten(tmp_path):
    assert save(tmp_path,{'kind':'fixture'},{'enabled':True})
    root=tmp_path/'editorial_traces';p=next(root.glob('*.json'));p.write_text('{broken')
    assert save(tmp_path,{'kind':'other'},{'enabled':True}) is False
    assert p.read_text()=='{broken' and len(list(root.glob('*.json')))==1

@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_unsafe_trace_files_refused_without_following(tmp_path,kind):
    import os
    root=tmp_path/'editorial_traces';root.mkdir(mode=0o700)
    target=tmp_path/'private';target.write_text('preserve')
    path=root/('a'*32+'.json')
    if kind=='symlink':path.symlink_to(target)
    elif kind=='hardlink':os.link(target,path)
    else:os.mkfifo(path)
    assert save(tmp_path,{'kind':'fixture'},{'enabled':True}) is False
    assert target.read_text()=='preserve'


def test_trace_lock_busy_is_nonblocking(tmp_path):
    import fcntl,os
    root=tmp_path/'editorial_traces';root.mkdir(mode=0o700)
    fd=os.open(root/'archive.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert save(tmp_path,{'kind':'fixture'},{'enabled':True}) is False
    finally:os.close(fd)

@pytest.mark.parametrize('payload',[{'raw_response':'<think>private</think>text'},
    {'raw_response':'Bearer synthetic-secret'}, {'raw_response':'token=synthetic-secret'}, {'raw_response':'{"password":"synthetic-secret"}'},
    {'reasoning_content':'synthetic-secret'}])
def test_sensitive_trace_is_refused_without_printing_text(tmp_path,caplog,payload):
    assert save(tmp_path,payload,{'enabled':True}) is False
    assert 'synthetic-secret' not in caplog.text and 'private</think>' not in caplog.text
    assert not (tmp_path/'editorial_traces').exists()
