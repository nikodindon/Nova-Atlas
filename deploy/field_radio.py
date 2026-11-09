"""Operator-only bounded mount sampler. Never starts Icecast, FFmpeg sources or jobs."""
import argparse,hashlib,ipaddress,json,os,signal,subprocess,sys,tempfile,threading,time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from deploy.field_metrics import run
from deploy.qualification import read


def endpoint(url,loopback_http=False):
    if not isinstance(url,str) or len(url)>2048 or any(c.isspace() for c in url) or '\\' in url:raise ValueError('fixed_mount_required')
    p=urlsplit(url)
    if not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment or p.scheme not in ('http','https'):raise ValueError('fixed_mount_required')
    if p.scheme=='http':
        try:local=ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:local=False
        if not loopback_http or not local:raise ValueError('https_or_explicit_literal_loopback_required')
    if p.port is not None and not 1<=p.port<=65535:raise ValueError('invalid_port')
    return url

def decode_sample(raw,*,runner=subprocess.run):
    if not 1024<=len(raw)<=65536:return 'FAIL'
    try:
        with tempfile.TemporaryDirectory(prefix='atlas-radio-sample-') as tmp:
            path=Path(tmp)/'sample.audio';path.write_bytes(raw)
            result=runner(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-f','mp3','-i',str(path),'-map','0:a:0','-t','0.25','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
            return 'PASS' if result.returncode==0 else 'FAIL'
    except (OSError,subprocess.TimeoutExpired):return 'NOT_TESTED'

def sample(url,*,loopback_http=False,session_factory=requests.Session,decoder=decode_sample,clock=time.monotonic):
    endpoint(url,loopback_http);started=clock()
    result={'mount':'FAIL','client_bytes':0,'sample_decode':'NOT_TESTED','audience_heard':'NOT_TESTED','ffmpeg_source_process':'NOT_TESTED','local_pipe':'NOT_TESTED','retry_after_seconds':None}
    try:
        with session_factory() as session:
            session.trust_env=False
            with session.get(url,stream=True,allow_redirects=False,timeout=(2,3),headers={'Accept':'audio/mpeg'}) as response:
                result['http_status']=response.status_code
                if response.status_code==429:
                    # Reuse a bounded parsed delay, never publish raw headers.
                    value=response.headers.get('Retry-After','')
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime,timezone
                    try:delay=int(value)
                    except (ValueError,TypeError):
                        try:delay=max(0,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
                        except (ValueError,TypeError,OverflowError):delay=60
                    result['retry_after_seconds']=max(1,min(delay,86401));result['embargo_exceeds_maximum_campaign']=delay>86400;return result
                if response.status_code!=200:return result
                result['mount']='PASS'
                if not response.headers.get('Content-Type','').lower().startswith(('audio/mpeg','audio/mp3')):return result
                body=bytearray()
                for chunk in response.iter_content(8192):
                    if clock()-started>4:break
                    body.extend(chunk[:65536-len(body)])
                    if len(body)>=8192:break # short samples, not complete-publication validation
                result['client_bytes']=len(body)
                if body:result['sample_decode']=decoder(bytes(body))
    except (requests.RequestException,OSError,ValueError):result['mount']='FAIL'
    finally:result['elapsed_seconds']=max(0,clock()-started)
    return result

def worker_run(command,**kwargs):
    if os.name!='posix':raise ValueError('posix_worker_required')
    timeout=kwargs.pop('timeout');body=kwargs.pop('input')
    process=subprocess.Popen(command,start_new_session=True,**kwargs)
    try:
        out,_=process.communicate(body,timeout=timeout)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.communicate(timeout=3)
        raise
    return subprocess.CompletedProcess(command,process.returncode,out)

def bounded(url,loopback_http=False,*,runner=worker_run):
    endpoint(url,loopback_http)
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG')}
    try:
        completed=runner([sys.executable,'-m','deploy.field_radio','--worker'],input=json.dumps({'url':url,'loopback_http':loopback_http}),text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,env=env,cwd=Path(__file__).resolve().parents[1])
        if completed.returncode or len(completed.stdout)>8192:raise ValueError()
        value=json.loads(completed.stdout)
        if not isinstance(value,dict) or value.get('mount') not in ('PASS','FAIL'):raise ValueError()
        return value
    except (OSError,ValueError,subprocess.TimeoutExpired):return {'mount':'FAIL','client_bytes':0,'sample_decode':'NOT_TESTED','audience_heard':'NOT_TESTED','reason':'bounded_worker_unavailable'}

def collector(mounts,*,loopback_http=False,probe=bounded,clock=time.monotonic):
    if not isinstance(mounts,dict) or not mounts or set(mounts)-{'primary','android'}:raise ValueError('named_mounts_required')
    for url in mounts.values():endpoint(url,loopback_http)
    next_allowed={}
    def collect():
        rows={}
        for name,url in mounts.items():
            if clock()<next_allowed.get(name,0):rows[name]={'mount':'NOT_TESTED','reason':'provider_embargo'};continue
            rows[name]=probe(url,loopback_http)
            delay=rows[name].get('retry_after_seconds')
            if isinstance(delay,int) and delay>0:next_allowed[name]=clock()+delay
        return {'channels':rows,'endpoint_fingerprints':{name:hashlib.sha256(url.encode()).hexdigest() for name,url in mounts.items()},'continuity':'sampled_not_continuous','radio_uptime':'NOT_TESTED'}
    return collect

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);p.add_argument('--mounts-file');p.add_argument('--output');p.add_argument('--allow-loopback-http',action='store_true');p.add_argument('--duration',type=int,default=0);p.add_argument('--interval',type=int,default=60);p.add_argument('--authorize-long-run',action='store_true');p.add_argument('--resources-reserved',action='store_true');a=p.parse_args()
    try:
        if a.worker:
            data=json.loads(sys.stdin.read(4097));print(json.dumps(sample(data['url'],loopback_http=data['loopback_http'])));return
        if not a.mounts_file or not a.output or a.interval<30:raise ValueError('explicit_paths_and_conservative_interval_required')
        mounts,_=read(a.mounts_file);stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
        result=run(collector(mounts,loopback_http=a.allow_loopback_http),a.output,duration=a.duration,interval=a.interval,long_authorized=a.authorize_long_run,resources_reserved=a.resources_reserved,stop=stop)
    except (OSError,ValueError,TypeError,KeyError):result={'status':'FAIL','reason':'field_probe_refused_or_unavailable'}
    print(json.dumps(result));raise SystemExit(1 if result.get('status')=='FAIL' else 0)
if __name__=='__main__':main()
