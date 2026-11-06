"""Opt-in local radio journal: admission/outbox and observations, never proof of listening."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid
from zoneinfo import ZoneInfo
from modules.core.source_manager import atomic_write
from modules.editorial.index import read,decode,lock
from modules.radio.audio_integrity import validated_publication,receipt_path,PUBLIC_NAME

ZONE=ZoneInfo('Europe/Paris')
STATES={'admitted','generating','ready','queued','playing','completed','skipped','failed','interrupted','recovery_required'}
CHANNELS={'primary','android','web'}
class StudioError(ValueError):pass
class StudioConflict(StudioError):pass


def checked(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise StudioError('unsafe_path')
    return path


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@contextmanager
def lease(path):
    path=checked(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise StudioError('unsafe_lock')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise StudioConflict('generation_or_runtime_active') from None
        yield
    finally:os.close(fd)


def calendar(hours,now,days=1):
    from modules.core.scheduler import validate_hours
    hours=validate_hours(hours);now=now.astimezone(ZONE)
    result=[]
    for offset in range(days):
        day=now.date()+timedelta(days=offset)
        for hour in hours:
            for minute in (0,30):
                target=datetime.combine(day,datetime.min.time(),ZONE).replace(hour=hour,minute=minute)
                if datetime.fromtimestamp(target.timestamp(),ZONE).replace(tzinfo=None)!=target.replace(tzinfo=None):continue
                launch=datetime.fromtimestamp(target.timestamp()-300,ZONE)
                result.append({'slot':target.strftime('%Y-%m-%dT%H:%M'),'target_at':target.isoformat(),'generation_planned_at':launch.isoformat()})
    return result


class StudioJournal:
    def __init__(self,data,queue,*,clock=None,configured=True):
        self.root=checked(data)/'radio_studio';self.path=self.root/'journal.json';self.queue=checked(queue)
        self.clock=clock or time.time;self.configured=configured;self.run_id=None
    def now(self):return datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()
    def init(self):checked(self.root).mkdir(parents=True,exist_ok=True,mode=0o700)
    def load(self):
        checked(self.path)
        try:
            raw=read(self.path,4*1024*1024)
            if raw is None:return {'version':1,'jobs':{},'channels':{},'runtime_id':None,'enabled':True,'controls':[],'reconcile_requested':False},digest(None)
            data=decode(raw)
            if set(data)!={'version','jobs','channels','runtime_id','enabled','controls','reconcile_requested'} or type(data['version']) is not int or data['version']!=1 or type(data['enabled']) is not bool or type(data['reconcile_requested']) is not bool:raise ValueError()
            if not isinstance(data['jobs'],dict) or len(data['jobs'])>1024 or not isinstance(data['controls'],list) or len(data['controls'])>128 or set(data['channels'])-CHANNELS:raise ValueError()
            for identity,row in data['jobs'].items():
                if not re.fullmatch('[a-f0-9]{32}',identity) or row['id']!=identity or row['status'] not in STATES or row['kind'] not in ('bulletin','flash') or not PUBLIC_NAME.fullmatch(row['publication_name']):raise ValueError()
                if row['publication_name']!=self.name(row['slot'],identity,row['kind']):raise ValueError()
                if len(row['events'])>64 or len(row['references'])>100 or set(row['channels'])-CHANNELS or type(row['attempts']) is not int or not 0<=row['attempts']<=3:raise ValueError()
                if any(not isinstance(s,str) or len(s)>100 for s in row['references']):raise ValueError()
                publication=row.get('publication')
                if publication and (publication['name']!=row['publication_name'] or publication['receipt']!=publication['name']+'.ready.json' or not re.fullmatch('[a-f0-9]{64}',publication['sha256']) or not math.isfinite(publication['duration']) or publication['duration']<=0):raise ValueError()
            self.validate(data)
            return data,hashlib.sha256(raw).hexdigest()
        except (ValueError,TypeError,KeyError,AttributeError,OSError,RecursionError):raise StudioError('journal_invalid_original_preserved') from None
    @staticmethod
    def validate(data):
        if not isinstance(data['channels'],dict) or data['runtime_id'] is not None and not re.fullmatch('[a-f0-9]{32}',data['runtime_id']):raise StudioError('invalid_runtime')
        def stamp(value):
            if value is not None and (not isinstance(value,str) or datetime.fromisoformat(value).tzinfo is None):raise StudioError('invalid_timestamp')
        def numeric(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
        for row in data['jobs'].values():
            fields={'id','kind','slot','status','profile','created_at','generation_started_at','generation_finished_at','generation_planned_at','publication_name','publication','references','attempts','reason','metrics','channels','events','run_id','resume_requested','verification'}
            if set(row)-fields or row['profile']!=row['kind'] or row['id']!=digest([row['kind'],'Europe/Paris',row['slot']])[:32]:raise StudioError('invalid_job')
            if row.get('verification'):
                if set(row['verification'])!={'at','valid'} or type(row['verification']['valid']) is not bool:raise StudioError('invalid_verification')
                stamp(row['verification']['at'])
            if row['run_id'] is not None and not re.fullmatch('[a-f0-9]{32}',row['run_id']):raise StudioError('invalid_run_id')
            if row['reason'] is not None and not re.fullmatch('[a-z_]{1,80}',row['reason']):raise StudioError('invalid_reason')
            if any(not re.fullmatch(r'(?:[a-f0-9]{32,64}|unknown)',v) for v in row['references']):raise StudioError('invalid_references')
            for field in ('created_at','generation_started_at','generation_finished_at','generation_planned_at'):stamp(row[field])
            if not isinstance(row['metrics'],dict) or set(row['metrics'])-{'words','segments','retries','tts_seconds','audio_seconds'} or any(not numeric(v) for v in row['metrics'].values()):raise StudioError('invalid_metrics')
            if not isinstance(row['channels'],dict) or not isinstance(row['events'],list):raise StudioError('invalid_channel_state')
            for event in row['events']:
                if not isinstance(event,dict) or set(event)-{'at','event','reason','channel','engine','voice','fallback'}:raise StudioError('invalid_event')
                stamp(event['at'])
                if not re.fullmatch('[a-z_]{1,80}',event['event']):raise StudioError('invalid_event')
                for key in ('reason','channel','engine','voice'):
                    if key in event and not re.fullmatch(r'[\w-]{1,100}',event[key]):raise StudioError('invalid_event_value')
            for value in row['channels'].values():
                if value.get('run_id') is not None and not re.fullmatch('[a-f0-9]{32}',value['run_id']):raise StudioError('invalid_run_id')
                if set(value)-{'status','updated_at','run_id','admitted_at','local_play_started_at','local_play_finished_at','bytes_local_pipe'} or value['status'] not in ('queued','playing','locally_sent','failed','interrupted'):raise StudioError('invalid_channel')
                for key in ('updated_at','admitted_at','local_play_started_at','local_play_finished_at'):
                    if key in value:stamp(value[key])
                if 'bytes_local_pipe' in value and not numeric(value['bytes_local_pipe']):raise StudioError('invalid_channel_metrics')
        for value in data['channels'].values():
            if not isinstance(value,dict) or set(value)-{'source_starts','reconnections','bytes_local_pipe','observed_at','event','icecast_connection_verified','audience_delivery_verified','last_local_bytes_at','epoch','bytes','delay'}:raise StudioError('invalid_transport')
            if value.get('icecast_connection_verified') is not None or value.get('audience_delivery_verified') is not None:raise StudioError('unsupported_delivery_claim')
            for key in ('source_starts','reconnections','bytes_local_pipe','epoch','bytes','delay'):
                if key in value and not numeric(value[key]):raise StudioError('invalid_transport_metric')
            for key in ('observed_at','last_local_bytes_at'):
                if key in value:stamp(value[key])
        for value in data['controls']:
            if not isinstance(value,dict) or set(value)!={'at','action','job'} or value['action'] not in ('enable','disable','reconcile','resume','verify'):raise StudioError('invalid_control_event')
            stamp(value['at'])

    def transaction(self,callback,expected=None):
        self.init()
        with lock(self.root/'journal.lock'):
            data,version=self.load()
            if expected is not None and expected!=version:raise StudioConflict('version_conflict')
            result=callback(data)
            self.validate(data)
            raw=json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
            if len(raw.encode())>4*1024*1024 or len(data['jobs'])>1024:raise StudioError('journal_quota')
            atomic_write(self.path,raw);return result
    @staticmethod
    def name(slot,identity,kind):
        if kind=='bulletin':stamp=datetime.strptime(slot,'%Y-%m-%dT%H:%M').strftime('%Y%m%d_%H%M00')
        else:
            match=re.fullmatch(r'manual:(\d{8}_\d{6}):[a-f0-9]{12}',slot)
            if not match:raise StudioError('invalid_manual_identity')
            stamp=match[1];datetime.strptime(stamp,'%Y%m%d_%H%M%S')
        return f'bulletin_{stamp}_{identity}.mp3'
    def event(self,row,kind,**values):
        row['events']=(row['events']+[{'at':self.now(),'event':kind,**values}])[-64:]
    def admit(self,slot,*,kind='bulletin',references=()):
        if not self.configured:raise StudioError('studio_not_configured')
        identity=digest([kind,'Europe/Paris',slot])[:32]
        name=self.name(slot,identity,kind)
        refs=[str(s) if re.fullmatch('[a-f0-9]{32,64}',str(s)) else 'unknown' for s in references][:100]
        def save(data):
            if identity in data['jobs']:return None
            if not data['enabled']:return None
            if len(data['jobs'])>=1024:raise StudioError('journal_quota_explicit_archive_required')
            busy=any(r['status']=='generating' for r in data['jobs'].values())
            row={'id':identity,'kind':kind,'slot':slot,'status':'skipped' if busy else 'admitted','profile':kind,'created_at':self.now(),'generation_started_at':None,'generation_finished_at':None,
                'generation_planned_at':datetime.fromtimestamp(datetime.strptime(slot,'%Y-%m-%dT%H:%M').replace(tzinfo=ZONE).timestamp()-300,ZONE).isoformat() if kind=='bulletin' else None,
                'publication_name':name,'publication':None,'references':refs,'attempts':0,'reason':'previous_generation_active' if busy else None,'metrics':{},'channels':{},'events':[],'run_id':self.run_id}
            self.event(row,'slot_skipped' if busy else 'admitted');data['jobs'][identity]=row;return row
        return self.transaction(save)
    def references(self,identity,articles):
        values=[str(a.get('hash')) if re.fullmatch('[a-f0-9]{32,64}',str(a.get('hash'))) else 'unknown' for a in articles][:100]
        self.transaction(lambda data:data['jobs'][identity].update(references=values))
    def start(self,identity):
        def save(data):
            row=data['jobs'][identity]
            if row['status'] not in ('admitted','interrupted','failed') or row['attempts']>=3 or row['publication']:raise StudioConflict('generation_not_allowed')
            row.update(status='generating',generation_started_at=self.now(),attempts=row['attempts']+1,run_id=self.run_id);self.event(row,'generation_started')
        self.transaction(save)
    def fail(self,identity,reason='generation_failed'):
        if reason not in ('generation_failed','interrupted','not_enough_articles','publication_invalid','admission_failed'):reason='generation_failed'
        def save(data):
            row=data['jobs'][identity];row.update(status='recovery_required' if row['publication'] else 'interrupted' if reason=='interrupted' else 'skipped' if reason=='not_enough_articles' else 'failed',reason=reason,generation_finished_at=row['generation_finished_at'] or self.now());self.event(row,reason)
        self.transaction(save)
    def metrics(self,identity,phase,**values):
        allowed={'words','segments','retries','tts_seconds','audio_seconds'}
        if set(values)-allowed or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values.values()):raise StudioError('invalid_metrics')
        def save(data):
            row=data['jobs'][identity];row['metrics'].update(values);self.event(row,phase)
        self.transaction(save)
    def tts_event(self,identity,phase,**values):
        if set(values)!={'engine','voice','fallback'} or not all(re.fullmatch(r'[\w-]{1,100}',values[k]) for k in ('engine','voice')) or type(values['fallback']) is not bool:raise StudioError('invalid_tts_observation')
        self.transaction(lambda data:self.event(data['jobs'][identity],phase,**values))
    def path_for(self,row,channel='primary'):
        name=row['publication_name'];path=checked(self.queue/('android_cache' if channel=='android' else '')/name)
        return path
    def publication(self,row):
        path=self.path_for(row)
        if not validated_publication(path):raise StudioError('publication_invalid')
        marker=decode(read(checked(receipt_path(path)),4096))
        return {'name':path.name,'receipt':path.name+'.ready.json','sha256':marker['sha256'],'duration':marker['duration'],'size':marker['size']}
    def attach(self,identity,path):
        def save(data):
            row=data['jobs'][identity]
            if checked(path)!=self.path_for(row):raise StudioError('publication_context_mismatch')
            publication=self.publication(row)
            row.update(publication=publication,status='ready',generation_finished_at=self.now(),reason=None)
            self.event(row,'publication_ready')
        self.transaction(save)
    def for_path(self,path):
        path=checked(path);data,_=self.load()
        return next((r for r in data['jobs'].values() if r['publication_name']==path.name and path in (self.path_for(r),self.path_for(r,'android'))),None)
    def channel_event(self,path,channel,phase,*,bytes_sent=0):
        if channel not in CHANNELS or phase not in ('queued','playing','locally_sent','failed','interrupted'):raise StudioError('invalid_channel_event')
        row=self.for_path(path)
        if not row or not row['publication']:raise StudioError('untracked_publication')
        if phase in ('queued','playing') and not validated_publication(path):raise StudioError('publication_invalid')
        def save(data):
            job=data['jobs'][row['id']];state=job['channels'].setdefault(channel,{})
            if phase=='queued' and state.get('status') in ('queued','playing','locally_sent'):return False
            state.update(status=phase,updated_at=self.now(),run_id=self.run_id)
            field={'queued':'admitted_at','playing':'local_play_started_at','locally_sent':'local_play_finished_at'}.get(phase)
            if field:state[field]=self.now()
            if phase=='locally_sent':state['bytes_local_pipe']=int(bytes_sent)
            if channel=='primary':job['status']='completed' if phase=='locally_sent' else 'recovery_required' if phase in ('failed','interrupted') else phase
            self.event(job,'channel_'+phase,channel=channel);return True
        return self.transaction(save)
    def observe(self,channel,phase,**values):
        if channel not in CHANNELS or phase not in ('ffmpeg_started','local_bytes','transport_failed','stopped'):raise StudioError('invalid_observation')
        allowed={'epoch','bytes','delay'}
        if set(values)-allowed or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values.values()):raise StudioError('invalid_observation')
        def save(data):
            info=data['channels'].setdefault(channel,{'reconnections':0,'bytes_local_pipe':0})
            info.update(observed_at=self.now(),event=phase,icecast_connection_verified=None,audience_delivery_verified=None)
            if phase=='ffmpeg_started':
                if info.get('source_starts',0)>0:info['reconnections']+=1
                info['source_starts']=info.get('source_starts',0)+1
            if phase=='local_bytes':info['bytes_local_pipe']+=values.get('bytes',0);info['last_local_bytes_at']=self.now()
            info.update(values)
        self.transaction(save)
    def reconcile(self,*,expected=None):
        def save(data):
            changed=[]
            for row in data['jobs'].values():
                if row['status']=='completed':continue
                old=row['status']
                if row['run_id']==self.run_id and row['status'] in ('generating','queued','playing') and self.run_id is not None:continue
                try:publication=self.publication(row)
                except (ValueError,OSError):publication=None
                if publication:
                    row['publication']=publication
                    if row['channels']:row['status']='recovery_required';row['reason']='playback_outcome_unknown'
                    else:row['status']='ready';row['reason']='recovered_unnotified_publication'
                elif old in ('generating','admitted'):row['status']='interrupted';row['reason']='generation_interrupted'
                elif row['publication']:row['status']='recovery_required';row['reason']='publication_missing_or_invalid'
                if row['status']!=old:self.event(row,'reconciled',reason=row['reason']);changed.append(row['id'])
            data['reconcile_requested']=False;return changed
        return self.transaction(save,expected)
    def control(self,action,expected,*,identity=None,ack=False):
        def save(data):
            recent=[c for c in data['controls'] if datetime.fromisoformat(c['at']).timestamp()>self.clock()-60]
            if len(recent)>=10:raise StudioConflict('control_rate_limit')
            if action in ('enable','disable'):data['enabled']=action=='enable'
            elif action=='reconcile':data['reconcile_requested']=True
            elif action=='verify':
                row=data['jobs'].get(identity)
                if not row:raise StudioError('job_not_found')
                try:valid=self.publication(row)==row['publication']
                except (ValueError,OSError):valid=False
                row['verification']={'at':self.now(),'valid':valid}
            elif action=='resume':
                row=data['jobs'].get(identity)
                if not row or row['kind']!='bulletin' or row['status'] not in ('ready','recovery_required') or ack is not True or not row['publication']:raise StudioConflict('explicit_valid_publication_resume_required')
                publication=self.publication(row)
                if publication!=row['publication']:raise StudioError('publication_changed')
                current=row['channels'].get('primary',{})
                if current.get('run_id')==data['runtime_id'] and current.get('status') in ('queued','playing'):raise StudioConflict('playback_still_active')
                row['channels'].pop('primary',None);row.update(status='ready',resume_requested=True,reason='operator_authorized_possible_replay');self.event(row,'resume_authorized')
            else:raise StudioError('invalid_control')
            data['controls']=(data['controls']+[{'at':self.now(),'action':action,'job':identity}])[-128:]
        if not self.configured:raise StudioError('studio_not_configured')
        with lease(self.root/'generation.lock'):return self.transaction(save,expected)
    def begin_run(self):
        self.run_id=uuid.uuid4().hex
        self.transaction(lambda data:data.update(runtime_id=self.run_id))
        return self.reconcile()
    def snapshot(self):
        data,version=self.load()
        rows=sorted(data['jobs'].values(),key=lambda r:r['created_at'],reverse=True)
        def elapsed(start,end):
            try:return max(0,(datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())
            except (TypeError,ValueError):return None
        generation=[elapsed(r['generation_started_at'],r['generation_finished_at']) for r in rows if r['publication']]
        generation=[v for v in generation if v is not None]
        def average(values):
            values=[v for v in values if v is not None];return sum(values)/len(values) if values else None
        metrics={'admitted':len(rows),'generated':sum(bool(r['publication']) for r in rows),'failed':sum(r['status']=='failed' for r in rows),'completed_local_pass':sum(r['status']=='completed' for r in rows),
            'generation_average_seconds':average(generation),'tts_seconds':sum(r['metrics'].get('tts_seconds',0) for r in rows),'segments':sum(r['metrics'].get('segments',0) for r in rows),'retries':sum(r['metrics'].get('retries',0) for r in rows),'audio_seconds':sum(r['publication']['duration'] for r in rows if r['publication']),
            'generation_to_primary_admission_average_seconds':average(elapsed(r['generation_finished_at'],r['channels'].get('primary',{}).get('admitted_at')) for r in rows),
            'primary_admission_to_local_play_average_seconds':average(elapsed(r['channels'].get('primary',{}).get('admitted_at'),r['channels'].get('primary',{}).get('local_play_started_at')) for r in rows)}
        return {'version':version,'configured':self.configured,'enabled':data['enabled'] if self.configured else False,'jobs':rows[:100],'channels':data['channels'],'reconcile_requested':data['reconcile_requested'],
                'metrics':metrics}


class RadioStudio:
    def __init__(self,journal,primary,android,stop_event):
        self.journal=journal;self.primary=primary;self.android=android;self.stop_event=stop_event
        primary.studio=journal;primary.studio_channel='primary';android.studio=journal;android.studio_channel='android'
        current=getattr(android,'_current',None)
        if current and validated_publication(current):
            try:journal.channel_event(current,'android','queued')
            except (ValueError,OSError):pass
    def enqueue(self,identity):
        row=self.journal.load()[0]['jobs'][identity];path=self.journal.path_for(row)
        if row['status'] not in ('ready','queued') or not validated_publication(path):return False
        # Never duplicate primary admission when Android succeeded but primary failed.
        android_ok=self.android.enqueue_bulletin(path)
        primary_ok=self.primary.enqueue_bulletin(path)
        if not primary_ok and row['status']=='ready':self.journal.fail(identity,'admission_failed')
        return primary_ok or android_ok
    def generate(self,slot,articles,builder,identity=None):
        try:
            with lease(self.journal.root/'generation.lock'):
                row=self.journal.load()[0]['jobs'].get(identity) if identity else self.journal.admit(slot,references=[a.get('hash','unknown') for a in articles])
                if row and row['status']!='admitted':return None
                if not row or row['status']=='skipped':return None
                identity=row['id']
                minimum=getattr(builder,'bulletins_cfg',{}).get('min_articles',3)
                if type(minimum) is int and len(articles)<minimum:self.journal.fail(identity,'not_enough_articles');return None
                if self.stop_event.is_set():self.journal.fail(identity,'interrupted');return None
                self.journal.references(identity,articles)
                self.journal.start(identity)
                try:
                    path=builder.build(articles,publication_name=row['publication_name'],observer=lambda phase,**values:(self.journal.tts_event if phase in ('tts_bridge_selected','tts_voice_planned') else self.journal.metrics)(identity,phase,**values),cancel_event=self.stop_event)
                    if not path:self.journal.fail(identity,'interrupted' if self.stop_event.is_set() else 'generation_failed');return None
                    self.journal.attach(identity,path);self.enqueue(identity);return path
                except Exception:self.journal.fail(identity);return None
        except StudioConflict:
            if identity:self.journal.fail(identity,'not_enough_articles')
            else:self.journal.admit(slot)
            return None
    def tick(self):
        data,_=self.journal.load()
        if data['reconcile_requested']:
            try:
                with lease(self.journal.root/'generation.lock'):self.journal.reconcile()
            except StudioConflict:pass
        for row in data['jobs'].values():
            if row.get('resume_requested') and row['status']=='ready':
                self.journal.transaction(lambda d:d['jobs'][row['id']].pop('resume_requested',None))
                with self.primary._bulletin_lock:self.primary._accepted_bulletins.pop(row['publication_name'],None)
                self.enqueue(row['id'])
