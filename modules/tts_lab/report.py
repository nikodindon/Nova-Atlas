"""Tables from actual run files. Fixture timing never counts as TTS performance."""
import json
from pathlib import Path
from statistics import median
from modules.tts_lab.contracts import LabError

MISSING='non mesuré'
CATALOG=Path(__file__).with_name('catalog.json')


def cell(value):return str(value).replace('|','/').replace('\n',' ')


def comparison(reports):
    catalog=json.loads(CATALOG.read_text());lines=['# Comparaison TTS — résultats disponibles','',
        'Mesures absentes : non mesuré. Fixtures sinusoïdales exclues des performances TTS. Aucune note humaine sans écoute.', '',
        '| Moteur | Modèle / voix FR | Code / poids / diffusion | RAM / VRAM | RTF CPU / GPU | Chargement | Stabilité | Qualité humaine | Déploiement / limites |',
        '|---|---|---|---|---|---|---|---|---|']
    for engine in catalog['engines']:
        selected=[r for r in reports if r.get('engine',{}).get('engine')==engine['engine']]
        real=[r for r in selected if r.get('status')=='success' and r.get('result',{}).get('sample_kind')=='real_synthesis']
        runs=[v for r in real for v in r['result']['runs'] if v.get('sample_kind')=='real_synthesis']
        ram=f"{max(v['peak_ram_kib'] for v in runs)/1024:.1f} MiB / {MISSING}" if runs else f'{MISSING} / {MISSING}'
        speed=f"{median(v['rtf'] for v in runs):.3f} / {MISSING}" if runs else f'{MISSING} / {MISSING}'
        load=f"{median(r['result']['model_load_seconds'] for r in real):.3f} s" if real else MISSING
        stability=(f"{len(runs)} sorties décodables ; omissions/répétitions/langue non vérifiées" if runs else MISSING)
        if selected and not real:stability='échecs observés : '+', '.join(sorted({r.get('reason') or 'unknown' for r in selected}))
        lines.append('| '+' | '.join(map(cell,[engine['engine'],engine['model']+' / '+engine['voice'],engine['code_license']+' ; '+engine['weights_license']+' ; '+engine['broadcast'],ram,speed,load,stability,MISSING,engine['deployment']+' ; '+engine['decision']]))+' |')
    lines+=['','## Mesures réelles par expérience et environnement','', '| Moteur / modèle / voix | CPU / mode | Chargement | Premier segment après chargement | RTF premier passage | RTF suivants | RAM worker max | VRAM / GPU |', '|---|---|---|---|---|---|---|---|']
    for report in reports:
        result=report.get('result',{})
        if report.get('status')!='success' or result.get('sample_kind')!='real_synthesis':continue
        runs=result['runs'];env=report.get('environment',{});spec=report['engine'];warm=runs[1:]
        values=[spec['engine']+' / '+spec['model']+' / '+spec['voice'],env.get('cpu','unknown')+' / '+env.get('inference_mode','unknown'),f"{result['model_load_seconds']:.3f} s",f"{runs[0]['first_segment_seconds']:.3f} s",f"{runs[0]['rtf']:.3f}",f"{median(r['rtf'] for r in warm):.3f}" if warm else MISSING,f"{max(r['peak_ram_kib'] for r in runs)/1024:.1f} MiB",MISSING]
        lines.append('| '+' | '.join(map(cell,values))+' |')
    lines+=['','Les lignes ne sont pas fusionnées entre machines, modèles ou voix. Aucun passage GPU exécuté.','', '## Provenance des expériences','']
    for report in reports:
        env=report.get('environment',{});spec=report.get('engine',{})
        lines.append(f"- {cell(spec.get('engine'))} {cell(spec.get('model'))} {cell(spec.get('version'))} / {cell(spec.get('voice'))} : {cell(report.get('status'))}, {cell(env.get('cpu'))}, profil {cell(env.get('profile_match'))}, mode {cell(env.get('inference_mode'))}. Genre : {cell(report.get('result',{}).get('sample_kind',report.get('reason')))}.")
    lines+=['','## Recommandation conditionnelle','',
        'Piper est le premier candidat CPU à tester ; Kokoro est un candidat qualité à examiner ensuite. Aucun vainqueur mesuré sur Ryzen/i5, aucune compatibilité 4 Go VRAM ni offloading démontrés.',
        'Conserver Edge dans la radio actuelle pour ce sprint. Sa diffusion publique et sa dépendance réseau restent à examiner. Aucun secours local qualifié tant que les essais réels et la revue des voix ne sont pas terminés.',
        'Un audio décodable ou un RTF bas ne prouve pas que le texte a été prononcé intégralement. Pas d’ASR ni écoute automatique.']
    return '\n'.join(lines)+'\n'
