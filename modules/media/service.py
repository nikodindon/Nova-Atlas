"""Explicit bounded admin network operations; collection hooks never fetch images."""
from contextlib import contextmanager
from datetime import datetime,timezone
import os
import threading
import time
from modules.media.store import MediaStore,MediaError,MediaConflict,target_for_article,target_for_event
from modules.media.commons import Commons
from modules.media.candidates import ImageCandidate,html_images
from modules.editorial.index import EventIndex,lock
from modules.fetch.ingestion import FeedIngestor,key
from modules.fetch.feed_parser import FeedEntry,FeedResult
from modules.core.llm_gateway import retry_after
from modules.core.source_manager import source_url
from modules.http import external as http


class MediaService:
    def __init__(self,articles,data,config=None,*,transport=None,runner=None,clock=None):
        config=config or {};self.config=config;self.data=data;self.articles=articles
        self.store=MediaStore(data,mode=config.get('mode','disabled'),clock=clock,runner=runner)
        self.transport=transport;self.gate=threading.Lock();self.ingestor=None;self.budget=None;self.cancel=threading.Event()

    def targets(self):
        index=EventIndex(self.articles,self.data);coverages,_,_,_=index.snapshot()
        result=[{'kind':'article','id':c.article_hash,'fingerprint':c.id,'title':c.title,'link':c.link,'origins':list(c.locations)[:5]} for c in coverages]
        try:events=index.load()
        except (ValueError,OSError):events=None
        result += [target_for_event(e) for e in (events or {}).get('events',[])]
        return result

    def target(self,value):
        if not isinstance(value,dict) or set(value)!={'kind','id','fingerprint'}:raise MediaError('invalid_target')
        for target in self.targets():
            if all(target[k]==value[k] for k in value):return target
        raise MediaConflict('target_changed_reload_required')

    @contextmanager
    def operation(self):
        if self.store.mode=='disabled':raise MediaError('downloads_disabled')
        if not self.gate.acquire(blocking=False):raise MediaError('media_operation_active')
        try:
            self.store.init()
            with lock(self.store.root/'network.lock'):
                now=self.store.clock()
                def admit(state):
                    values=[t for t in state['admissions'] if t>now-60]
                    if len(values)>=3:raise MediaError('three_media_operations_per_minute')
                    state['admissions']=values+[now]
                self.store.transaction(admit)
                self.cancel.clear();self.budget=http.RequestBudget(seconds=25,requests=4,per_host=4)
                self.ingestor=FeedIngestor(self.store.data/'media_http_state.json')
                if not self.ingestor.state.writable:raise MediaError('http_state_unavailable')
                try:yield
                except MediaError:raise
                except (ValueError,TypeError,KeyError):raise MediaError('media_operation_refused') from None
        finally:self.ingestor=None;self.gate.release()

    def get(self,url):
        try:source_url(url)
        except ValueError:raise MediaError('blocked_by_security') from None
        if self.cancel.is_set():raise MediaError('cancelled')
        try:
            timeout=min(8,http.remaining(self.budget.deadline))
            if self.transport:response=self.transport(url,timeout=timeout,cancel_event=self.cancel,host_budget=self.ingestor.host_budget,request_budget=self.budget)
            else:response=http.ExternalHTTPClient(http.Limits(total_timeout=timeout,io_timeout=min(timeout,5),max_bytes=4*1024*1024,max_redirects=2)).get(url,cancel_event=self.cancel,host_budget=self.ingestor.host_budget,request_budget=self.budget)
            if response.status_code!=200:raise http.HTTPStatusError(response.status_code,response.headers.get('retry-after'))
            if len(response.content)>4*1024*1024:raise http.FetchError('response_too_large')
            return response
        except http.FetchError as error:
            code=error.status_code if isinstance(error,http.HTTPStatusError) else None
            result=FeedResult('blocked_by_security' if isinstance(error,http.BlockedURL) else 'temporarily_unavailable',reason='media_http_error',http_status=code,
                retry_after=retry_after(error.retry_after) if isinstance(error,http.HTTPStatusError) else None,rate_host=key(error.host) if isinstance(error,http.HTTPStatusError) and error.host else '')
            self.ingestor.record(url,result);raise MediaError('blocked_by_security' if isinstance(error,http.BlockedURL) else 'http_'+str(code) if code else getattr(error,'kind',str(error)) if str(error) in ('deadline','cancelled','request_budget') else 'network_error') from None

    def search(self,term,target):
        if self.config.get('commons_enabled') is not True:raise MediaError('commons_not_configured')
        target=self.target(target)
        with self.operation():
            candidates=Commons(self.get).fetch(term=term)
            return [self.store.add(c,target,trusted=True) for c in candidates]

    def refresh(self,identity):
        if self.config.get('commons_enabled') is not True:raise MediaError('commons_not_configured')
        state,_=self.store.load();row=state['records'].get(identity)
        if not row or row['candidate']['provider']!='commons':raise MediaError('commons_candidate_required')
        with self.operation():
            candidates=Commons(self.get).fetch(page_id=row['candidate']['resource_id'])
            if not candidates:
                self.store.transaction(lambda state:state['records'][identity].update(status='verification_failed',error='commons_resource_unavailable'))
                raise MediaError('commons_resource_unavailable')
            candidate=next((c for c in candidates if c.resource_id==row['candidate']['resource_id']),None)
            if candidate is None:raise MediaError('commons_resource_changed')
            # The identity includes configured URL; changed URL/resource invalidates old approval too.
            new_id=self.store.add(candidate,row['target'],trusted=True)
            if new_id!=identity:self.store.transaction(lambda state:state['records'][identity].update(status='verification_failed',error='resource_url_changed'))
            return new_id

    def prepare(self,identity,expected):
        state,version=self.store.load()
        if version!=expected:raise MediaConflict('version_conflict_reload_required')
        row=state['records'].get(identity)
        if not row or row['rights']['status']!='rights_verified':raise MediaError('rights_not_verified')
        # Metadata must have been checked recently; operator can refresh independently.
        if self.store.clock()-row['checked_at']>3600:raise MediaError('refresh_rights_before_preparing')
        if self.store.usage()+row['candidate']['proof']['size']+2*1024*1024>256*1024*1024:raise MediaError('disk_quota')
        with self.operation():
            response=self.get(row['candidate']['url'])
            # Admission changed only the manifest version; do not accept a changed candidate.
            current,current_version=self.store.load()
            if current['records'][identity]['resource_version']!=row['resource_version']:raise MediaConflict('resource_changed')
            try:return self.store.prepare(identity,response.content,expected=current_version)
            except ValueError as error:
                self.store.transaction(lambda s:s['records'][identity].update(error='image_verification_failed',status='verification_failed'))
                raise MediaError('image_verification_failed') from None

    def discover_cached(self,target):
        target=self.target(target)
        if self.store.mode=='disabled':return []
        if target['kind']!='article':raise MediaError('article_target_required')
        state=__import__('modules.fetch.ingestion',fromlist=['FeedState']).FeedState(self.store.data/'feed_ingestion_state.json')
        found=[]
        manifest,_=self.store.load()
        for row in manifest['records'].values():
            if row['target']['kind']=='article_reference' and row['target']['id']==target['id']:
                found.append(self.store.add(ImageCandidate(**row['candidate']),target))
        for record in state.records.values():
            for raw in record.get('entries',[]):
                entry=FeedEntry.from_dict(raw)
                if entry.link!=target['link']:continue
                for image in entry.images:
                    found.append(self.store.add(ImageCandidate(image['url'],image['method'],entry.link),target))
        return sorted(set(found))

    def discover_html(self,target):
        target=self.target(target)
        if target['kind']!='article' or not target.get('link'):raise MediaError('article_target_required')
        with self.operation():
            response=self.get(target['link'])
            return [self.store.add(c,target) for c in html_images(response.content,response.url)]

    def valid_target(self,target):
        try:
            if target['kind']=='event':
                index=EventIndex(self.articles,self.data).load()
                event=next((e for e in (index or {}).get('events',[]) if e['event_id']==target['id']),None)
                return event is not None and target_for_event(event)['fingerprint']==target['fingerprint']
            if target['kind']!='article':return False
            from modules.editorial.index import read,decode
            from modules.editorial.events import Coverage
            import re
            for origin in target.get('origins',[]):
                match=re.fullmatch(r'(\d{8}_articles.json):(\d{1,3})',origin)
                if not match:continue
                values=decode(read(self.articles/match[1],4*1024*1024))
                if not isinstance(values,list) or int(match[2])>=len(values):continue
                if Coverage.from_article(values[int(match[2])]).id==target['fingerprint']:return True
        except (ValueError,OSError,KeyError,TypeError):pass
        return False
