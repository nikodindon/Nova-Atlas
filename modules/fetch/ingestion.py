"""Bounded source downloads. Workers never write article/health files or call a LLM."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import json
import logging
import math
import os
import stat
from functools import wraps
from pathlib import Path
import threading
import time
from modules.http import external as http
from modules.fetch.article_state import atomic_json
from modules.fetch.feed_parser import FeedEntry,FeedResult,parse_feed
from modules.core.llm_gateway import retry_after

log=logging.getLogger('nova.ingestion')
MAX_STATE=8*1024*1024


def key(value):return hashlib.sha256(value.encode()).hexdigest()


def options(config):
    values={}
    for name,default,low,high in [('feed_workers',4,1,4),('feed_per_host',1,1,2),('feed_timeout',10,1,60),
        ('max_articles_per_feed',8,1,100),('feed_max_sources',256,1,512),('feed_min_poll_seconds',60,0,86400),
        ('feed_retry_base_seconds',300,1,86400),('feed_retry_cap_seconds',21600,1,604800),('max_age_days',0,0,36500)]:
        value=config.get(name,default)
        if type(value) is not int or not low<=value<=high:raise ValueError('Invalid RSS ingestion setting')
        values[name]=value
    values['feed_per_host']=min(values['feed_per_host'],values['feed_workers'])
    return values


class FeedState:
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else None;self.records={};self.writable=True;self.dirty=False
        if self.path is None:return
        if self.path.is_symlink():
            self.writable=False;log.warning('Feed state symlink refused');return
        if not self.path.exists():return
        try:
            fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as source:
                info=os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_STATE:raise ValueError()
                raw=source.read(MAX_STATE+1)
                if len(raw)>MAX_STATE:raise ValueError()
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
            if type(data.get('version')) is not int or data.get('version')!=1 or not isinstance(data.get('records'),dict) or len(data['records'])>515:raise ValueError()
            for identity,record in data['records'].items():
                if len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity) or not isinstance(record,dict):raise ValueError()
                if set(record)-{'last_attempt','status','error','http_status','duration','warnings','policy','host','last_success','failures','next_attempt_at','last_count','last_good_fetch','response_url','etag','last_modified','entries','latest_published_at','host_until','rate_host','retry_until'}:raise ValueError()
                for name in ('policy','host','rate_host'):
                    if name in record and (not isinstance(record[name],str) or len(record[name])!=64):raise ValueError()
                for name in ('last_attempt','next_attempt_at','host_until','retry_until','last_good_fetch','last_success'):
                    if name in record and (type(record[name]) not in (int,float) or not math.isfinite(record[name])):raise ValueError()
                if type(record.get('failures',0)) is not int or not 0<=record.get('failures',0)<=10000:raise ValueError()
                if not isinstance(record.get('entries',[]),list) or len(record.get('entries',[]))>100:raise ValueError()
                if record.get('status') not in ('success','not_modified','empty','invalid_feed','temporarily_unavailable','permanently_unavailable','blocked_by_security'):raise ValueError()
                if record.get('http_status') is not None and (type(record['http_status']) is not int or not 100<=record['http_status']<=599):raise ValueError()
                if not isinstance(record.get('error',''),str) or len(record.get('error',''))>128:raise ValueError()
                if not isinstance(record.get('warnings',[]),list) or len(record.get('warnings',[]))>100:raise ValueError()
                if any(not isinstance(v,str) or len(v)>128 for v in record.get('warnings',[])):raise ValueError()
                for entry in record.get('entries',[]):
                    if key(FeedEntry.from_dict(entry).feed_url)!=identity:raise ValueError()
                if record.get('etag') is not None and http.validator_value(record['etag']) is None:raise ValueError()
                if record.get('last_modified') is not None and http.validator_value(record['last_modified'],128) is None:raise ValueError()
                if record.get('response_url'):http.parse_url(record['response_url'])
            self.records=data['records']
        except (ValueError,TypeError,KeyError,OSError,http.FetchError):
            self.writable=False;log.warning('Feed state invalid: originals preserved, conditional cache disabled')

    def save(self,now):
        if not self.writable or self.path is None or not self.dirty:return
        self.records={k:r for k,r in self.records.items() if now-r.get('last_attempt',now)<90*86400 or max(r.get('host_until',0),r.get('retry_until',0))>now}
        # Evict whole records, including validators, never leave a validator with a missing cache.
        while self.records and (len(self.records)>512 or len(json.dumps({'version':1,'records':self.records}).encode())>MAX_STATE):
            candidates=[k for k,r in self.records.items() if max(r.get('host_until',0),r.get('retry_until',0))<=now]
            if candidates:
                oldest=min(candidates,key=lambda k:self.records[k].get('last_attempt',0));del self.records[oldest]
            else:
                for record in self.records.values():
                    for name in ('entries','etag','last_modified','response_url'):record.pop(name,None)
                break # Retain active server embargoes, at most 512 + three admitted workers.
        try:atomic_json(self.path,{'version':1,'records':self.records});self.dirty=False
        except OSError:
            self.writable=False;log.warning('Feed state unavailable; articles remain independently recoverable')


def synchronized(method):
    @wraps(method)
    def invoke(self,*args,**kwargs):
        with self._lock:return method(self,*args,**kwargs)
    return invoke


class FeedIngestor:
    def __init__(self,path=None,*,config=None,clock=None):
        self._lock=threading.RLock()
        self.options=options(config or {});self.state=FeedState(path);self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.policy=key(json.dumps({'parser':1,'limit':self.options['max_articles_per_feed'],'age':self.options['max_age_days']}))
        self.cancel_event=None
        self.host_budget=http.HostBudget(self.options['feed_per_host'],clock=lambda:self.now().timestamp(),default_cooldown=self.options['feed_retry_base_seconds'])
        embargo_records=list(self.state.records.values())
        if self.state.path is not None and self.state.path.name=='feed_ingestion_state.json':
            # Admin tests own a separate journal, but their server embargoes also
            # constrain the next News cycle. Never merge/write its entries/cache.
            auxiliary=self.state.path.with_name('source_test_state.json')
            if auxiliary.exists():embargo_records.extend(FeedState(auxiliary).records.values())
        for rec in embargo_records:
            if rec.get('host_until',0)>self.now().timestamp() and rec.get('rate_host',rec.get('host')):
                self.host_budget.defer_key(rec.get('rate_host',rec['host']),rec['host_until'])

    def now(self):
        value=self.clock()
        return datetime.fromtimestamp(value.timestamp(),timezone.utc)

    @staticmethod
    def host(url):return key(http.parse_url(url).host)

    @synchronized
    def host_until(self,host):return max((r.get('host_until',0) for r in self.state.records.values() if r.get('host')==host or r.get('rate_host')==host),default=0)

    @synchronized
    def _prior(self,url):
        record=dict(self.state.records.get(key(url),{}))
        if record.get('policy')!=self.policy:
            for name in ('etag','last_modified','entries','response_url','policy'):record.pop(name,None)
            if record.get('status') in ('success','empty','not_modified'):record['next_attempt_at']=0
        return record

    def download(self,url):
        now=self.now();started=time.monotonic();prior=self._prior(url)
        if self.cancel_event is not None and self.cancel_event.is_set():return FeedResult('temporarily_unavailable',reason='cancelled',deferred=True)
        if prior.get('next_attempt_at',0)>now.timestamp():return FeedResult('temporarily_unavailable',reason='scheduled_later',deferred=True)
        try:
            host=self.host(url)
            with self._lock:
                if key(url) not in self.state.records and len(self.state.records)>=512 and all(max(r.get('host_until',0),r.get('retry_until',0))>now.timestamp() for r in self.state.records.values()):
                    return FeedResult('temporarily_unavailable',reason='state_capacity',deferred=True)
            if max(prior.get('next_attempt_at',0),self.host_until(host))>now.timestamp():
                return FeedResult('temporarily_unavailable',reason='scheduled_later',deferred=True)
            conditional=None
            if prior.get('response_url') and (prior.get('etag') or prior.get('last_modified')):
                conditional=http.Conditional(prior['response_url'],prior.get('etag'),prior.get('last_modified'))
            response=http.get(url,timeout=self.options['feed_timeout'],conditional=conditional,accept_not_modified=True,cancel_event=self.cancel_event,host_budget=self.host_budget)
            status=response.status_code;headers=response.headers;response_url=response.url
            if status==304:
                entries=tuple(FeedEntry.from_dict(v) for v in prior.get('entries',[]))
                return FeedResult('not_modified',entries,reason='' if 'entries' in prior else 'cache_miss',http_status=304,
                    response_url=response_url,retrieved_at=now,duration=time.monotonic()-started)
            if status!=200:raise http.HTTPStatusError(status,headers.get('retry-after'))
            parsed=parse_feed(response.content,url,response_url,retrieved_at=now)
            if parsed.status=='invalid_feed':return replace(parsed,http_status=status,duration=time.monotonic()-started)
            warnings=list(parsed.warnings);eligible=[];old=0
            for entry in parsed.entries:
                age=(now-entry.published_at).total_seconds()/86400 if entry.published_at is not None else None
                if age is not None and age < -1:warnings.append('future_date')
                if self.options['max_age_days'] and age is not None and age>self.options['max_age_days']:old+=1;continue
                eligible.append(entry)
            if old:warnings.append('old_entries_deferred:'+str(old))
            if len(eligible)>self.options['max_articles_per_feed']:warnings.append('entry_window_limited')
            content_type=headers.get('content-type','').split(';',1)[0].lower()
            if content_type and 'xml' not in content_type:warnings.append('non_xml_content_type')
            return replace(parsed,entries=tuple(eligible[:self.options['max_articles_per_feed']]),warnings=tuple(dict.fromkeys(warnings)),http_status=200,
                etag=http.validator_value(headers.get('etag')),last_modified=http.validator_value(headers.get('last-modified'),128),duration=time.monotonic()-started)
        except http.BlockedURL:
            return FeedResult('blocked_by_security',reason='security_policy',retrieved_at=now,duration=time.monotonic()-started)
        except http.HTTPStatusError as exc:
            status=exc.status_code
            temporary=status in (408,425,429) or status>=500
            return FeedResult('temporarily_unavailable' if temporary else 'permanently_unavailable',reason='http_'+str(status),http_status=status,
                retrieved_at=now,duration=time.monotonic()-started,retry_after=retry_after(exc.retry_after,now.timestamp()),rate_host=key(exc.host) if exc.host else '')
        except http.FetchError as exc:
            reason=str(exc) if str(exc) in ('dns_timeout','dns_busy','deadline','cancelled','response_too_large','transport_error','truncated_response','dns_error') else 'http_transport'
            return FeedResult('temporarily_unavailable',reason=reason,retrieved_at=now,duration=time.monotonic()-started)
        except Exception:
            return FeedResult('invalid_feed',reason='parser_or_cache_error',retrieved_at=now,duration=time.monotonic()-started)

    @synchronized
    def record(self,url,result):
        if result.deferred:return
        now=self.now().timestamp();record=self._prior(url)
        record.update(last_attempt=now,status=result.status,error=result.reason,http_status=result.http_status,duration=result.duration,
            warnings=list(result.warnings),policy=self.policy)
        try:record['host']=self.host(url)
        except http.BlockedURL:pass
        if result.status in ('success','empty') or (result.status=='not_modified' and result.reason!='cache_miss'):
            record.pop('retry_until',None)
            record.update(last_success=now,failures=0,next_attempt_at=now+self.options['feed_min_poll_seconds'],last_count=len(result.entries))
            if result.status!='not_modified':
                record.update(last_good_fetch=now,response_url=result.response_url,etag=result.etag,last_modified=result.last_modified,
                              entries=[e.to_dict() for e in result.entries])
                dates=[e.published_at for e in result.entries if e.published_at is not None]
                record['latest_published_at']=max(dates).isoformat() if dates else record.get('latest_published_at')
        else:
            failures=min(10000,record.get('failures',0)+1);delay=min(self.options['feed_retry_cap_seconds'],self.options['feed_retry_base_seconds']*2**min(failures-1,20))
            if result.status in ('blocked_by_security','permanently_unavailable'):delay=max(delay,86400)
            if result.retry_after is not None:
                delay=max(delay,result.retry_after);record['retry_until']=now+delay
            record.update(failures=failures,next_attempt_at=now+delay)
            if result.http_status==429:
                record['host_until']=now+delay
                record['rate_host']=result.rate_host or record.get('host','')
                self.host_budget.defer_key(record['rate_host'],record['host_until'])
            if result.reason=='cache_miss':
                for name in ('etag','last_modified','response_url','entries'):record.pop(name,None)
        self.state.records[key(url)]=record;self.state.dirty=True;self.state.save(now)
        log.info('Feed key=%s status=%s reason=%s entries=%d warnings=%d',key(url)[:12],result.status,result.reason,len(result.entries),len(result.warnings))

    def invalidate(self,url):
        with self._lock:
            record=self.state.records.get(key(url))
            if record is not None:
                for name in ('etag','last_modified','response_url','entries','policy'):record.pop(name,None)
                self.state.dirty=True;self.state.save(self.now().timestamp())

    def fetch(self,url):
        result=self.download(url);self.record(url,result);return result

    def fetch_many(self,urls,*,downloader=None,cancel_event=None):
        self.cancel_event=cancel_event;downloader=downloader or self.download
        ordered=list(dict.fromkeys(urls));allowed=ordered[:self.options['feed_max_sources']]
        results={u:FeedResult('temporarily_unavailable',reason='source_limit',deferred=True) for u in ordered[len(allowed):]}
        pending=list(allowed);active={};by_host={}
        with ThreadPoolExecutor(max_workers=self.options['feed_workers'],thread_name_prefix='FeedHTTP') as pool:
            while pending or active:
                if cancel_event is not None and cancel_event.is_set():
                    for u in pending:results[u]=FeedResult('temporarily_unavailable',reason='cancelled',deferred=True)
                    pending.clear()
                for url in list(pending):
                    if len(active)>=self.options['feed_workers']:break
                    try:host=self.host(url)
                    except http.BlockedURL:
                        result=self.download(url);results[url]=result;self.record(url,result);pending.remove(url);continue
                    if by_host.get(host,0)>=self.options['feed_per_host']:continue
                    future=pool.submit(downloader,url);active[future]=(url,host);by_host[host]=by_host.get(host,0)+1;pending.remove(url)
                if not active:continue
                done,_=wait(active,return_when=FIRST_COMPLETED)
                for future in done:
                    url,host=active.pop(future);by_host[host]-=1
                    try:result=future.result()
                    except Exception:result=FeedResult('temporarily_unavailable',reason='download_worker_error')
                    results[url]=result;self.record(url,result)
        return {url:results[url] for url in ordered}
