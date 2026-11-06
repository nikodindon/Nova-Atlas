"""No crawler, browser, LLM or subscription side effects. Reuse secure HTTP/ingestion."""
from dataclasses import dataclass,asdict,replace
from datetime import datetime,timezone
from html.parser import HTMLParser
import json
import re
import threading
import time
from typing import Protocol
import unicodedata
from urllib.parse import urljoin,urlsplit,urlunsplit,urlencode
from bs4 import BeautifulSoup
from modules.core.source_manager import source_url,SourceError,digest
from modules.http import external as http
from modules.fetch.feed_parser import parse_feed,FeedResult
from modules.fetch.ingestion import FeedIngestor,key
from modules.core.llm_gateway import retry_after

CONVENTIONAL=('/feed','/rss','/rss.xml','/atom.xml','/feed.xml')
TYPES={'application/rss+xml','application/atom+xml','application/rdf+xml','text/xml','application/xml'}


def display_url(value):
    if not value:return None
    parts=urlsplit(value);return urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))


def plain(value,limit=300):
    soup=BeautifulSoup(str(value)[:4000],'html.parser')
    for node in soup(['script','style']):node.decompose()
    text=' '.join(soup.get_text(' ',strip=True).split())
    return ''.join(c for c in text if not unicodedata.category(c).startswith('C'))[:limit]


def query(value):
    if not isinstance(value,str) or not 2<=len(value.strip())<=120 or any(unicodedata.category(c).startswith('C') for c in value):raise SourceError('Thème invalide (2–120 caractères)')
    return value.strip()


@dataclass(frozen=True)
class Candidate:
    url:str
    site_url:str
    method:str
    title:str=''
    description:str=''
    format:str|None=None
    language:str|None=None
    entries:int|None=None
    latest_published_at:str|None=None
    category:str|None=None
    state:str='unverified'
    warnings:tuple=()
    checked_at:str|None=None
    existing:tuple=()
    samples:tuple=()
    final_url:str|None=None
    criteria:tuple=()
    http_status:int|None=None
    reason:str=''

    @property
    def id(self):return digest(self.url)
    def public(self):
        value=asdict(self);value['site_url']=display_url(self.site_url);value['final_url']=display_url(self.final_url)
        return {'id':self.id,**value}


def candidate(value,base,method,title=''):
    try:
        absolute=value if urlsplit(value).scheme else urljoin(base,value)
        source_url(absolute)
        return Candidate(absolute,base,method,plain(title))
    except (ValueError,TypeError):return None


class Links(HTMLParser):
    def __init__(self,base,maximum):
        super().__init__(convert_charrefs=True);self.base=base;self.maximum=maximum;self.examined=0;self.results=[]
    def handle_starttag(self,tag,attrs):
        if tag not in ('a','link') or self.examined>=self.maximum:return
        self.examined+=1;attrs=dict(attrs);href=attrs.get('href') or ''
        rel=(attrs.get('rel') or '').lower().split();mime=(attrs.get('type') or '').split(';')[0].lower()
        method='html_alternate' if tag=='link' and 'alternate' in rel and mime in TYPES else 'html_link'
        if method=='html_link' and not (mime in TYPES or re.search(r'(?:rss|atom|feed)(?:\.xml)?(?:[/?#]|$)',href,re.I)):return
        result=candidate(href,self.base,method,attrs.get('title',''))
        if result:self.results.append(result)


def html_candidates(body,base,maximum=100,encoding='utf-8'):
    if not isinstance(body,bytes) or len(body)>1024*1024:raise SourceError('HTML trop volumineux')
    parser=Links(base,maximum)
    try:parser.feed(body.decode(encoding,errors='replace'));parser.close()
    except (LookupError,ValueError,RecursionError):raise SourceError('HTML invalide') from None
    return parser.results


def header_candidates(value,base):
    results=[]
    # Only the bounded Link header, not arbitrary response headers/URLs, is interpreted.
    for part in re.split(r',(?=\s*<)',value[:8192])[:20]:
        match=re.match(r'\s*<([^>]+)>\s*(.*)',part)
        if not match:continue
        params={k.lower():v.strip('"\'') for k,v in re.findall(r';\s*([\w-]+)\s*=\s*("[^"]*"|[^;\s]+)',match[2])}
        if 'alternate' not in params.get('rel','').split() or params.get('type','').lower() not in TYPES:continue
        result=candidate(match[1],base,'http_link',params.get('title',''))
        if result:results.append(result)
    return results


def ranking(row):
    """Same deterministic ordering for fresh and cached, rejoined memberships."""
    criteria=row['criteria']
    return (criteria[0]!='verified',criteria[1]!='declared',criteria[2]!='theme_match',
        -(datetime.fromisoformat(row['latest_published_at']).timestamp() if row['latest_published_at'] else 0),
        bool(row['existing']),row['url'])


class SearchBackend(Protocol):
    def sites(self,theme,getter,maximum):...


class RSSHubBackend(Protocol):
    """Future configured instance + reviewed routes, never a URL-to-LAN proxy."""
    def candidates(self,site):...


class SearXNG:
    def __init__(self,endpoint):
        source_url(endpoint);parts=urlsplit(endpoint)
        if parts.scheme!='https' or parts.query or parts.fragment or parts.path.rstrip('/')!='/search':raise SourceError('SearXNG exige un endpoint HTTPS public /search sans paramètres')
        self.endpoint=endpoint
    def sites(self,theme,getter,maximum):
        theme=query(theme)
        url=self.endpoint+'?'+urlencode({'q':theme,'format':'json','pageno':1,'safesearch':1})
        response=getter(url)
        if response.status_code!=200:raise SourceError('SearXNG indisponible')
        try:
            data=json.loads(response.content)
            if not isinstance(data,dict) or not isinstance(data.get('results'),list):raise ValueError()
            results=[]
            for row in data['results'][:20]:
                if not isinstance(row,dict) or not isinstance(row.get('url'),str):continue
                # Never take search titles/snippets as evidence of a feed's identity/freshness.
                try:source_url(row['url'])
                except SourceError:continue
                if row['url'] not in results:results.append(row['url'])
                if len(results)>=maximum:break
            return results
        except (ValueError,TypeError,RecursionError):raise SourceError('Réponse SearXNG invalide') from None


class Discovery:
    def __init__(self,manager,*,config=None,search=None,transport=None,cancel=None):
        self.manager=manager;config=config or {};self.options={}
        for name,default,lo,hi in [('seconds',25,1,45),('requests',12,1,20),('per_host',8,1,12),('sites',2,1,3),('links',100,1,200),('feeds',8,1,12),('results',12,1,20)]:
            value=config.get(name,default)
            if type(value) is not int or not lo<=value<=hi:raise SourceError('Limite de découverte invalide')
            self.options[name]=value
        self.search=search;self.transport=transport;self.cancel=cancel or threading.Event()
        self.budget=http.RequestBudget(self.options['seconds'],self.options['requests'],self.options['per_host'])
        self.ingestor=FeedIngestor(manager.health_path.with_name('discovery_feed_state.json') if manager.health_path else None,
            config={'feed_timeout':8,'max_articles_per_feed':100},http_get=self.get)
        self.ingestor.cancel_event=self.cancel;self.warnings=[];self.verified=0;self.results={}
        if not self.ingestor.state.writable:raise SourceError('État de découverte indisponible')

    def get(self,url,**kwargs):
        source_url(url)
        if self.cancel.is_set():raise http.FetchError('cancelled')
        timeout=min(8,http.remaining(self.budget.deadline))
        if self.transport:return self.transport(url,timeout=timeout,cancel_event=self.cancel,host_budget=self.ingestor.host_budget,request_budget=self.budget,**{k:v for k,v in kwargs.items() if k in ('conditional','accept_not_modified')})
        client=http.ExternalHTTPClient(http.Limits(total_timeout=timeout,io_timeout=min(5,timeout),max_bytes=1024*1024,max_redirects=2))
        return client.get(url,cancel_event=self.cancel,host_budget=self.ingestor.host_budget,request_budget=self.budget,
            **{k:v for k,v in kwargs.items() if k in ('conditional','accept_not_modified')})

    def failed(self,url,exc):
        if isinstance(exc,http.HTTPStatusError) and exc.status_code==304:
            result=FeedResult('not_modified',reason='cache_miss',http_status=304)
        elif isinstance(exc,http.HTTPStatusError):
            result=FeedResult('temporarily_unavailable' if exc.status_code>=500 or exc.status_code in (408,425,429) else 'permanently_unavailable',
                reason='http_'+str(exc.status_code),http_status=exc.status_code,retry_after=retry_after(exc.retry_after),rate_host=key(exc.host) if exc.host else '')
        else:result=FeedResult('blocked_by_security' if isinstance(exc,(http.BlockedURL,SourceError)) else 'temporarily_unavailable',reason='security_policy' if isinstance(exc,(http.BlockedURL,SourceError)) else 'network_or_budget')
        if isinstance(exc,http.TransportError):result=replace(result,reason=exc.kind)
        elif isinstance(exc,http.FetchError) and str(exc) in ('deadline','dns_timeout','dns_busy','request_budget','cancelled','response_too_large'):
            result=replace(result,reason=str(exc))
        self.ingestor.record(url,result);return result

    def convert(self,c,result):
        now=datetime.now(timezone.utc).isoformat();samples=tuple({'title':e.title,'published_at':e.published_at.isoformat() if e.published_at else None} for e in result.entries[:3])
        dates=[e.published_at.isoformat() for e in result.entries if e.published_at]
        title=result.feed_title or c.title or next((e.source for e in result.entries if e.source),'')
        return replace(c,title=plain(title),state=result.status,description=plain(result.description,500),language=result.language,format=result.format or None,entries=len(result.entries),latest_published_at=max(dates,default=None),
            checked_at=None if result.deferred else now,warnings=result.warnings,samples=samples,final_url=result.response_url or None,http_status=result.http_status,reason=result.reason)

    def verify(self,c):
        if c.id in self.results:return
        if self.verified>=self.options['feeds'] or self.cancel.is_set() or time.monotonic()>=self.budget.deadline:
            self.results[c.id]=c;return
        self.verified+=1
        result=self.ingestor.fetch(c.url)
        self.results[c.id]=self.convert(c,result)

    def site(self,url):
        source_url(url)
        if self.cancel.is_set() or self.verified>=self.options['feeds'] or time.monotonic()>=self.budget.deadline:return
        prior=self.ingestor._prior(url)
        if prior.get('next_attempt_at',0)>self.ingestor.now().timestamp():
            c=Candidate(url,url,'site');self.results[c.id]=replace(c,state='temporarily_unavailable',reason='scheduled_later');return
        try:
            response=self.get(url)
            if response.status_code!=200:raise http.HTTPStatusError(response.status_code)
        except (http.FetchError,SourceError) as exc:
            c=Candidate(url,url,'site');self.results[c.id]=self.convert(c,self.failed(url,exc));return
        # Direct feed URLs are supported without downloading the same resource twice.
        parsed=parse_feed(response.content,url,response.url,max_bytes=1024*1024)
        if parsed.status in ('success','empty'):
            self.verified+=1;self.ingestor.record(url,parsed)
            c=Candidate(url,response.url,'direct');self.results[c.id]=self.convert(c,parsed);return
        content_type=response.headers.get('content-type','').lower()
        if 'html' not in content_type and not re.search(br'<(?:!doctype\s+html|html|head|body|link)\b',response.content[:2048],re.I):
            c=Candidate(url,response.url,'direct');self.results[c.id]=self.convert(c,parsed);return
        try:
            encoding=re.search(r'charset=["\']?([\w-]+)',content_type)
            candidates=header_candidates(response.headers.get('link',''),response.url)+html_candidates(response.content,response.url,self.options['links'],encoding[1] if encoding else 'utf-8')
        except SourceError:self.warnings.append('invalid_html');candidates=[]
        parts=urlsplit(response.url);origin=urlunsplit((parts.scheme,parts.netloc,'/','',''))
        candidates += [candidate(path,origin,'conventional') for path in CONVENTIONAL]
        unique={}
        for c in candidates:
            if c and c.id not in unique:unique[c.id]=c
            if len(unique)>=self.options['results']:break
        for c in unique.values():self.verify(c)

    def run(self,mode,value):
        if mode=='site':self.site(value)
        elif mode=='search':
            value=query(value)
            if self.search is None:raise SourceError('SearXNG non configuré ; la découverte par site reste disponible')
            try:sites=self.search.sites(value,self.get,self.options['sites'])
            except http.FetchError as exc:
                # Keep provider URL/query private; host cooldown remains visible only in the private journal.
                self.failed(self.search.endpoint,exc);raise SourceError('SearXNG indisponible') from None
            for url in sites:
                if self.cancel.is_set() or time.monotonic()>=self.budget.deadline:break
                self.site(url)
        else:raise SourceError('Mode invalide')
        doc=self.manager.read();rows=[]
        for c in self.results.values():
            membership=tuple({'id':s.id,'category':s.category,'enabled':s.enabled} for s in doc.sources if s.url==c.url)
            valid=c.state in ('success','empty','not_modified') and bool(c.format)
            match=bool(mode=='search' and any(word.casefold() in c.title.casefold() for word in value.split()))
            criteria=(('verified' if valid else 'unverified'),('declared' if c.method in ('html_alternate','http_link') else c.method),('theme_match' if match else 'no_theme_evidence'),('dated' if c.latest_published_at else 'date_unknown'),('existing' if membership else 'new'))
            rows.append(replace(c,existing=membership,criteria=criteria))
        rows.sort(key=lambda c:ranking(c.public()))
        return {'candidates':[c.public() for c in rows[:self.options['results']]],'version':doc.version,'warnings':self.warnings,
            'cancelled':self.cancel.is_set(),'requests':self.budget.count,'verified':self.verified,'limits':self.options}
