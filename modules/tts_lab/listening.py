"""Explicit blind listening pack, no subjective score invented by software."""
import json
from pathlib import Path
import random
import shutil
from modules.tts_lab.audio import inspect_wav
from modules.tts_lab.contracts import LabError,checked,private_root
from modules.core.source_manager import atomic_write

CRITERIA=('naturel','prononciation','intelligibilite','fluidite','intonation','rythme','ponctuation','timbre','expressivite','confort','chiffres_acronymes','coherence_segments')


def blind(reports,output,*,seed=0):
    samples=[]
    for path in reports:
        path=checked(path);report=json.loads(path.read_text())
        if report.get('status')!='success' or report.get('result',{}).get('sample_kind')!='real_synthesis':raise LabError('real_samples_required_for_listening')
        for run in report['result']['runs']:
            name=run['comparison_file']
            if Path(name).name!=name or not name.endswith('.wav'):raise LabError('unsafe_sample')
            source=checked(path.parent/name);info=inspect_wav(source)
            if info['sha256']!=run['normalized_audio']['sha256']:raise LabError('sample_changed')
            samples.append((source,report['engine'],run['case_id'],run['repetition']))
    if not samples or len(samples)>180:raise LabError('invalid_sample_count')
    random.Random(seed).shuffle(samples);root=private_root(output);key={};forms=[]
    for i,(source,engine,case,repetition) in enumerate(samples):
        identity=f'E{i+1:03d}';destination=root/(identity+'.wav');shutil.copyfile(source,destination);destination.chmod(0o600)
        key[identity]={'engine':engine,'case':case,'repetition':repetition}
        forms.append({'sample':identity,'ratings':dict.fromkeys(CRITERIA),'comment':'','omissions':None,'repetitions':None,'language_change':None,'listener':None})
    atomic_write(root/'.private-key.json',json.dumps(key,ensure_ascii=False));atomic_write(root/'listening-form.json',json.dumps({'version':1,'notice':'Évaluation exploratoire, pas un MOS scientifique. Ne pas partager la clé avant écoute.','evaluations':forms},ensure_ascii=False,indent=2))
    return {'samples':len(forms),'key_private':True}


def validate_evaluations(data):
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('evaluations'),list) or not 1<=len(data['evaluations'])<=180:raise LabError('invalid_evaluations')
    for row in data['evaluations']:
        if set(row.get('ratings',{}))!=set(CRITERIA) or any(type(v) is not int or not 1<=v<=5 for v in row['ratings'].values()):raise LabError('all_listening_scores_required')
        if not isinstance(row.get('comment'),str) or len(row['comment'])>1000 or not isinstance(row.get('listener'),str) or not 1<=len(row['listener'])<=40:raise LabError('invalid_listening_metadata')
    return {'evaluations':len(data['evaluations']),'scope':'human exploratory ratings, not scientific MOS'}
