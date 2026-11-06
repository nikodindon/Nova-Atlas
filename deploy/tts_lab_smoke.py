"""CI-only network-none fixture experiment, never a speech model or provider."""
import os
from deploy.smoke import run

CODE=r'''
from pathlib import Path
import json,tempfile
from modules.tts_lab.contracts import EngineSpec
from modules.tts_lab.corpus import load
from modules.tts_lab.runner import run
from modules.tts_lab.audio import inspect_wav
from modules.tts_lab.report import comparison
with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
 root=Path(temporary)
 result=run(EngineSpec(),[c for c in load()['cases'] if c['id'] in ('longueur-50','longueur-800')],root/'experiment',execute=True,repetitions=2,timeout=60)
 assert result['status']=='success',result['reason']
 assert result['result']['sample_kind']=='synthetic_fixture'
 assert len(result['result']['runs'])==4
 for row in result['result']['runs']:
  assert row['audio']['frames']==sum(part['audio']['frames'] for part in row['segments'])
  assert row['coverage']=='unverified_no_asr_or_listening'
  assert inspect_wav(root/'experiment'/row['comparison_file'])['frames']>0
 report=comparison([result]);assert 'non mesuré' in report
 assert not (root/'audio_queue').exists()
 print('TTS lab smoke PASS: corpus, isolated bounded CPU worker, synthetic PCM only, full segment order, ffmpeg conversion, fixed gain, no production publication or network/model.')
'''


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    print(run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,noexec,nosuid,size=128m,mode=1777',
        '--entrypoint','python','nova-atlas:02-foundation','-c',CODE]).stdout)

if __name__=='__main__':main()
