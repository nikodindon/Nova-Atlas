"""All source HTTP mocked, except the socket-injected client unit tests. No Internet."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import threading
from unittest.mock import Mock
import pytest
from modules.fetch.feed_parser import FeedEntry,FeedResult,parse_feed,source_date
from modules.fetch.ingestion import FeedIngestor,FeedState,options,key
from modules.http import external as http

FIXTURES=Path(__file__).parent/'fixtures/feeds'
URL='https://example.invalid/feed'
NOW=datetime(2026,11,5,12,tzinfo=timezone.utc)


def body(name='rss20.xml'):return (FIXTURES/name).read_bytes()
def response(content=None,status=200,headers=None,url=URL):return http.Response(body() if content is None else content,status,headers or {},url)

@pytest.fixture
def service(tmp_path):
    clock=[NOW]
    obj=FeedIngestor(tmp_path/'state.json',config={'feed_min_poll_seconds':0},clock=lambda:clock[0])
    obj.advance=lambda **kwargs:clock.__setitem__(0,clock[0]+timedelta(**kwargs))
    return obj

@pytest.mark.parametrize('name',['rss091.xml','rss10.xml','rss20.xml','atom03.xml','atom10.xml'])
def test_supported_formats_unicode_and_raw_values(name):
    result=parse_feed(body(name),URL,URL,retrieved_at=NOW)
    assert result.status=='success' and len(result.entries)==1
    entry=result.entries[0]
    assert entry.link.startswith('https://example.invalid/') and entry.title
    assert entry.retrieved_at==NOW and set(entry.legacy())=={'title','link','pub_date','ingestion'}


def test_atom_dates_do_not_conflate_updated_and_published():
    entry=parse_feed(body('atom10.xml'),URL,URL).entries[0]
    assert entry.entry_id=='urn:synthetic:10' and '日本語' in entry.title
    assert '<' not in entry.title and 'alert' not in entry.title
    assert entry.link=='https://example.invalid/base/story?utm_source=synthetic#part'
    assert entry.published_at==datetime(2026,10,25,0,30,tzinfo=timezone.utc)
    assert entry.updated_at==datetime(2026,10,25,1,30,tzinfo=timezone.utc)
    assert '<img' in entry.summary_raw and 'complet' in entry.content_raw

@pytest.mark.parametrize('value,status,utc',[
    ('2026-11-04T02:00:00-08:00','aware','2026-11-04T10:00:00+00:00'),
    ('2024-02-29T23:30:00-02:00','aware','2024-03-01T01:30:00+00:00'),
    ('2026-12-31T23:30:00-02:00','aware','2027-01-01T01:30:00+00:00'),
    ('2026-03-29T02:30:00','ambiguous_local',None),('2026-10-25T02:30:00','ambiguous_local',None),
    ('2026-02-29T12:00:00Z','invalid',None),('jeu. 5 novembre 2026 12:00 CET','invalid',None),
    ('Thu, 05 Nov 2026 12:00:00 GMT','aware','2026-11-05T12:00:00+00:00'),('', 'missing',None)])
def test_dates_keep_raw_ambiguous_and_nonstandard(value,status,utc):
    parsed,actual=source_date(value);assert actual==status
    assert (parsed.isoformat() if parsed else None)==utc

@pytest.mark.parametrize('xml,reason',[
    (b'<rss><channel><item>broken</rss>','invalid_xml'),
    (b'<html><body>not a feed</body></html>','unrecognized_format'),
    (b'<!DOCTYPE rss [<!ENTITY local SYSTEM "file:///synthetic-private">]><rss><channel/></rss>','dtd_forbidden'),
    (b'<rss><channel><item><title>News</title><link>javascript:alert(1)</link></item></channel></rss>','no_usable_entries'),
    (b'', 'empty_or_oversized_body')])
def test_invalid_feeds_are_not_successful_empty(xml,reason):
    result=parse_feed(xml,URL,URL);assert result.status=='invalid_feed' and result.reason==reason


def test_valid_empty_and_guid_without_article_url_are_distinct():
    assert parse_feed(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>',URL,URL).status=='empty'
    no_link=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:only:id</id><title>News</title></entry></feed>'
    assert parse_feed(no_link,URL,URL).status=='invalid_feed'


def test_feedparser_cannot_fetch_a_url_or_external_resource(monkeypatch):
    import feedparser.http
    monkeypatch.setattr(feedparser.http,'get',lambda *a,**kw:pytest.fail('feedparser attempted networking'))
    assert parse_feed(body('atom10.xml'),URL,URL).status=='success'


def test_conditional_headers_304_and_restart_replay(service,monkeypatch):
    transport=Mock(side_effect=[response(headers={'etag':'"first"','last-modified':'Thu, 05 Nov 2026 12:00:00 GMT'}),response(b'',304)])
    monkeypatch.setattr(http,'get',transport)
    first=service.fetch(URL)
    restarted=FeedIngestor(service.state.path,config={'feed_min_poll_seconds':0},clock=lambda:NOW)
    second=restarted.fetch(URL)
    assert second.status=='not_modified' and second.entries==first.entries
    condition=transport.call_args.kwargs['conditional']
    assert condition==http.Conditional(URL,'"first"','Thu, 05 Nov 2026 12:00:00 GMT')
    assert service.state.path.stat().st_mode&0o777==0o600
    stored=json.loads(service.state.path.read_text())
    assert stored['version']==1 and stored['records'][key(URL)]['http_status']==304


def test_corrupt_200_does_not_replace_healthy_etag_or_cached_entries(service,monkeypatch):
    transport=Mock(side_effect=[response(headers={'etag':'"healthy"'}),response(b'<broken',headers={'etag':'"bad"'})])
    monkeypatch.setattr(http,'get',transport);first=service.fetch(URL)
    assert service.fetch(URL).status=='invalid_feed'
    record=service.state.records[key(URL)]
    assert record['etag']=='"healthy"' and record['entries'][0]['entry_id']==first.entries[0].entry_id
    assert record['failures']==1


def test_etag_modified_and_invalid_headers(service,monkeypatch):
    transport=Mock(side_effect=[response(headers={'etag':'"a"'}),response(headers={'etag':'"b"','last-modified':'bad\r\nInjected: header'})])
    monkeypatch.setattr(http,'get',transport);service.fetch(URL);service.fetch(URL)
    record=service.state.records[key(URL)]
    assert record['etag']=='"b"' and record['last_modified'] is None


def test_304_without_snapshot_is_explicit_and_invalidates_validators(service,monkeypatch):
    monkeypatch.setattr(http,'get',Mock(return_value=response(b'',304)))
    result=service.fetch(URL)
    assert result.status=='not_modified' and result.reason=='cache_miss' and not result.entries
    assert 'etag' not in service.state.records[key(URL)]

@pytest.mark.parametrize('status,expected',[(429,'temporarily_unavailable'),(500,'temporarily_unavailable'),(503,'temporarily_unavailable'),(404,'permanently_unavailable'),(410,'permanently_unavailable')])
def test_http_error_classification_and_backoff(service,monkeypatch,status,expected):
    transport=Mock(side_effect=http.HTTPStatusError(status,'7200'))
    monkeypatch.setattr(http,'get',transport)
    assert service.fetch(URL).status==expected
    assert service.fetch(URL).reason=='scheduled_later' and transport.call_count==1
    assert service.state.records[key(URL)]['failures']==1
    if status==429:assert service.state.records[key(URL)]['next_attempt_at']>=NOW.timestamp()+7200

@pytest.mark.parametrize('error,expected',[(http.BlockedURL('non_public_address'),'blocked_by_security'),(http.FetchError('deadline'),'temporarily_unavailable'),(http.FetchError('response_too_large'),'temporarily_unavailable')])
def test_security_timeout_and_size_failures_are_not_empty(service,monkeypatch,error,expected):
    monkeypatch.setattr(http,'get',Mock(side_effect=error))
    assert service.fetch(URL).status==expected


def test_host_retry_after_applies_to_other_feeds_and_survives_restart(service,monkeypatch):
    transport=Mock(side_effect=http.HTTPStatusError(429,'7200'));monkeypatch.setattr(http,'get',transport)
    service.fetch(URL)
    restarted=FeedIngestor(service.state.path,config={'feed_min_poll_seconds':0},clock=lambda:NOW)
    assert restarted.fetch('https://example.invalid/another').reason=='scheduled_later'
    assert transport.call_count==1


def test_source_change_and_explicit_invalidation_are_isolated(service,monkeypatch):
    transport=Mock(return_value=response(headers={'etag':'"a"'}));monkeypatch.setattr(http,'get',transport)
    service.fetch(URL);service.fetch('https://different.invalid/feed')
    assert transport.call_args.kwargs['conditional'] is None
    service.invalidate(URL);service.fetch(URL)
    assert transport.call_args.kwargs['conditional'] is None


def test_old_policy_never_discards_ambiguous_or_missing_dates(monkeypatch):
    content=b'<rss version="2.0"><channel><item><title>Old</title><link>https://example.invalid/old</link><pubDate>2020-01-01T00:00:00Z</pubDate></item><item><title>Unknown</title><link>https://example.invalid/unknown</link><pubDate>not-a-date</pubDate></item><item><title>Naive</title><link>https://example.invalid/naive</link><pubDate>2020-01-01T00:00:00</pubDate></item></channel></rss>'
    monkeypatch.setattr(http,'get',Mock(return_value=response(content)))
    service=FeedIngestor(config={'max_age_days':7},clock=lambda:NOW)
    result=service.fetch(URL)
    assert [e.title for e in result.entries]==['Unknown','Naive']
    assert 'old_entries_deferred:1' in result.warnings
    assert all('seen' not in r for r in service.state.records.values())


def test_freshness_config_change_forces_unconditional_200(service,monkeypatch):
    transport=Mock(return_value=response(headers={'etag':'"a"'}));monkeypatch.setattr(http,'get',transport);service.fetch(URL)
    changed=FeedIngestor(service.state.path,config={'max_age_days':7},clock=lambda:NOW)
    changed.fetch(URL);assert transport.call_args.kwargs['conditional'] is None


def test_corrupted_state_is_preserved_and_only_disables_conditional_cache(service,monkeypatch):
    service.state.path.write_text('{broken')
    broken=FeedIngestor(service.state.path)
    transport=Mock(return_value=response());monkeypatch.setattr(http,'get',transport)
    assert broken.fetch(URL).status=='success'
    assert transport.call_args.kwargs['conditional'] is None
    assert service.state.path.read_text()=='{broken'


def test_concurrency_global_per_host_and_stable_result_order(monkeypatch):
    urls=['https://a.invalid/1','https://a.invalid/2','https://b.invalid/1','https://c.invalid/1','https://d.invalid/1']
    guard=threading.Lock();active=0;max_active=0;hosts={};started=threading.Event();release=threading.Event()
    def transport(url,**kwargs):
        nonlocal active,max_active
        host=http.parse_url(url).host
        with guard:
            active+=1;max_active=max(max_active,active);hosts[host]=hosts.get(host,0)+1;assert hosts[host]<=1
            if active==4:started.set();release.set()
        assert release.wait(3)
        with guard:active-=1;hosts[host]-=1
        return response(url=url)
    monkeypatch.setattr(http,'get',transport)
    service=FeedIngestor(config={'feed_workers':4,'feed_per_host':1,'feed_min_poll_seconds':0})
    result=service.fetch_many(urls)
    assert started.is_set() and max_active==4 and list(result)==urls
    assert all(r.status=='success' for r in result.values())
    assert not any(t.name.startswith('FeedHTTP') for t in threading.enumerate())


def test_cancel_drains_active_workers_and_does_not_start_pending(monkeypatch):
    cancel=threading.Event();calls=[]
    def transport(url,**kwargs):calls.append(url);cancel.set();return response(url=url)
    monkeypatch.setattr(http,'get',transport)
    service=FeedIngestor(config={'feed_workers':1})
    results=service.fetch_many(['https://a.invalid/1','https://b.invalid/1'],cancel_event=cancel)
    assert len(calls)==1 and results['https://b.invalid/1'].reason=='cancelled'
    assert not any(t.name.startswith('FeedHTTP') for t in threading.enumerate())


def test_no_credential_body_or_url_in_logs(service,monkeypatch,caplog):
    monkeypatch.setattr(http,'get',Mock(side_effect=http.FetchError('SYNTHETIC_PRIVATE_TOKEN')))
    service.fetch(URL+'?token=SYNTHETIC_PRIVATE_TOKEN')
    assert 'SYNTHETIC_PRIVATE_TOKEN' not in caplog.text


def test_rss_mtime_cache_is_scoped_to_path_and_preserves_disabled_order(tmp_path):
    import os
    from modules.fetch import atlas_fetch
    one=tmp_path/'one.yaml';two=tmp_path/'two.yaml'
    one.write_text('tech:\n  - https://one.invalid/a\n  # [SPRINT0] https://one.invalid/disabled\n  - https://one.invalid/b\n')
    two.write_text('world:\n  - https://two.invalid/a\n');stamp=one.stat().st_mtime_ns;os.utime(two,ns=(stamp,stamp))
    atlas_fetch.invalidate_feeds_cache()
    assert atlas_fetch._load_rss_sources(one)=={'tech':['https://one.invalid/a','https://one.invalid/b']}
    assert atlas_fetch._load_rss_sources(two)=={'world':['https://two.invalid/a']}
    atlas_fetch.invalidate_feeds_cache()


def test_retry_after_is_not_shortened_by_state_retention(service,monkeypatch):
    monkeypatch.setattr(http,'get',Mock(side_effect=http.HTTPStatusError(429,str(365*86400))))
    service.fetch(URL);service.advance(days=100)
    service.state.dirty=True;service.state.save(service.now().timestamp())
    restarted=FeedIngestor(service.state.path,clock=service.clock)
    assert restarted.fetch(URL).reason=='scheduled_later'


def test_cache_capacity_never_keeps_validator_without_entry_snapshot(service,monkeypatch):
    import modules.fetch.ingestion as module
    monkeypatch.setattr(http,'get',Mock(return_value=response(headers={'etag':'"a"'})))
    monkeypatch.setattr(module,'MAX_STATE',100)
    service.fetch(URL)
    assert service.state.records=={}


def test_empty_feed_cannot_reset_last_known_publication_date(service,monkeypatch):
    monkeypatch.setattr(http,'get',Mock(side_effect=[response(),response(b'<rss version="2.0"><channel/></rss>')]))
    service.fetch(URL);known=service.state.records[key(URL)]['latest_published_at'];service.fetch(URL)
    assert service.state.records[key(URL)]['last_count']==0
    assert service.state.records[key(URL)]['latest_published_at']==known
