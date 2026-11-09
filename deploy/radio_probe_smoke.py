"""CI-owned Icecast only: short sine waves, no TTS, music, scheduler or bulletin."""
import os

CODE=r'''
import subprocess,time,json
from pathlib import Path
import requests
secret=Path('/run/secrets/icecast_source_password').read_text().strip()
assert len(secret)==64
measurements=[]
for mount in ('nova','nova-android'):
 command=['ffmpeg','-v','error','-nostdin','-re','-f','lavfi','-i','sine=frequency=440:duration=8','-c:a','libmp3lame','-b:a','128k','-content_type','audio/mpeg','-f','mp3','icecast://source:'+secret+'@icecast:8000/'+mount]
 process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  deadline=time.monotonic()+10;sample=None
  with requests.Session() as session:
   session.trust_env=False
   while time.monotonic()<deadline and process.poll() is None:
    try:
     with session.get('http://icecast:8000/'+mount,stream=True,allow_redirects=False,timeout=(1,2)) as response:
      if response.status_code==200 and response.headers.get('Content-Type','').startswith('audio/'):
       sample=next(response.iter_content(chunk_size=8192),b'')
       if len(sample)>=4096:break
    except requests.RequestException:pass
    time.sleep(.1)
  assert sample and len(sample)>=4096,'synthetic source not received'
  subprocess.run(['ffmpeg','-v','error','-nostdin','-f','mp3','-i','pipe:0','-f','null','-'],input=sample,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=5)
  measurements.append({'channel':mount,'source_started':True,'http_status':200,'client_bytes_received':len(sample),'sample_decoded':True,'human_listening':'not_tested'})
 finally:
  if process.poll() is None:process.terminate()
  try:process.wait(timeout=3)
  except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
print('RC isolated Icecast samples PASS '+json.dumps(measurements))
'''


def exercise(command,run,field_run_id=None):
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Disposable CI only')
    print(run(command+['exec','-T','web','python','-c',CODE]).stdout,flush=True)
    run(command+['restart','icecast'])
    run(command+['up','-d','--wait','--wait-timeout','60','icecast'])
    print(run(command+['exec','-T','web','python','-c',CODE]).stdout,flush=True)
    print('RC Icecast restart PASS: both isolated mounts received and decoded before/after restart; no claim of 24h continuity or audience delivery.',flush=True)

    if field_run_id:
        from pathlib import Path
        source=(Path(__file__).with_name('field_source.py')).read_text()
        code="""
import json,subprocess,sys,time,tempfile
from pathlib import Path
import requests
source=SOURCE
args=['field-source','--execute','--run-id',RUN_ID,'--duration','10','--authorize-test-source','--resources-reserved']
temporary=tempfile.TemporaryDirectory(prefix='field-source-ci-')
program=Path(temporary.name)/'field_source.py';program.write_text(source)
process=subprocess.Popen([sys.executable,str(program),*args[1:]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
try:
 with requests.Session() as session:
  session.trust_env=False
  for mount in ('nova','nova-android'):
   okay=False;deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    try:
     with session.get('http://127.0.0.1:5055/stream/'+mount,stream=True,allow_redirects=False,timeout=(1,2)) as response:
      if response.status_code==200:
       raw=next(response.iter_content(8192),b'')
       if len(raw)>=4096:okay=True;break
    except requests.RequestException:pass
    time.sleep(.1)
   assert okay,'synthetic fixture receiver unavailable'
 out,_=process.communicate(timeout=30)
 assert process.returncode==0 and json.loads(out)['jobs_admitted']==1
 from modules.radio.studio import StudioJournal
 from pathlib import Path
 journal=StudioJournal(Path('/app/data'),Path('/app/audio_queue'))
 assert len(journal.load()[0]['jobs'])==1
 print('Field source smoke PASS: synthetic publication/receipt, both live test mounts, durable Studio job, bounded source shutdown; not a field qualification')
finally:
 if process.poll() is None:
  process.terminate()
  try:process.wait(timeout=25)
  except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
 temporary.cleanup()
"""
        code=code.replace('SOURCE',repr(source)).replace('RUN_ID',repr(field_run_id))
        print(run(command+['exec','-T','web','python','-c',code]).stdout,flush=True)
