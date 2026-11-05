"""Typed ingestion through the real legacy retry/publication path, mocks for extraction/LLM."""
from datetime import datetime,timedelta
import json
import threading
from unittest.mock import Mock
from modules.fetch import atlas_fetch
from modules.http import external as http
from tests.test_article_reproductions import fetcher_at
from modules.fetch.ingestion import key


def xml(links):
    return ('<rss version="2.0"><channel>'+''.join(f'<item><guid isPermaLink="false">urn:stable</guid><title>Une information {index}</title><link>{link}</link><pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate></item>' for index,link in enumerate(links))+'</channel></rss>').encode()


def test_same_url_across_categories_uses_yaml_priority_not_download_completion(tmp_path,monkeypatch):
    engine=fetcher_at(tmp_path,monkeypatch)
    monkeypatch.setattr(atlas_fetch,'_load_rss_sources',lambda:{'tech':['https://first.invalid/rss'],'world':['https://second.invalid/rss']})
    transport=Mock(side_effect=lambda url,**kw:http.Response(xml(['https://news.example/article']),200,{},url))
    monkeypatch.setattr(http,'get',transport)
    assert engine.run()==1
    assert engine._load_today()[0]['category']=='tech'
    assert engine._summarize.call_count==1 and transport.call_count==2
    record=engine._state['records'][engine._hash('https://news.example/article')]
    assert record['ingestion']['source_key']==key('https://first.invalid/rss')


def test_304_replays_cache_after_crash_before_discovery(tmp_path,monkeypatch):
    source='https://first.invalid/rss';engine=fetcher_at(tmp_path,monkeypatch)
    monkeypatch.setattr(atlas_fetch,'_load_rss_sources',lambda:{'tech':[source]})
    transport=Mock(side_effect=[http.Response(xml(['https://news.example/article']),200,{'etag':'"a"'},source),http.Response(b'',304,{},source)])
    monkeypatch.setattr(http,'get',transport)
    original=engine._record;engine._record=Mock(side_effect=KeyboardInterrupt())
    try:engine.run()
    except KeyboardInterrupt:pass
    assert engine._summarize.call_count==0
    restarted=fetcher_at(tmp_path,monkeypatch,datetime(2026,11,4,12,2))
    assert restarted.run()==1 and restarted._summarize.call_count==1
    assert transport.call_args.kwargs['conditional'].etag=='"a"'
    assert len(restarted._load_today())==1


def test_304_and_category_cap_preserve_pending_and_do_not_generate_twice(tmp_path,monkeypatch):
    source='https://first.invalid/rss';engine=fetcher_at(tmp_path,monkeypatch);engine.max_per_category=1
    monkeypatch.setattr(atlas_fetch,'_load_rss_sources',lambda:{'tech':[source]})
    monkeypatch.setattr(http,'get',Mock(side_effect=[http.Response(xml(['https://news.example/a','https://news.example/b']),200,{'etag':'"a"'},source),http.Response(b'',304,{},source)]))
    assert engine.run()==1
    engine._clock=lambda:datetime(2026,11,4,12,2)
    assert engine.run()==1 and len(engine._load_today())==2
    assert engine._summarize.call_count==2
    assert all(set(article)=={'hash','timestamp','category','title','title_original','link','source','pub_date','summary'} for article in engine._load_today())


def test_guid_stable_new_urls_remain_distinct(tmp_path,monkeypatch):
    engine=fetcher_at(tmp_path,monkeypatch)
    monkeypatch.setattr(atlas_fetch,'_load_rss_sources',lambda:{'tech':['https://first.invalid/rss']})
    monkeypatch.setattr(http,'get',Mock(return_value=http.Response(xml(['https://news.example/a?tracking=1','https://news.example/a?tracking=2']),200,{},'https://first.invalid/rss')))
    assert engine.run()==2 and engine._summarize.call_count==2
    assert len({a['hash'] for a in engine._load_today()})==2


def test_cancel_after_extraction_does_not_start_llm_or_consume_retry_budget(tmp_path,monkeypatch):
    engine=fetcher_at(tmp_path,monkeypatch);cancel=threading.Event()
    monkeypatch.setattr(atlas_fetch,'_load_rss_sources',lambda:{'tech':['https://first.invalid/rss']})
    monkeypatch.setattr(http,'get',Mock(return_value=http.Response(xml(['https://news.example/a']),200,{},'https://first.invalid/rss')))
    engine._fetch_content=Mock(side_effect=lambda *a,**kw:(cancel.set() or 'Synthetic article'))
    assert engine.run(stop_event=cancel)==0
    engine._summarize.assert_not_called()
    record=next(iter(engine._state['records'].values()))
    assert record['status']=='retryable' and record['attempts']==0
