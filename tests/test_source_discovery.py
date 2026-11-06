"""No external DNS/network; all response bodies are synthetic."""
import json
import threading
import time
from pathlib import Path
import pytest
from modules.core.source_manager import SourceManager,SourceError
from modules.discovery.source_discovery import Discovery,SearXNG,html_candidates,header_candidates,query
from modules.http import external as http
from tests.test_http_security import client_for,FakeResponse,PUBLIC4

RSS=b'<rss version="2.0"><channel><title>Journal technique</title><description>&lt;img src=x onerror=bad&gt; Description</description><language>fr</language><item><title>Actualite</title><link>https://article.invalid/1</link><pubDate>Thu, 05 Nov 2026 12:00:00 +0100</pubDate></item></channel></rss>'
ATOM=b'<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ja"><title>Journal Atom</title><entry><title>Actualite</title><link href="https://article.invalid/2"/></entry></feed>'
HTML=b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head></html>'

@pytest.fixture
def manager(tmp_path):
    (tmp_path/'feeds.yaml').write_text('tech:\n - https://existing.invalid/feed\n # [SPRINT0] https://disabled.invalid/feed\n')
    return SourceManager(tmp_path/'feeds.yaml',tmp_path/'feed_ingestion_state.json')


def transport(mapping,calls):
    def get(url,**kw):
        kw['request_budget'].take(http.parse_url(url).host)
        with kw['host_budget'].slot(http.parse_url(url).host,time.monotonic()+1,kw.get('cancel_event')):
            calls.append(url);value=mapping.get(url,http.HTTPStatusError(404))
            if isinstance(value,Exception):raise value
            if isinstance(value,http.Response):return value
            return http.Response(value,200,{'content-type':'application/rss+xml' if b'<rss' in value or b'<feed' in value else 'text/html'},url)
    return get


def engine(manager,mapping,config=None,search=None,cancel=None):
    calls=[];return Discovery(manager,config=config,transport=transport(mapping,calls),search=search,cancel=cancel),calls


@pytest.mark.parametrize('body,url',[(HTML,'https://site.invalid/feed'),(b'<link rel="alternate" type="application/atom+xml" href="atom.xml">','https://site.invalid/atom.xml'),(b'<a href="/rss.xml">RSS</a>','https://site.invalid/rss.xml'),(b'<a href="/feed/">WordPress</a>','https://site.invalid/feed/')])
def test_declared_explicit_wordpress_and_relative(manager,body,url):
    e,calls=engine(manager,{'https://site.invalid/':body,url:RSS})
    result=e.run('site','https://site.invalid/');row=next(c for c in result['candidates'] if c['url']==url)
    assert row['state']=='success' and row['format']=='rss20' and row['entries']==1
    assert row['language']=='fr' and row['category'] is None and row['description']=='Description'
    assert row['latest_published_at'].endswith('+00:00') and len(row['samples'])==1
    assert len(calls)<=8 and manager.read().sources[0].url=='https://existing.invalid/feed'


def test_header_and_final_redirect_base(manager):
    page=http.Response(b'<html></html>',200,{'content-type':'text/html','link':'</news.xml>; rel="alternate"; type="application/atom+xml"'},'https://other.invalid/new/')
    e,_=engine(manager,{'https://site.invalid/':page,'https://other.invalid/news.xml':ATOM})
    rows=e.run('site','https://site.invalid/')['candidates'];row=next(r for r in rows if r['url']=='https://other.invalid/news.xml')
    assert row['format']=='atom10' and row['method']=='http_link' and row['latest_published_at'] is None


def test_multiple_candidates_exact_dedup_and_hostile_title(manager):
    body=b'<link rel="alternate" type="application/rss+xml" href="/feed" title="&lt;script&gt;bad&lt;/script&gt;"><a href="/feed">RSS</a><link rel="alternate" type="application/atom+xml" href="/atom.xml">'
    e,calls=engine(manager,{'https://site.invalid/':body,'https://site.invalid/feed':RSS,'https://site.invalid/atom.xml':ATOM})
    rows=e.run('site','https://site.invalid/')['candidates']
    assert len([r for r in rows if r['url']=='https://site.invalid/feed'])==1 and calls.count('https://site.invalid/feed')==1
    assert all('<script' not in r['title'] for r in rows)
    assert [r['format'] for r in rows[:2]]==['rss20','atom10']


@pytest.mark.parametrize('body,state',[(b'<rss>','invalid_feed'),(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss version="2.0"><channel/></rss>','invalid_feed'),(b'<html>no feed</html>','invalid_feed'),(b'<rss version="2.0"><channel/></rss>','empty'),(b'<unrecognized/>','invalid_feed')])
def test_candidate_validation_not_claimed_for_html_or_invalid(manager,body,state):
    e,_=engine(manager,{'https://site.invalid/':HTML,'https://site.invalid/feed':body})
    row=next(r for r in e.run('site','https://site.invalid/')['candidates'] if r['url'].endswith('/feed'))
    assert row['state']==state
    assert ('verified' in row['criteria'])==(state=='empty')


@pytest.mark.parametrize('exc,status,code',[(http.HTTPStatusError(404),'permanently_unavailable',404),(http.HTTPStatusError(410),'permanently_unavailable',410),(http.HTTPStatusError(503),'temporarily_unavailable',503),(http.HTTPStatusError(429,'900'),'temporarily_unavailable',429),(http.FetchError('deadline'),'temporarily_unavailable',None),(http.BlockedURL('private'),'blocked_by_security',None)])
def test_errors_remain_explicit(manager,exc,status,code):
    e,_=engine(manager,{'https://site.invalid/':HTML,'https://site.invalid/feed':exc})
    row=next(r for r in e.run('site','https://site.invalid/')['candidates'] if r['url'].endswith('/feed'))
    assert row['state']==status and row['http_status']==code


@pytest.mark.parametrize('url',['http://127.0.0.1/','http://[::1]/','http://100.64.0.1/','http://10.0.0.1/','http://172.17.0.1/','http://169.254.169.254/','file:///etc/passwd','https://u:pass@public.invalid/'])
def test_private_request_and_candidates_refused(manager,url):
    e,calls=engine(manager,{})
    with pytest.raises(SourceError):e.run('site',url)
    assert not calls
    assert html_candidates(('<link rel="alternate" type="application/rss+xml" href="'+url+'">').encode(),'https://site.invalid/')==[]


def test_real_client_rebinding_and_private_redirect_preserved(manager):
    client,calls,resolver=client_for([FakeResponse(body=RSS)])
    resolver.side_effect=[[PUBLIC4],['127.0.0.1']]
    def get(url,**kw):kw.pop('timeout',None);return client.get(url,**kw)
    e=Discovery(manager,transport=get);rows=e.run('site','https://site.invalid/feed')['candidates']
    assert rows[0]['state']=='success' and resolver.call_count==1 and calls[0][1]==PUBLIC4
    client,calls,_=client_for([FakeResponse(status=302,headers={'Location':'http://127.0.0.1/private'})])
    e=Discovery(manager,transport=lambda url,**kw:client.get(url,**{k:v for k,v in kw.items() if k!='timeout'}))
    assert e.run('site','https://second.invalid/')['candidates'][0]['state']=='blocked_by_security' and len(calls)==1


def test_actual_redirect_hops_share_budget():
    client,calls,_=client_for([FakeResponse(status=302,headers={'Location':'https://other.invalid/'}),FakeResponse(status=302,headers={'Location':'https://third.invalid/'}),FakeResponse()])
    with pytest.raises(http.FetchError,match='request_budget'):client.get('https://site.invalid/',request_budget=http.RequestBudget(requests=2))
    assert len(calls)==2


def test_global_deadline_caps_client_and_blocks_expired_budget():
    client,calls,_=client_for([FakeResponse()]);budget=http.RequestBudget();budget.deadline=time.monotonic()-1
    with pytest.raises(http.FetchError):client.get('https://site.invalid/',request_budget=budget)
    assert calls==[]


def test_no_rss_no_crawl_and_limits(manager):
    body=b'<html>'+b'<a href="/page">page</a>'*500+b'</html>'
    e,calls=engine(manager,{'https://site.invalid/':body},config={'requests':3,'feeds':2,'links':10})
    rows=e.run('site','https://site.invalid/')['candidates']
    assert len(calls)<=3 and not any('/page' in url for url in calls)
    assert sum(r['checked_at'] is not None for r in rows)==2 and any(r['state']=='unverified' for r in rows)


def test_cancel_has_no_download_or_worker(manager):
    cancel=threading.Event();cancel.set();e,calls=engine(manager,{},cancel=cancel)
    result=e.run('site','https://site.invalid/')
    assert result['cancelled'] and calls==[]


def test_exact_existing_active_disabled_other_category(manager):
    body=b'<link rel="alternate" type="application/rss+xml" href="https://existing.invalid/feed"><link rel="alternate" type="application/rss+xml" href="https://disabled.invalid/feed">'
    e,_=engine(manager,{'https://site.invalid/':body,'https://existing.invalid/feed':RSS,'https://disabled.invalid/feed':RSS})
    rows=e.run('site','https://site.invalid/')['candidates']
    exact={r['url']:r for r in rows}
    assert exact['https://existing.invalid/feed']['existing'][0]['enabled']
    assert not exact['https://disabled.invalid/feed']['existing'][0]['enabled']


def test_searxng_optional_and_safe_fixed_endpoint(manager):
    with pytest.raises(SourceError):SearXNG('http://192.168.1.1:8080/search')
    with pytest.raises(SourceError):SearXNG('https://search.invalid/search?api_key=secret')
    e,_=engine(manager,{})
    with pytest.raises(SourceError,match='non configuré'):e.run('search','technologie japonaise')
    backend=SearXNG('https://search.invalid/search')
    queryurl='https://search.invalid/search?q=technologie+japonaise&format=json&pageno=1&safesearch=1'
    e,calls=engine(manager,{queryurl:json.dumps({'results':[{'url':'https://site.invalid/'},{'url':'http://127.0.0.1/'}]}).encode(),'https://site.invalid/':HTML,'https://site.invalid/feed':RSS},search=backend)
    result=e.run('search','technologie japonaise');assert any(r['format']=='rss20' for r in result['candidates'])
    assert not any('127.0.0.1' in url for url in calls) and result['requests']<=12


@pytest.mark.parametrize('payload',[b'not json',b'[]',b'{}',b'{"results":42}'])
def test_bad_provider_json_is_not_a_site(manager,payload):
    backend=SearXNG('https://search.invalid/search')
    with pytest.raises(SourceError):backend.sites('theme',lambda url:http.Response(payload,200,{},url),2)


@pytest.mark.parametrize('value',['','x','a'*121,'line\nbreak',{},[]])
def test_theme_boundaries(value):
    with pytest.raises(SourceError):query(value)


def test_malformed_html_attributes_and_excess_links():
    body=b'<link rel type href><a href><link rel="alternate" type="application/rss+xml" href="/feed">'
    assert len(html_candidates(body,'https://site.invalid/'))==1
    assert len(html_candidates(body,'https://site.invalid/',maximum=1))==0


def test_real_http_429_stops_host_and_preserves_retry_after(manager):
    client,calls,_=client_for([FakeResponse(body=HTML,headers={'Content-Type':'text/html'}),FakeResponse(status=429,headers={'Retry-After':'900'})])
    def get(url,**kw):kw.pop('timeout',None);return client.get(url,**kw)
    result=Discovery(manager,transport=get).run('site','https://site.invalid/')
    row=next(r for r in result['candidates'] if r['http_status']==429)
    assert row['state']=='temporarily_unavailable' and len(calls)==2
    assert any(rec.get('host_until',0)>=time.time()+890 for rec in json.loads(manager.health_path.with_name('discovery_feed_state.json').read_text())['records'].values())


def test_absent_metadata_remains_unknown(manager):
    e,_=engine(manager,{'https://site.invalid/feed':b'<rss version="2.0"><channel><item><title>Seul titre</title><link>https://article.invalid/</link></item></channel></rss>'})
    row=e.run('site','https://site.invalid/feed')['candidates'][0]
    assert row['title']=='' and row['language'] is None and row['latest_published_at'] is None and row['category'] is None


def test_http_304_without_local_evidence_is_not_verified(manager):
    e,_=engine(manager,{'https://site.invalid/':http.HTTPStatusError(304)})
    row=e.run('site','https://site.invalid/')['candidates'][0]
    assert row['state']=='not_modified' and row['format'] is None and row['criteria'][0]=='unverified'


def test_body_size_rejected_by_real_transport():
    client,calls,_=client_for([FakeResponse(body=b'x'*100,headers={'Content-Length':'100'})],limits=http.Limits(max_bytes=50))
    with pytest.raises(http.FetchError):client.get('https://site.invalid/')
    assert len(calls)==1


@pytest.mark.parametrize('kind',['timeout','tls_error'])
def test_safe_transport_diagnostics_distinguish_timeout_and_tls(manager,kind):
    e,_=engine(manager,{'https://site.invalid/':http.TransportError(kind)})
    row=e.run('site','https://site.invalid/')['candidates'][0]
    assert row['state']=='temporarily_unavailable' and row['reason']==kind
    assert str(http.TransportError(kind))=='transport_error'
