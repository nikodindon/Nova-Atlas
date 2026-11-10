"""Explicit shared-server qualification. No service control or production data writes."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timedelta
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo
import yaml
from deploy import editorial_prompt_trial as trial
from deploy.editorial_qualification import ExportError,read,sha,json_text
from modules.core.llm_client import OllamaClient
from modules.fetch.editorial_trace import sensitive
from modules.fetch import editorial as ed

SHARED_ENDPOINT='http://127.0.0.1:8080'
SERVICES=('news','radio','icecast','web','frontend','frontend-gateway')

class Deferred(RuntimeError):pass


def atomic(path,value):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.trial-')
    try:
        with os.fdopen(fd,'w') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(name):os.unlink(name)


def states():
    names=['atlas-lab-'+service+'-1' for service in SERVICES]
    rows=subprocess.check_output(['docker','inspect','--format','{{json .State}}',*names],text=True,timeout=5).splitlines()
    if len(rows)!=len(SERVICES):raise Deferred('container_state_unavailable')
    return dict(zip(SERVICES,[json.loads(row) for row in rows]))


def server_json(path):
    if path not in ('/health','/v1/models','/props','/slots'):raise Deferred('unexpected_probe')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),trial.NoRedirect())
    with opener.open(SHARED_ENDPOINT+path,timeout=5) as response:
        raw=response.read(262145)
        if len(raw)>262144:raise Deferred('probe_size_limit')
        return json.loads(raw)


def props_identity(props):
    # No chat templates, prompts, headers or reasoning content are archived.
    params=props.get('default_generation_settings',{}).get('params',{})
    params={k:v for k,v in params.items() if k not in ('generation_prompt',)}
    return {'model_alias':props.get('model_alias'),'total_slots':props.get('total_slots'),
            'build_info':props.get('build_info'),'n_ctx':props.get('default_generation_settings',{}).get('n_ctx'),'params':params}


def props_digest(props):
    return sha(json.dumps(props_identity(props),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())


def protected_radio_window(now,hours,call_seconds,margin_seconds):
    if not isinstance(hours,list) or not hours or any(type(h) is not int or not 0<=h<24 for h in hours):raise Deferred('invalid_radio_calendar')
    # Civil schedule; using timestamps preserves correct elapsed durations across DST.
    for offset in (-1,0,1):
        day=now.date()+timedelta(days=offset)
        for hour in set(hours):
            for minute in (0,30):
                for fold in (0,1):
                    target=datetime(day.year,day.month,day.day,hour,minute,tzinfo=now.tzinfo,fold=fold)
                    admission=target.timestamp()-300
                    if admission-margin_seconds-call_seconds<=now.timestamp()<=target.timestamp()+margin_seconds:return True
    return False


class Guard:
    def __init__(self,authorization):self.auth=authorization
    def check(self,*,during=False):
        a=self.auth;root=Path(a['state_root'])
        if sha(read(root/'config/bulletins.yaml',65536))!=a['radio_calendar_sha256']:raise Deferred('radio_calendar_changed')
        hours=yaml.safe_load(read(root/'config/bulletins.yaml',65536)).get('post_hours')
        if protected_radio_window(datetime.now(ZoneInfo('Europe/Paris')),hours,a['max_call_seconds'],a['radio_guard_seconds']):raise Deferred('radio_window_reserved')
        observed=states()
        if observed['news'].get('Running'):raise Deferred('news_running')
        for service in SERVICES:
            current=observed[service]
            if current.get('StartedAt')!=a['service_started_at'][service]:raise Deferred('service_restarted')
            if service!='news' and (not current.get('Running') or current.get('Health',{}).get('Status')!='healthy'):raise Deferred('service_unhealthy')
        pid=a['radio_worker_pid'];status=Path(f'/proc/{pid}/status').read_text()
        threads=int(next(line.split()[1] for line in status.splitlines() if line.startswith('Threads:')))
        if threads!=a['radio_idle_threads']:raise Deferred('radio_worker_busy')
        journal=json.loads(read(root/'data/radio_studio/journal.json',4*1024*1024))
        if any(r.get('status') in ('admitted','generating') for r in journal.get('jobs',{}).values()):raise Deferred('radio_job_active')
        statline=Path(f"/proc/{a['server_pid']}/stat").read_text().split(') ',1)[1].split()
        if int(statline[19])!=a['server_start_ticks']:raise Deferred('server_process_changed')
        info=Path(a['model']).stat()
        if {'inode':info.st_ino,'bytes':info.st_size,'mtime_ns':info.st_mtime_ns}!=a['weights_stat']:raise Deferred('model_file_changed')
        mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:',))}
        if mem['MemAvailable']<a['min_available_memory_bytes']:raise Deferred('insufficient_available_memory')
        if not during:
            if props_digest(server_json('/props'))!=a['server_props_sha256']:raise Deferred('server_parameters_changed')
            slots=server_json('/slots')
            if not isinstance(slots,list) or not slots or any(r.get('is_processing') is not False for r in slots):raise Deferred('server_busy_or_unknown')
        else:
            slots=server_json('/slots')
            if not isinstance(slots,list) or sum(r.get('is_processing') is True for r in slots)>1:raise Deferred('concurrent_provider_request')
        if (Path(a['output_root'])/'STOP').exists():raise Deferred('operator_stop')


@contextmanager
def shared_lease(path):
    """Lock the exact existing inode read-only; never create/change .lock or .meta."""
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise Deferred('unsafe_shared_lock')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);current=path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=os.geteuid() or (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):raise Deferred('unsafe_shared_lock')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Deferred('shared_llm_busy') from None
        yield
    finally:os.close(fd)


class WatchedResponse:
    def __init__(self,response,connection,stop,watcher,client):self.response=response;self.connection=connection;self.stop=stop;self.watcher=watcher;self.client=client;self.status=response.status
    def __enter__(self):return self
    def read(self,limit):
        raw=self.response.read(limit)
        if len(raw)<=4*1024*1024:
            try:
                data=json.loads(raw);timings=data.get('timings',{})
                self.client.server_timings={k:v for k,v in timings.items() if k in ('cache_n','prompt_n','prompt_ms','prompt_per_second','predicted_n','predicted_ms','predicted_per_second') and type(v) in (int,float) and math.isfinite(v) and v>=0}
                identity=data.get('id')
                if isinstance(identity,str) and len(identity)<=128 and not sensitive(identity) and all(c.isalnum() or c in '-_' for c in identity):self.client.provider_response_id=identity
                content=data.get('choices',[{}])[0].get('message',{}).get('content')
                if isinstance(content,str) and len(content)<=64000 and not ed.REASONING.search(content) and not sensitive(content):self.client.exact_output=content
            except (ValueError,TypeError,AttributeError,IndexError,KeyError):pass
        return raw
    def __exit__(self,*args):
        self.stop.set();self.response.close();self.connection.close();self.watcher.join()


class SharedClient(OllamaClient):
    def __init__(self,a,work,guard):
        self.authorization=a;self.guard=guard;self.request_sent=False;self.interruption=None;self.server_timings={};self.provider_response_id=None;self.exact_output=None;self.intent=None
        super().__init__({'paths':{'data_dir':str(work)},'llm':{'provider':'llama-server','base_url':SHARED_ENDPOINT,'model':a['model'],'cache':{'enabled':False},'lock_wait_seconds':{'fetch':0}}})
        if self.detected!=a['model']:raise Deferred('loaded_model_changed')
    def _detect_model_from_server(self):
        listing=server_json('/v1/models');self.detected=self.model if any(r.get('id')==self.model for r in listing.get('data',[])) else None
        return self.detected
    def _acquire_lock(self,*args,**kwargs):
        self._shared_context=shared_lease(Path(self.authorization['state_root'])/'data/ollama.lock')
        try:self._shared_context.__enter__();self.guard.check();return True
        except Exception as exc:
            if hasattr(self,'_shared_context'):self._shared_context.__exit__(None,None,None)
            self.interruption=str(exc) if isinstance(exc,Deferred) else type(exc).__name__;return False
    def _release_lock(self):self._shared_context.__exit__(None,None,None);return True
    def _open_http(self,request,*,timeout):
        if request.full_url!=SHARED_ENDPOINT+'/v1/chat/completions':raise Deferred('unexpected_generation_destination')
        self.guard.check();self.request_sent=False;self.interruption=None;self.server_timings={};self.provider_response_id=None;self.exact_output=None
        conn=http.client.HTTPConnection('127.0.0.1',8080,timeout=timeout);stop=threading.Event();holder={}
        deadline=time.monotonic()+timeout
        def watch():
            while not stop.wait(2):
                try:
                    if time.monotonic()>=deadline:raise Deferred('call_budget_elapsed')
                    self.guard.check(during=True)
                except Exception as exc:
                    self.interruption=str(exc) if isinstance(exc,Deferred) else type(exc).__name__
                    sock=holder.get('socket')
                    if sock is not None:
                        try:sock.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                    return
        watcher=threading.Thread(target=watch,name='shared-trial-watch',daemon=True);watcher.start()
        try:
            conn.connect();holder['socket']=conn.sock
            if self.interruption:raise Deferred(self.interruption)
            if self.intent:self.intent()
            self.request_sent=True
            conn.request('POST','/v1/chat/completions',body=request.data,headers=dict(request.header_items()))
            response=conn.getresponse()
            if response.status!=200:
                response.close();raise urllib.error.HTTPError(request.full_url,response.status,'shared_provider_http_error',{},None)
            return WatchedResponse(response,conn,stop,watcher,self)
        except BaseException:
            stop.set();conn.close();watcher.join();raise


def authorize(path):
    a=json.loads(read(path,65536))
    required={'schema','mode','approved','isolated_from_atlas_lab','endpoint','family','model','quantization','weights_sha256','runtime_version','hardware','reasoning_mode','server_parameters','max_total_seconds','max_call_seconds','radio_guard_seconds','cooldown_seconds','min_available_memory_bytes','state_root','output_root','radio_calendar_sha256','service_started_at','radio_worker_pid','radio_idle_threads','server_pid','server_start_ticks','server_props_sha256','weights_stat'}
    if not isinstance(a,dict) or set(a)!=required or a['schema']!=1:raise ExportError('invalid_shared_authorization')
    if a['mode']!='shared' or a['approved'] is not True or a['isolated_from_atlas_lab'] is not False or a['endpoint']!=SHARED_ENDPOINT:raise ExportError('explicit_shared_authorization_required')
    if a['family']!='Ornith-1.5-35B' or a['quantization']!='IQ2_M' or a['reasoning_mode'] not in ('off','on'):raise ExportError('exact_shared_model_required')
    for k in ('weights_sha256','radio_calendar_sha256','server_props_sha256'):
        if not isinstance(a[k],str) or len(a[k])!=64 or any(c not in '0123456789abcdef' for c in a[k]):raise ExportError('shared_digest_required')
    for k in ('model','runtime_version','hardware','state_root','output_root'):
        if not isinstance(a[k],str) or not a[k].strip():raise ExportError('shared_environment_required')
    for k,lo,hi in [('max_total_seconds',60,21600),('max_call_seconds',10,240),('radio_guard_seconds',300,3600),('cooldown_seconds',2,60),('radio_worker_pid',1,10**9),('radio_idle_threads',1,100),('server_pid',1,10**9),('server_start_ticks',1,10**18),('min_available_memory_bytes',1024**3,64*1024**3)]:
        if type(a[k]) is not int or not lo<=a[k]<=hi:raise ExportError('invalid_shared_budget')
    if not isinstance(a['server_parameters'],dict) or set(a['server_parameters'])!={'context_size','threads','gpu_layers'} or any(type(v) is not int for v in a['server_parameters'].values()):raise ExportError('server_parameters_required')
    if any(not Path(a[k]).is_absolute() for k in ('model','state_root','output_root')):raise ExportError('absolute_shared_paths_required')
    if not isinstance(a['service_started_at'],dict) or any(not isinstance(v,str) or not v for v in a['service_started_at'].values()):raise ExportError('container_baseline_required')
    if not isinstance(a['weights_stat'],dict) or set(a['weights_stat'])!={'inode','bytes','mtime_ns'} or any(type(v) is not int or v<=0 for v in a['weights_stat'].values()):raise ExportError('model_file_identity_required')
    if set(a['service_started_at'])!=set(SERVICES) or sensitive(a):raise ExportError('invalid_shared_environment')
    return a


def job_key(job):return job['case_id']+'::'+job['version']+'::'+str(job['repetition'])


def measure_shared(plan_path,authorization_path,output,*,execute=False,resume=False):
    if not execute:raise ExportError('explicit_execute_required')
    auth=authorize(authorization_path);raw,protocol,corpus,jobs=trial.validated_protocol(plan_path)
    output=Path(output).absolute()
    if output!=Path(auth['output_root']).absolute():raise ExportError('authorized_output_mismatch')
    if any(p.is_symlink() or (p/'.git').is_file() or (p/'.git/HEAD').is_file() for p in (output,*output.parents)):raise ExportError('private_shared_output_required')
    state=Path(auth['state_root']).absolute()
    if output.is_relative_to(state) or state.is_relative_to(output) or output.is_relative_to(Path.home()/'atlas-lab/Nova-Atlas'):raise ExportError('production_output_refused')
    if not output.exists():output.mkdir(mode=0o700)
    if output.stat().st_uid!=os.geteuid() or output.stat().st_mode&0o077:raise ExportError('private_shared_output_required')
    lockpath=output/'runner.lock';fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        checkpoint=output/'checkpoint.json'
        if checkpoint.exists():
            if not resume:raise ExportError('explicit_resume_required')
            progress=json.loads(read(checkpoint))
            if progress['plan_sha256']!=sha(raw) or progress['authorization_sha256']!=sha(read(authorization_path)):raise ExportError('resume_inputs_changed')
        else:progress={'schema':1,'plan_sha256':sha(raw),'authorization_sha256':sha(read(authorization_path)),'started_at':time.time(),'attempts':{},'deferrals':[]}
        deadline=progress['started_at']+auth['max_total_seconds'];guard=Guard(auth);reason=None
        with tempfile.TemporaryDirectory(prefix='nova-shared-trial-') as work:
            client=SharedClient(auth,Path(work),guard)
            for job in jobs:
                key=job_key(job)
                if key in progress['attempts']:
                    old=progress['attempts'][key]
                    if old.get('state')=='in_flight':
                        receipt={'request_id':old['request_id'],'kind':'interrupted_outcome_unknown','transport_attempted':True}
                        row=trial.assess_output(job,'',receipt);row['status']='interrupted_outcome_unknown';old.update(state='finished',result=row)
                    continue # Never silently repeat a started generation, including timeouts.
                remaining=deadline-time.time()
                if remaining<=0:reason='global_budget_elapsed';break
                try:guard.check()
                except (Deferred,OSError,ValueError) as exc:reason=str(exc) if isinstance(exc,Deferred) else type(exc).__name__;break
                identity=uuid.uuid4().hex;receipt={};client.request_sent=False;client.interruption=None
                def intent():
                    progress['attempts'][key]={'state':'in_flight','request_id':identity,'started_at':time.time()};atomic(checkpoint,progress)
                client.intent=intent
                try:text=client.call(job['prompt'],caller='fetch',timeout=min(auth['max_call_seconds'],remaining),use_cache=False,request_id=identity,metrics=receipt)
                except KeyboardInterrupt:text='';reason='operator_interrupted'
                receipt.update(server_timings=client.server_timings,provider_response_id=client.provider_response_id,interruption=client.interruption)
                if not client.request_sent:
                    reason=client.interruption or reason or 'admission_deferred';break
                row=trial.assess_output(job,text,receipt)
                row['output_exact']=client.exact_output
                row['exact_output_sha256']=sha(client.exact_output.encode()) if client.exact_output is not None else None
                if client.interruption:
                    row['coexistence_event']=client.interruption
                    if receipt.get('kind')!='success':row.update(status='timeout' if client.interruption=='call_budget_elapsed' else 'interrupted_for_radio_or_safety',error=client.interruption)
                progress['attempts'][key].update(state='finished',finished_at=time.time(),result=row)
                progress['status']='running';progress['updated_at']=time.time();atomic(checkpoint,progress)
                print(json.dumps({'attempts':len(progress['attempts']),'case':job['case_id'],'version':job['version'],'status':row['status'],'request_id':identity}),flush=True)
                if client.interruption or reason:reason=client.interruption or reason;break
                time.sleep(auth['cooldown_seconds']) # Lock has already been released by call().
        if reason:progress['deferrals'].append({'at':time.time(),'reason':reason})
        results=[entry['result'] for entry in progress['attempts'].values() if entry.get('state')=='finished']
        progress.update(status='complete' if len(results)==len(jobs) else 'partial',updated_at=time.time());atomic(checkpoint,progress)
        reports=output/'reports';reports.mkdir(mode=0o700,exist_ok=True)
        reportdir=reports/('snapshot-'+uuid.uuid4().hex)
        summary=trial.export_measurements(raw,protocol,corpus,jobs,auth,results,reportdir,plan_path,authorization_path,reason is not None)
        atomic(output/'latest-report.json',{'path':str(reportdir),'status':progress['status'],'attempts':len(results),'reason':reason,'shared':True})
        return {**summary,'environment':'shared_not_isolated','reason':reason,'report_path':str(reportdir)}
    finally:os.close(fd)
