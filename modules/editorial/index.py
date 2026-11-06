"""Private derived index: bounded input, stable locks, no article writes or network."""
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import datetime,timezone,timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
from zoneinfo import ZoneInfo
from modules.core.source_manager import atomic_write
from modules.editorial.events import Coverage,group_coverages,sha,ALGORITHM

MAX_INDEX=8*1024*1024
class IndexError(ValueError):pass


def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError()
            result[k]=v
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise IndexError('invalid_json') from None


def read(path,maximum=MAX_INDEX):
    path=Path(path)
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    except OSError:raise IndexError('unsafe_or_unavailable_file') from None
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>maximum:raise IndexError('unsafe_or_oversized_file')
        raw=stream.read(maximum+1)
        if len(raw)>maximum:raise IndexError('file_limit')
    return raw


@contextmanager
def lock(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise IndexError('unsafe_lock')
        until=time.monotonic()+5
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>until:raise IndexError('busy')
                time.sleep(.02)
        yield
    finally:os.close(fd)


def override_state(path):
    raw=read(path,256*1024)
    if raw is None:return {'version':1,'separate':[],'merge':[],'titles':{},'history':[]},sha('')
    data=decode(raw)
    if not isinstance(data,dict) or (type(data.get('version')) is not int or data.get('version')!=1) or set(data)-{'version','separate','merge','titles','history'}:raise IndexError('invalid_overrides')
    for action in ('separate','merge'):
        values=data.get(action)
        if not isinstance(values,list) or len(values)>1000:raise IndexError('override_limit')
        for pair in values:
            if not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(i,str) for i in pair) or len(set(pair))!=2 or any(not isinstance(i,str) or not re.fullmatch('[0-9a-f]{64}',i) for i in pair):raise IndexError('invalid_pair')
    if not isinstance(data.get('titles'),dict) or len(data['titles'])>500 or not isinstance(data.get('history'),list) or len(data['history'])>1000:raise IndexError('override_limit')
    for identity,title in data['titles'].items():
        if not re.fullmatch('[0-9a-f]{64}',identity) or not isinstance(title,str) or not title.strip() or len(title)>1000:raise IndexError('invalid_title_override')
    if {frozenset(x) for x in data['separate']}&{frozenset(x) for x in data['merge']}:raise IndexError('conflicting_overrides')
    validate_overrides(data)
    return data,sha(raw.decode())


class EventIndex:
    def __init__(self,articles,data,*,clock=None,days=14):
        self.articles=Path(articles);self.data=Path(data);self.path=self.data/'editorial_events.json';self.overrides=self.data/'editorial_overrides.json'
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        if type(days) is not int or not 1<=days<=31:raise IndexError('retention_limit')
        self.days=days
        for path in (self.articles,self.data):
            if any(part.is_symlink() for part in (path,*path.parents)):raise IndexError('unsafe_directory')

    def load(self,*,allow_stale=False):
        raw=read(self.path)
        if raw is None:return None
        data=decode(raw)
        if not isinstance(data,dict) or (type(data.get('version')) is not int or data.get('version')!=1) or data.get('algorithm')!=ALGORITHM or not isinstance(data.get('events'),list) or len(data['events'])>500:raise IndexError('invalid_index')
        if not allow_stale and data.get('overrides_sha')!=override_state(self.overrides)[1]:raise IndexError('overrides_require_rebuild')
        ids=set()
        try:
            if data.get('status') not in ('ready','degraded'):raise ValueError()
            if datetime.fromisoformat(data['generated_at']).tzinfo is None:raise ValueError()
            for name in ('input_sha','overrides_sha'):
                if not isinstance(data.get(name),str) or not re.fullmatch('[0-9a-f]{64}',data[name]):raise ValueError()
            for event in data['events']:
                if not isinstance(event,dict) or not re.fullmatch('evt_[0-9a-f]{32}',event['event_id']) or event['event_id'] in ids:raise ValueError()
                ids.add(event['event_id'])
                for field in ('last_updated_at','created_at','first_discovered_at','event_at','latest_published_at'):
                    if event.get(field) is not None:
                        stamp=datetime.fromisoformat(event[field])
                        if stamp.tzinfo is None:raise ValueError()
                if type(event.get('article_count')) is not int or type(event.get('domain_count')) is not int or not 0<=event['domain_count']<=event['article_count']:raise ValueError()
                if event.get('algorithm')!=ALGORITHM or event.get('status') not in ('single','grouped','human_association'):raise ValueError()
                for field in ('sources','domains','article_hashes','secondary_categories','disagreements','reasons'):
                    if not isinstance(event.get(field),list) or len(event[field])>1000 or any(not isinstance(v,str) for v in event[field]):raise ValueError()
                if not isinstance(event['category'],str) or len(event['category'])>100:raise ValueError()
                if not isinstance(event['articles'],list) or not 1<=len(event['articles'])<=500 or event['article_count']!=len(event['articles']):raise ValueError()
                for article in event['articles']:
                    if not isinstance(article,dict):raise ValueError()
                    if not re.fullmatch('[0-9a-f]{64}',article['id']):raise ValueError()
                    from modules.editorial.events import public_link
                    if article.get('link') is not None and public_link(article['link'])!=article['link']:raise ValueError()
                    for field,maximum in (('title',1000),('original_title',1000),('summary',4000),('source',200),('category',100),('published_raw',1024),('observed_raw',100)):
                        if not isinstance(article.get(field),str) or len(article[field])>maximum:raise ValueError()
                    for field in ('published_at','observed_at','first_seen_at','fetched_at','processed_at'):
                        if article.get(field) is not None and datetime.fromisoformat(article[field]).tzinfo is None:raise ValueError()
                public_event(event)
                if not isinstance(event['title'],str) or len(event['title'])>1000 or event['independent_confirmation'] is not False:raise ValueError()
            if not isinstance(data.get('manifest'),dict) or not isinstance(data.get('diagnostics'),list):raise ValueError()
        except (ValueError,KeyError,TypeError):raise IndexError('invalid_index') from None
        return data

    def snapshot(self):
        if self.articles.is_symlink() or self.data.is_symlink():raise IndexError('unsafe_directory')
        now=self.clock()
        if now.tzinfo is None:raise IndexError('aware_clock_required')
        local_date=now.astimezone(ZoneInfo('Europe/Paris')).date();cutoff=local_date-timedelta(days=self.days-1);manifest={};diagnostics=[];coverages={};total=0;files=[]
        if self.articles.exists():
            files=sorted(self.articles.glob('*_articles.json'))
            for child in self.articles.iterdir():
                if re.fullmatch(r'\d{8}',child.name) and child.is_dir():diagnostics.append({'file':child.name,'state':'legacy_subdirectory_requires_explicit_adapter'})
            if len(files)>10000:raise IndexError('inventory_limit')
        for path in files:
            match=re.fullmatch('(\\d{8})_articles.json',path.name)
            if not match:continue
            try:day=datetime.strptime(match[1],'%Y%m%d').date()
            except ValueError:
                diagnostics.append({'file':path.name,'state':'invalid_filename_date'});continue
            if not cutoff<=day<=local_date:continue
            if len(manifest)>=31:raise IndexError('file_count_limit')
            raw=None
            try:
                raw=read(path,4*1024*1024)
                if raw is None:raise IndexError('disappeared_file')
                total+=len(raw)
                if total>32*1024*1024:raise IndexError('input_size_limit')
                manifest[path.name]=sha(raw.decode('utf-8'))
                values=decode(raw)
                if not isinstance(values,list):raise IndexError('invalid_article_array')
                if len(values)>500:raise IndexError('article_limit')
            except (IndexError,UnicodeError) as exc:
                if str(exc) in ('input_size_limit','article_limit'):raise
                diagnostics.append({'file':path.name,'state':str(exc) if isinstance(exc,IndexError) else 'invalid_encoding'})
                # Hash a malformed UTF-8/JSON source too, preserving its explicit state.
                if raw is not None:manifest[path.name]=__import__('hashlib').sha256(raw).hexdigest()
                continue
            for i,a in enumerate(values):
                try:c=Coverage.from_article(a,f'{path.name}:{i}')
                except (ValueError,TypeError):diagnostics.append({'file':path.name,'record':i,'state':'invalid_article'});continue
                if c.id in coverages:c=replace(c,locations=tuple(sorted(set(c.locations+coverages[c.id].locations))))
                coverages[c.id]=c
                if len(coverages)>500:raise IndexError('article_limit')
        fingerprint=sha(json.dumps([manifest,diagnostics],sort_keys=True,separators=(',',':')))
        return list(coverages.values()),manifest,diagnostics,fingerprint

    def rebuild(self,*,apply=False,expected=None):
        if apply:
            with lock(self.data/'editorial-events.lock'),lock(self.data/'article-integrity.lock'):
                return self._build(True,expected)
        return self._build(False,expected)

    def _build(self,apply,expected):
        previous=self.load(allow_stale=True);overrides,override_sha=override_state(self.overrides)
        coverages,manifest,diagnostics,fingerprint=self.snapshot()
        if apply and expected!=fingerprint:raise IndexError('input_changed_or_unreviewed')
        known={c.id for c in coverages}
        references={i for name in ('separate','merge') for pair in overrides[name] for i in pair}|set(overrides['titles'])
        missing=sorted(references-known)
        if missing:diagnostics.append({'state':'override_references_outside_snapshot','count':len(missing),'ids':missing[:100]})
        now=self.clock().astimezone(timezone.utc).isoformat()
        events,uncertain=group_coverages(coverages,previous=previous['events'] if previous else None,overrides=overrides,now=now)
        unchanged=previous and previous.get('input_sha')==fingerprint and previous.get('overrides_sha')==override_sha
        result={'version':1,'algorithm':ALGORITHM,'generated_at':previous['generated_at'] if unchanged else now,'input_sha':fingerprint,'overrides_sha':override_sha,
            'status':'degraded' if diagnostics else 'ready','manifest':manifest,'diagnostics':diagnostics,'events':events,'uncertain':uncertain,'retention_days':self.days}
        raw=json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))
        if len(raw.encode())>MAX_INDEX:raise IndexError('index_size_limit')
        if apply:
            if self.snapshot()[3]!=fingerprint:raise IndexError('inputs_changed_during_rebuild')
            # Override writers use this same index lock; their file is never overwritten here.
            atomic_write(self.path,raw)
        return result

    def update_overrides(self,patch,*,apply=False,expected=None,reason=''):
        if not isinstance(patch,dict) or set(patch)-{'separate','merge','titles'}:raise IndexError('invalid_patch')
        if apply:
            with lock(self.data/'editorial-events.lock'):return self._update_overrides(patch,True,expected,reason)
        return self._update_overrides(patch,False,expected,reason)

    def _update_overrides(self,patch,apply,expected,reason):
        current,digest=override_state(self.overrides)
        if apply and (expected!=digest or not isinstance(reason,str) or not 1<=len(reason)<=300):raise IndexError('reviewed_version_and_reason_required')
        proposed={**current,**patch}
        if len(current['history'])>=1000:raise IndexError('override_audit_limit')
        proposed['history']=current['history']+[{'at':self.clock().astimezone(timezone.utc).isoformat(),'reason':reason,'previous_sha':digest}]
        raw=json.dumps(proposed,ensure_ascii=False,allow_nan=False)
        # Validate without touching any existing file.
        validate_overrides(proposed)
        if len(raw.encode())>256*1024:raise IndexError('override_size_limit')
        if apply:atomic_write(self.overrides,raw)
        return {'current_sha':digest,'overrides':proposed,'applied':apply}


def validate_overrides(value):
    if not isinstance(value.get('titles'),dict) or len(value['titles'])>500:raise IndexError('invalid_overrides')
    for name in ('separate','merge'):
        if not isinstance(value.get(name),list) or len(value[name])>1000:raise IndexError('invalid_overrides')
        for pair in value[name]:
            if not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(i,str) or not re.fullmatch('[0-9a-f]{64}',i) for i in pair) or pair[0]==pair[1]:raise IndexError('invalid_pair')
    if {frozenset(p) for p in value['separate']}&{frozenset(p) for p in value['merge']}:raise IndexError('conflicting_overrides')
    parents={}
    def find(x):
        parents.setdefault(x,x)
        while parents[x]!=x:
            parents[x]=parents[parents[x]];x=parents[x]
        return x
    for a,b in value['merge']:
        left,right=find(a),find(b)
        if left!=right:parents[right]=left
    for a,b in value['separate']:
        if find(a)==find(b):raise IndexError('conflicting_overrides')
    sizes={}
    for identity in list(parents):sizes[find(identity)]=sizes.get(find(identity),0)+1
    if any(size>50 for size in sizes.values()):raise IndexError('group_limit')
    for identity,title in value['titles'].items():
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity) or not isinstance(title,str) or not title.strip() or len(title)>1000:raise IndexError('invalid_title_override')


def public_event(event):
    # Explicit projection: no filenames, override audit, journal entries or configuration.
    fields=('event_id','title','category','secondary_categories','article_hashes','sources','domains','article_count','domain_count','first_discovered_at','last_updated_at','event_at','latest_published_at','status','confidence','disagreements','algorithm','independent_confirmation','reasons')
    result={k:event[k] for k in fields}
    result['articles']=[{k:a.get(k) for k in ('id','article_hash','title','original_title','summary','link','source','domain','category','published_raw','published_at','publication_status','observed_raw','observed_at','observation_status','language','first_seen_at','fetched_at','processed_at')} for a in event['articles']]
    return result
