"""Synthetic YAML and health only, no network or real installation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
import os
import pytest
from modules.core import source_manager as sm
from modules.fetch.ingestion import key
from modules.core.feeds_loader import load_feeds,save_feeds

SAMPLE='''# historical header
tech:
  - https://example.invalid/rss
  # [SPRINT0] https://disabled.invalid/feed
monde:
empty:
'''

@pytest.fixture
def manager(tmp_path):
    path=tmp_path/'feeds.yaml';path.write_text(SAMPLE)
    return sm.SourceManager(path,tmp_path/'health.json',clock=lambda:2000000000)


def test_historical_disabled_empty_order_and_ids(manager):
    doc=manager.read();assert doc.categories==['tech','monde','empty']
    assert [s.enabled for s in doc.sources]==[True,False]
    ids=[s.id for s in doc.sources]
    manager.mutate('move',{'id':ids[1],'direction':'up'},doc.version)
    assert [s.id for s in manager.read().sources]==ids[::-1]
    assert '# historical header' in manager.path.read_text()
    assert 'SPRINT0' in manager.path.read_text()
    assert list(load_feeds(manager.path))==doc.categories


@pytest.mark.parametrize('url',['http://127.0.0.1/x','http://[::1]/','http://10.0.0.2/','http://172.17.0.1/','http://100.64.1.1/','http://169.254.169.254/','file:///etc/passwd','https://u:p@example.invalid/','https://example.invalid/?token=secret','http://localhost/','http://x.local/','https://example.invalid:4433/'])
def test_invalid_urls_every_mutation(manager,url):
    old=manager.path.read_bytes()
    with pytest.raises(sm.SourceError):manager.mutate('add',{'url':url,'category':'tech'})
    with pytest.raises(sm.SourceError):manager.mutate('update',{'id':manager.read().sources[0].id,'url':url})
    assert manager.path.read_bytes()==old


@pytest.mark.parametrize('cat',['','a\nb','a:b','a\x00b','a'*101,'[x]','*alias','a\t','<script>',' a'])
def test_invalid_categories_all_paths(manager,cat):
    with pytest.raises(sm.SourceError):manager.mutate('category',{'category':cat})
    with pytest.raises(sm.SourceError):manager.mutate('add',{'category':cat,'url':'https://public.invalid/'})
    with pytest.raises(sm.SourceError):manager.mutate('move',{'category':cat,'id':manager.read().sources[0].id})


def test_actions_keep_identity_cache_and_health(manager):
    original=manager.listing();sid=original['sources'][0]['id']
    manager.mutate('update',{'id':sid,'name':'Économie 日本語'})
    manager.mutate('disable',{'id':sid});assert manager.listing()['sources'][0]['health']=='disabled'
    manager.mutate('enable',{'id':sid});assert manager.listing()['sources'][0]['health']=='never_checked'
    manager.mutate('move',{'id':sid,'category':'monde'})
    assert next(s for s in manager.read().sources if s.id==sid).category=='monde'
    manager.mutate('rename_category',{'category':'monde','new_category':'monde nouveau'})
    assert next(s for s in manager.read().sources if s.id==sid).category=='monde nouveau'
    manager.mutate('delete',{'id':sid});assert sid not in [s.id for s in manager.read().sources]


def test_same_url_categories_independent_membership(manager):
    manager.mutate('add',{'category':'monde','url':'https://example.invalid/rss'})
    doc=manager.read();assert len({s.id for s in doc.sources})==3
    with pytest.raises(sm.Conflict):manager.mutate('add',{'category':'tech','url':'https://example.invalid/rss'})
    with pytest.raises(sm.SourceError):manager.mutate('move',{'id':doc.sources[0].id,'category':'monde'})


def test_reorder_complete_set_no_duplicates(manager):
    ids=[s.id for s in manager.read().sources]
    for order in [ids[:1],ids+[ids[0]],['bad',ids[1]],'string']:
        with pytest.raises(sm.SourceError):manager.mutate('reorder',{'category':'tech','ids':order})
    manager.mutate('reorder',{'category':'tech','ids':ids[::-1]})
    assert [s.id for s in manager.read().sources]==ids[::-1]


def test_version_conflict_and_legacy_snapshot(manager):
    snapshot=load_feeds(manager.path);version=manager.read().version
    manager.mutate('category',{'category':'new'},version)
    with pytest.raises(sm.Conflict):manager.mutate('category',{'category':'lost'},version)
    with pytest.raises(sm.Conflict):save_feeds(manager.path,snapshot)
    assert 'new' in manager.read().categories and 'lost' not in manager.read().categories


def _process_add(path,cat,queue):
    try:sm.SourceManager(path).mutate('category',{'category':cat});queue.put('ok')
    except Exception as error:queue.put(type(error).__name__)


def test_two_processes_no_lost_update(manager):
    ctx=multiprocessing.get_context('fork');queue=ctx.Queue()
    procs=[ctx.Process(target=_process_add,args=(manager.path,'cat'+str(i),queue)) for i in range(2)]
    # Test subprocess.Process allowed by conftest: real local processes, no external program.
    for p in procs:p.start()
    for p in procs:p.join(5);assert p.exitcode==0
    assert [queue.get(timeout=1) for _ in procs]==['ok','ok']
    assert {'cat0','cat1'}<=set(manager.read().categories)
    assert manager.path.with_name('feeds.yaml.lock').exists()


def test_threads_expected_version_one_wins(manager):
    version=manager.read().version
    def add(i):
        try:manager.mutate('category',{'category':'new'+str(i)},version);return 'ok'
        except sm.Conflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(add,range(2)))==['conflict','ok']


def test_crash_before_replace_preserves_original(manager,monkeypatch):
    old=manager.path.read_bytes();real=sm.os.replace
    def interrupt(src,dst):
        if dst==manager.path:raise OSError('synthetic interrupted write')
        return real(src,dst)
    monkeypatch.setattr(sm.os,'replace',interrupt)
    with pytest.raises(OSError):manager.mutate('category',{'category':'never'})
    assert manager.path.read_bytes()==old
    assert manager.path.with_name('feeds.yaml.bak').read_bytes()==old
    assert not list(manager.path.parent.glob('*.tmp'))


@pytest.mark.parametrize('raw',['tech:\n - https://ok.invalid\nbroken [','tech:\ntech:\n','tech: &anchor\n - https://ok.invalid','tech:\n - https://u:secret@x.invalid/','- https://x.invalid/','tech:\n - https://x.invalid/\n - https://x.invalid/','tech:\n  - |\n    https://x.invalid/'])
def test_corrupt_file_never_overwritten(manager,raw):
    manager.path.write_text(raw)
    with pytest.raises(sm.SourceError):manager.mutate('category',{'category':'new'})
    assert manager.path.read_text()==raw


def test_symlinks_and_special_files_refused(manager,tmp_path):
    other=tmp_path/'other';other.write_text('private');manager.path.unlink();manager.path.symlink_to(other)
    with pytest.raises(sm.SourceError):manager.read()
    assert other.read_text()=='private'


def test_health_accurate_stale_disabled_and_embargo_untouched(manager):
    state={'version':1,'records':{key('https://example.invalid/rss'):{'status':'temporarily_unavailable','last_attempt':1999800000,'last_success':1999700000,'http_status':429,'failures':2,'next_attempt_at':2000100000,'host_until':2000100000,'last_count':4}}}
    manager.health_path.write_text(json.dumps(state));before=manager.health_path.read_bytes()
    row=manager.listing()['sources'][0]
    assert row['health']=='temporarily_unavailable' and row['stale'] and row['observation_age_seconds']==200000 and row['http_status']==429
    manager.mutate('disable',{'id':row['id']});manager.mutate('enable',{'id':row['id']})
    assert manager.health_path.read_bytes()==before


@pytest.mark.parametrize('fmt',['yaml','opml'])
def test_export_preview_roundtrip_idempotent(manager,tmp_path,fmt):
    output=manager.export(fmt);other=sm.SourceManager(tmp_path/'other.yaml');other.path.write_text('')
    incoming,report=other.preview(output,fmt)
    assert report['additions']==2 and report['duplicates']==0
    other.mutate('import',{'document':incoming},report['version'])
    _,again=other.preview(output,fmt);assert again['duplicates']==2 and again['additions']==0
    assert [s.enabled for s in other.read().sources]==[True,False]
    assert other.read().categories==manager.read().categories


@pytest.mark.parametrize('content',['<!DOCTYPE opml [<!ENTITY x SYSTEM "file:///etc/passwd">]><opml><body>&x;</body></opml>','<opml><body><outline xmlUrl="http://127.0.0.1/"/></body></opml>','<opml>','<html/>'])
def test_import_invalid_opml(manager,content):
    before=manager.path.read_bytes()
    with pytest.raises(sm.SourceError):manager.preview(content,'opml')
    assert manager.path.read_bytes()==before


def test_new_cycle_reloads_atomic_replacement_even_identical_mtime(manager):
    from modules.fetch.atlas_fetch import _load_rss_sources,invalidate_feeds_cache
    invalidate_feeds_cache();assert len(_load_rss_sources(manager.path)['tech'])==1
    old=manager.path.stat().st_mtime_ns;src=manager.read().sources[0]
    manager.mutate('disable',{'id':src.id});os.utime(manager.path,ns=(old,old))
    assert _load_rss_sources(manager.path)['tech']==[]


def test_explicit_url_change_retains_management_id_but_new_cache_key(manager):
    src=manager.read().sources[0]
    manager.mutate('update',{'id':src.id,'url':'https://example.invalid/new?tracking=preserved'})
    changed=manager.read().sources[0]
    assert changed.id==src.id and sm.digest(changed.url)!=sm.digest(src.url)


def test_legacy_plain_stale_dict_refused(manager):
    old=manager.path.read_bytes()
    with pytest.raises(sm.Conflict):save_feeds(manager.path,{'tech':['https://new.invalid/rss']})
    assert manager.path.read_bytes()==old


def test_corrupt_health_is_not_presented_as_never_observed(manager):
    manager.health_path.write_text('broken')
    row=manager.listing()['sources'][0]
    assert row['health']=='degraded' and row['error']=='health_state_unavailable'


def test_news_honors_test_429_without_merging_its_journal(tmp_path,monkeypatch):
    from modules.fetch.ingestion import FeedIngestor,key
    from modules.http import external as http
    url='https://public.invalid/rss';now=2000000000
    (tmp_path/'source_test_state.json').write_text(json.dumps({'version':1,'records':{key(url):{'status':'temporarily_unavailable','last_attempt':now,'http_status':429,'host':key('public.invalid'),'host_until':now+900}}}))
    ingestor=FeedIngestor(tmp_path/'feed_ingestion_state.json',clock=lambda:__import__('datetime').datetime.fromtimestamp(now,__import__('datetime').timezone.utc))
    def download(url,**kw):
        with kw['host_budget'].slot('public.invalid',__import__('time').monotonic()+1):raise AssertionError('embargo bypassed')
    monkeypatch.setattr(http,'get',download)
    result=ingestor.fetch(url);assert result.status=='temporarily_unavailable' and result.http_status==429
    assert ingestor.state.records[key(url)]['host_until']>=now+900


@pytest.mark.parametrize('raw',['&anchor tech:\n - https://x.invalid/\n','tech:\n - &anchor https://x.invalid/\n','!str tech:\n - https://x.invalid/\n'])
def test_yaml_anchors_and_tags_refused(manager,raw):
    with pytest.raises(sm.SourceError):manager.preview(raw,'yaml')


@pytest.mark.parametrize('url',['https://x.invalid/?key=synthetic','https://x.invalid/?sig=synthetic','https://x.invalid/?auth=synthetic'])
def test_additional_credential_queries_cannot_export(manager,url):
    with pytest.raises(sm.SourceError):manager.mutate('add',{'url':url,'category':'tech'})
