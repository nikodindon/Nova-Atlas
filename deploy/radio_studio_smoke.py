"""CI-only Radio Studio crash/recovery with real codecs, private fixture and file receiver."""
import os
from deploy.smoke import run

CODE=r'''
import json,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
 root=Path(temporary)
 phase1=r"""
import subprocess,sys,threading
from pathlib import Path
from modules.radio.studio import StudioJournal,lease
from modules.radio import bulletin_generator as bg
root=Path(sys.argv[1]);(root/'queue').mkdir();(root/'config').mkdir();(root/'background').mkdir()
(root/'config/bulletins.yaml').write_text('min_articles: 1\nlength_policy: warn\ntarget_words: 1500\n')
journal=StudioJournal(root/'data',root/'queue');journal.begin_run()
row=journal.admit('2026-11-06T14:30');journal.start(row['id'])
def synthetic_segment(text,voice,path,timeout=90):
 wav=path.with_suffix('.wav')
 result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','0.4','-ac','1','-codec:a','pcm_s16le',str(wav)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
 if result.returncode:return False
 from modules.radio.tts_bridge import NativeSpeech,TTSBridge
 TTSBridge.convert(NativeSpeech(wav,'wav'),path,5)
 return True
bg.synthesize_segment=synthetic_segment
builder=bg.BulletinGenerator({}, {'root':root,'data':root/'data','articles':root/'articles','audio_queue':root/'queue','tmp_dir':root/'work','background_music':root/'background'})
with lease(journal.root/'generation.lock'):
 path=builder.build([{'title':'Synthetic fixture'}],script='[VOIX1] Début fictif. [VOIX2] Conclusion fictive.',publication_name=row['publication_name'],observer=lambda phase,**values:(journal.tts_event if phase=='tts_voice_planned' else journal.metrics)(row['id'],phase,**values))
 assert path is not None
# Deliberately exit after atomic publication and BEFORE attach/queue notification.
assert journal.load()[0]['jobs'][row['id']]['status']=='generating'
"""
 subprocess.run([sys.executable,'-c',phase1,str(root)],check=True,timeout=60)
 from modules.radio.studio import StudioJournal,RadioStudio
 from modules.radio.streamer import Streamer,AndroidStreamer
 from modules.radio.audio_integrity import validated_publication,inspect_audio
 import threading
 journal=StudioJournal(root/'data',root/'queue');journal.begin_run()
 row=next(iter(journal.load()[0]['jobs'].values()));assert row['status']=='ready'
 config={'paths':{'audio_queue':str(root/'queue'),'music':str(root/'music')},'icecast':{'password':'synthetic-smoke-only'},'radio':{'bitrate':'128k','sample_rate':24000,'channels':1}}
 primary=Streamer(config);android=AndroidStreamer(config);studio=RadioStudio(journal,primary,android,threading.Event())
 assert studio.enqueue(row['id']);assert primary._play_queue.qsize()==1 and validated_publication(android._current)
 cached=android._current
 # Explicit Python test receiver: local file instead of Icecast, network=none.
 primary.icecast_url=str(root/'receiver.mp3')
 primary._play_next()
 source=primary._ffmpeg_proc
 assert source is not None
 source.stdin.close();source.wait(timeout=10)
 primary.stop();primary._kill_ffmpeg();android.stop();android._kill_ffmpeg()
 assert inspect_audio(root/'receiver.mp3').duration>0
 assert validated_publication(cached) and validated_publication(root/'queue'/row['publication_name'])
 assert journal.load()[0]['jobs'][row['id']]['status']=='completed'
 # Restart again: no automatic replay or new generation of the consumed local pass.
 restarted=StudioJournal(root/'data',root/'queue');restarted.begin_run()
 assert restarted.load()[0]['jobs'][row['id']]['status']=='completed'
 assert restarted.admit(row['slot']) is None
 assert not list((root/'queue').glob('.bulletin-*'))
 print('Radio Studio smoke PASS: real synthetic MP3 pipeline/receipt, process restart before notification, durable reconciliation, both admissions, local FFmpeg file receiver, clean stop, no automatic replay and preserved Android. No actual Icecast, TTS, LLM or production data.')
'''


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    print(run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,noexec,nosuid,size=128m,mode=1777',
        '--entrypoint','python','nova-atlas:02-foundation','-c',CODE]).stdout)

if __name__=='__main__':main()
