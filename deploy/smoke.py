"""Disposable GitHub CI only: builds images, runs Web/Icecast, never News/Radio engines."""
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid
from deploy.init import initialize

ROOT=Path(__file__).resolve().parents[1]
_REDACTIONS=[]


def diagnostic(output):
    for value in _REDACTIONS:
        output=output.replace(value,'[redacted]')
    output=re.sub(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])','[redacted]',output)
    print(output[-12000:],flush=True)


def run(command,*,input=None,check=True):
    result=subprocess.run(command,cwd=ROOT,input=input,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900)
    if check and result.returncode:
        diagnostic(result.stdout)
        raise RuntimeError('Isolated Docker smoke command failed')
    return result


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Smoke is restricted to the disposable GitHub runner')
    with tempfile.TemporaryDirectory(prefix='atlas-compose-smoke-') as temporary:
        state=Path(temporary)/'state'
        initialize(state,'synthetic-ci-admin',secrets.token_urlsafe(32))
        _REDACTIONS[:]=[p.read_text().strip() for p in (state/'secrets').iterdir() if p.is_file()]
        with (state/'compose.env').open('a') as out:out.write('ATLAS_WEB_PORT=0\n')
        command=['docker','compose','-p','atlas-ci-'+uuid.uuid4().hex[:10],'-f',str(ROOT/'docker-compose.yml'),'--env-file',str(state/'compose.env'),'--profile','full']
        app_image='nova-atlas:02-foundation'
        try:
            run(command+['config','--quiet'])
            print('Smoke: building images',flush=True)
            run(command+['build','web','icecast'])
            # A sensitive entrypoint cannot fall back to example credentials.
            denied=run(['docker','run','--rm',app_image,'web'],check=False)
            assert denied.returncode==78
            print('Smoke: starting synthetic Web and Icecast',flush=True)
            run(command+['up','-d','--wait','--wait-timeout','120','web','icecast'])
            run(command+['exec','-T','web','python','-m','deploy.healthcheck','web'])
            code="""import os,subprocess
from pathlib import Path
assert os.getuid()!=0
from zoneinfo import ZoneInfo
from datetime import datetime
assert datetime(2026,1,1,tzinfo=ZoneInfo('Europe/Paris')).utcoffset().total_seconds()==3600
assert datetime(2026,7,1,tzinfo=ZoneInfo('Europe/Paris')).utcoffset().total_seconds()==7200
for binary in ('ffmpeg','ffprobe'):
 subprocess.run([binary,'-version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
Path('/app/data/ci-persistence').write_text('synthetic persistent data')
os.chmod('/app/data/ci-persistence',0o600)
assert not Path('/app/NOVA_ATLAS_V2_PLAN.md').exists()
assert not Path('/app/.env.admin').exists()
"""
            run(command+['exec','-T','web','python','-c',code])
            # Same private file is readable from the News identity, without starting News.
            run(command+['run','--rm','--no-deps','--entrypoint','python','news','-c',"from pathlib import Path;assert Path('/app/data/ci-persistence').read_text()=='synthetic persistent data'"])
            holder_code="""from pathlib import Path
import time
from modules.core.llm_lock import LocalLLMLock
lock=LocalLLMLock(Path('/app/data/ollama.lock'))
assert lock.acquire('fetch',1)
Path('/app/data/ci-lock-ready').touch()
time.sleep(10)
lock.release()
"""
            print('Smoke: shared private files and cross-container flock',flush=True)
            holder=subprocess.Popen(command+['run','--rm','--no-deps','--entrypoint','python','news','-c',holder_code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                deadline=time.monotonic()+20
                while not (state/'data/ci-lock-ready').exists() and time.monotonic()<deadline:time.sleep(.1)
                assert (state/'data/ci-lock-ready').exists()
                contender="from pathlib import Path;from modules.core.llm_lock import LocalLLMLock;lock=LocalLLMLock(Path('/app/data/ollama.lock'));assert not lock.acquire('bulletin',.1)"
                run(command+['run','--rm','--no-deps','--entrypoint','python','radio','-c',contender])
                assert holder.wait(timeout=20)==0
            finally:
                if holder.poll() is None:holder.terminate();holder.wait(timeout=15)
            # Actual synthetic MP3, no TTS or streamer. Verify private receipts across roles.
            audio="""import subprocess,uuid
from pathlib import Path
from modules.radio.audio_integrity import inspect_audio,publish_audio
work=Path('/app/audio_queue/.ci-synthetic');work.mkdir()
source=work/'sound.mp3'
subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=0.3','-c:a','libmp3lame',str(source)],check=True)
target=Path('/app/audio_queue')/('bulletin_20261104_120000_'+uuid.uuid4().hex+'.mp3')
publish_audio(source,target,inspect_audio(source),1)
"""
            run(command+['exec','-T','web','python','-c',audio])
            run(command+['run','--rm','--no-deps','--entrypoint','python','radio','-c',"from pathlib import Path;from modules.radio.audio_integrity import validated_publication;assert all(validated_publication(p) for p in Path('/app/audio_queue').glob('bulletin_*.mp3'))"])
            from deploy.radio_probe_smoke import exercise
            exercise(command,run)
            print('Smoke: atomic audio and cooperative restart',flush=True)
            inode=(state/'data/service-web.lock').stat().st_ino
            run(command+['stop','web'])
            container=run(command+['ps','-aq','web']).stdout.strip()
            assert run(['docker','inspect','--format','{{.State.ExitCode}}',container]).stdout.strip()=='0'
            run(command+['stop','icecast'])
            from deploy.recovery import snapshot,restore
            snapshot(state,Path(temporary)/'snapshot',apply=True,stopped=True,include_secrets=True)
            restored=Path(temporary)/'restored';restore(Path(temporary)/'snapshot',restored,apply=True)
            verify_audio="from pathlib import Path;from modules.radio.audio_integrity import validated_publication,inspect_audio;files=list(Path('/verify').glob('bulletin_*.mp3'));assert files;assert all(validated_publication(p) and inspect_audio(p).duration>0 for p in files);print('RC real MP3 restore PASS: receipt hashes and full decoding in isolated read-only container')"
            print(run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,noexec,nosuid,size=32m,mode=1777','--volume',str(restored/'audio_queue')+':/verify:ro','--entrypoint','python',app_image,'-c',verify_audio]).stdout,flush=True)
            run(command+['up','-d','--wait','--wait-timeout','120','web','icecast'])
            assert (state/'data/ci-persistence').read_text()=='synthetic persistent data'
            assert (state/'data/service-web.lock').stat().st_ino==inode
            logs=run(command+['logs','--no-color','web','icecast']).stdout
            for name in ('admin_password_hash','session_secret','icecast_source_password'):
                assert (state/'secrets'/name).read_text().strip() not in logs
            print('Docker smoke PASS: builds, non-root binaries, closed secrets, health, shared locks/audio, persistence and SIGTERM. No engines started.')
        except BaseException:
            diagnostic(run(command+['logs','--no-color','--tail','60','web','icecast'],check=False).stdout)
            raise
        finally:
            run(command+['down','--remove-orphans'],check=False)


if __name__=='__main__':main()
