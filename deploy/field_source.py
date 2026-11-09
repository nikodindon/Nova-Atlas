"""Explicit synthetic-only source for a new, authorized isolated Compose state.
Plan by default. Never starts main.py, News, an LLM or TTS. Not a qualification.
"""
import argparse,json,math,os,re,signal,subprocess,sys,tempfile,threading,time,uuid
from datetime import datetime,timezone
from pathlib import Path


def safe(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('unsafe_field_path')
    return path

def initialize(state):
    state=safe(state)
    for relative in ('config','config/config.yaml','config/feeds.yaml','data/articles','audio_queue','music'):safe(state/relative)
    if not (state/'config/config.yaml').is_file() or (state/'config/feeds.yaml').read_text().strip()!='{}':raise ValueError('fresh_managed_state_required')
    if any((state/'data/articles').iterdir()) or any(p.is_file() for p in (state/'audio_queue').rglob('*')) or any((state/'music').iterdir()):raise ValueError('no_existing_media_or_articles_allowed')
    marker={'version':1,'run_id':uuid.uuid4().hex,'mode':'synthetic-only','created_at':datetime.now(timezone.utc).isoformat()}
    path=state/'config/field-run.json';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(marker,stream);stream.flush();os.fsync(stream.fileno())
    return marker['run_id']

def authorize(marker,run_id,duration,interval,ack,reserved,long_ack):
    if not isinstance(marker,dict) or set(marker)!={'version','run_id','mode','created_at'} or marker['version']!=1 or marker['mode']!='synthetic-only':raise ValueError('field_marker_required')
    if not isinstance(run_id,str) or not re.fullmatch('[a-f0-9]{32}',run_id) or run_id!=marker['run_id']:raise ValueError('run_id_mismatch')
    if not ack or not reserved or not 1<=duration<=86400 or not 300<=interval<=3600 or duration>60 and not long_ack:raise ValueError('explicit_authorization_and_resources_required')

def execute(run_id,duration,interval,ack,reserved,long_ack):
    root=Path('/app')
    if Path.cwd()!=root or os.getuid()==0:raise ValueError('nonroot_managed_test_container_only')
    for relative in ('config','data','audio_queue','music'):safe(root/relative)
    marker_path=safe(root/'config/field-run.json')
    if marker_path.stat().st_size>4096 or marker_path.stat().st_nlink!=1:raise ValueError('invalid_marker_file')
    marker=json.loads(marker_path.read_text())
    authorize(marker,run_id,duration,interval,ack,reserved,long_ack)
    if any(p.is_symlink() or not p.is_file() or p.name!=f'field-tone-{run_id}.mp3' for p in (root/'music').iterdir()):raise ValueError('synthetic_music_only')
    sys.path.insert(0,str(root))  # trusted read-only code root for a /field_source.py bind mount
    from deploy.runtime import read_secret
    from modules.radio.studio import StudioJournal,RadioStudio,lease
    from modules.radio.streamer import Streamer,AndroidStreamer
    from modules.radio.audio_integrity import inspect_audio,publish_audio
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    queue=root/'audio_queue';journal=StudioJournal(root/'data',queue)
    if len(journal.load()[0]['jobs'])+math.ceil(duration/interval)>900:raise ValueError('archive_review_required_before_campaign')
    with lease(journal.root/'runtime.lock'):
        journal.begin_run()
        cfg={'paths':{'audio_queue':str(queue),'music':str(root/'music')},'radio':{'bitrate':'128k','sample_rate':44100,'channels':2},
             'icecast':{'host':'icecast','port':8000,'user':'source','password':read_secret('icecast_source_password'),'mount':'/nova','android_mount':'/nova-android'}}
        primary=Streamer(cfg);android=AndroidStreamer(cfg);studio=RadioStudio(journal,primary,android,stop)
        threads=[threading.Thread(target=s.run,daemon=True) for s in (primary,android)]
        jobs=0;started=time.monotonic()
        try:
            for thread in threads:thread.start()
            while not stop.is_set() and time.monotonic()-started<duration:
                stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S');slot=f'manual:{stamp}:{uuid.uuid4().hex[:12]}'
                row=journal.admit(slot,kind='flash')
                if not row or row['status']=='skipped':raise ValueError('test_admission_refused')
                journal.start(row['id'])
                with tempfile.TemporaryDirectory(prefix='.field-tone-',dir=queue) as stage:
                    path=Path(stage)/'tone.mp3'
                    subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i','sine=frequency=440:duration=6','-ar','44100','-ac','2','-c:a','libmp3lame','-b:a','128k',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=20)
                    if stop.is_set():journal.fail(row['id'],'interrupted');break
                    info=inspect_audio(path);published=queue/row['publication_name'];publish_audio(path,published,info,1)
                    music=root/'music'/f'field-tone-{run_id}.mp3'
                    if not music.exists():
                        with music.open('xb') as stream:stream.write(published.read_bytes())
                        music.chmod(0o600)
                    journal.attach(row['id'],published)
                    if not studio.enqueue(row['id']):raise ValueError('test_queue_refused')
                    jobs+=1
                stop.wait(min(interval,max(0,duration-(time.monotonic()-started))))
        finally:
            stop.set()
            for streamer in (primary,android):streamer.stop()
            for thread in threads:thread.join(timeout=12)
            for streamer in (primary,android):streamer._kill_ffmpeg()
            for thread in threads:thread.join(timeout=6)
            if any(t.is_alive() for t in threads):raise ValueError('test_source_shutdown_incomplete')
    return {'status':'synthetic_source_stopped','jobs_admitted':jobs,'elapsed_seconds':time.monotonic()-started,'audience_delivery':'NOT_TESTED','radio_24h_qualified':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--initialize');p.add_argument('--execute',action='store_true');p.add_argument('--run-id');p.add_argument('--duration',type=int,default=30);p.add_argument('--interval',type=int,default=300);p.add_argument('--authorize-test-source',action='store_true');p.add_argument('--resources-reserved',action='store_true');p.add_argument('--authorize-long-run',action='store_true');a=p.parse_args()
    try:
        if a.initialize:
            print(json.dumps({'status':'marked_not_started','run_id':initialize(a.initialize)}));return
        if not a.execute:print(json.dumps({'status':'PLAN_ONLY','services_started':False}));return
        print(json.dumps(execute(a.run_id,a.duration,a.interval,a.authorize_test_source,a.resources_reserved,a.authorize_long_run)))
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'status':'FAIL','reason':'synthetic_source_refused_or_failed'}));raise SystemExit(1)
if __name__=='__main__':main()
