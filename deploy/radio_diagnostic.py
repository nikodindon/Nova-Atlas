"""Bounded operator probe. Journal events, local pipes and received bytes are distinct."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import requests
from modules.radio.studio import StudioJournal


def observations(channels,*,now=None):
    now=now or datetime.now(timezone.utc);result={}
    for name in ('primary','android'):
        value=channels.get(name,{})
        try:age=max(0,(now-datetime.fromisoformat(value['observed_at'])).total_seconds())
        except (ValueError,TypeError,KeyError):age=None
        fresh=age is not None and age<=60
        result[name]={'observation_age_seconds':age,'ffmpeg_start_event':{'status':'pass' if fresh and value.get('source_starts',0)>0 else 'warning' if age is not None else 'not_tested','reason':'journal_event_not_live_process_probe'},
            'local_pipe_bytes':{'status':'pass' if fresh and value.get('bytes_local_pipe',0)>0 else 'not_tested','bytes':value.get('bytes_local_pipe',0)},
            'icecast_mount':{'status':'not_tested'},'client_audio_bytes':{'status':'not_tested'},'audience_delivery':{'status':'not_tested'}}
    return result


def probe(url,*,session_factory=requests.Session):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in url) or '\\' in url:raise ValueError('fixed_operator_mount_required')
    result={'icecast_mount':{'status':'fail'},'client_audio_bytes':{'status':'not_tested'},'audio_decode':{'status':'not_tested'},'audience_delivery':{'status':'not_tested'}}
    # Intentionally separate operator-only internal mount transport, no web endpoint.
    try:
        with session_factory() as session:
            session.trust_env=False
            with session.get(url,stream=True,allow_redirects=False,timeout=(2,3),headers={'Accept':'audio/mpeg,audio/ogg'}) as response:
                if response.status_code!=200:return result
                result['icecast_mount']={'status':'pass','http_status':200}
                if not response.headers.get('Content-Type','').lower().startswith(('audio/','application/ogg')):return result
                chunk=next(response.iter_content(chunk_size=1),b'')
                result['client_audio_bytes']={'status':'pass' if chunk else 'fail','bytes':len(chunk)}
    except (requests.RequestException,OSError,ValueError):result['icecast_mount']={'status':'fail','reason':'bounded_probe_unavailable'}
    return result


def bounded_probe(url):
    # Bound even DNS/header slowloris in a disposable child; never kill a streamer.
    env={key:value for key,value in os.environ.items() if key in ('PATH','LANG')}
    try:
        result=subprocess.run([sys.executable,'-m','deploy.radio_diagnostic','--probe-worker'],input=url+'\n',text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            timeout=6,env=env,cwd=Path(__file__).resolve().parents[1])
        if result.returncode or len(result.stdout)>4096:raise ValueError()
        return json.loads(result.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):return {'status':'fail','reason':'bounded_probe_unavailable','audience_delivery':{'status':'not_tested'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data');parser.add_argument('--queue');parser.add_argument('--probe-mount',help='Explicit operator fixed origin, never accepted by an HTTP route');parser.add_argument('--probe-worker',action='store_true',help=argparse.SUPPRESS);args=parser.parse_args()
    try:
        if args.probe_worker:
            print(json.dumps(probe(sys.stdin.readline(4097).strip()),sort_keys=True));return
        if not args.data or not args.queue:raise ValueError('explicit_paths_required')
        data,_=StudioJournal(args.data,args.queue).load();report={'channels':observations(data['channels'])}
        if args.probe_mount:report['explicit_probe']=bounded_probe(args.probe_mount)
    except (ValueError,OSError):report={'status':'fail','reason':'diagnostic_refused'}
    print(json.dumps(report,sort_keys=True))
    if report.get('status')=='fail' or args.probe_mount and (report.get('explicit_probe',{}).get('icecast_mount',{}).get('status')!='pass' or report.get('explicit_probe',{}).get('client_audio_bytes',{}).get('status')!='pass'):raise SystemExit(1)

if __name__=='__main__':main()
