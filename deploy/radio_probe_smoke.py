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


def exercise(command,run):
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Disposable CI only')
    print(run(command+['exec','-T','web','python','-c',CODE]).stdout,flush=True)
    run(command+['restart','icecast'])
    run(command+['up','-d','--wait','--wait-timeout','60','icecast'])
    print(run(command+['exec','-T','web','python','-c',CODE]).stdout,flush=True)
    print('RC Icecast restart PASS: both isolated mounts received and decoded before/after restart; no claim of 24h continuity or audience delivery.',flush=True)
