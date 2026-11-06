"""Private Radio Studio: bounded observation and explicit control, no web scheduler."""
from datetime import datetime
import json
import re
from flask import jsonify,request,send_file
from modules.radio.studio import StudioJournal,StudioError,StudioConflict,calendar,ZONE


def install_studio_api(app,paths,config):
    journal=StudioJournal(paths['data'],paths['root']/config.get('paths',{}).get('audio_queue','audio_queue'),configured=config.get('radio_studio',{}).get('enabled') is True)
    app.extensions['atlas_radio_studio']=journal
    @app.errorhandler(StudioError)
    def studio_error(error):return jsonify(status='error',reason=str(error)),409 if isinstance(error,StudioConflict) else 400
    def body():
        if not request.is_json or not request.content_length or request.content_length>4096:raise StudioError('bounded_json_required')
        def pairs(values):
            result={}
            for k,v in values:
                if k in result:raise StudioError('duplicate_json_field')
                result[k]=v
            return result
        try:value=json.loads(request.get_data(),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise StudioError('invalid_json') from None
        if not isinstance(value,dict) or set(value)-{'action','version','id','ack'}:raise StudioError('invalid_fields')
        if not isinstance(value.get('version'),str) or not re.fullmatch('[a-f0-9]{64}',value['version']):raise StudioError('version_required')
        if value.get('action') not in ('dry-run','enable','disable','reconcile','resume','verify'):raise StudioError('invalid_control')
        if 'id' in value and (not isinstance(value['id'],str) or not re.fullmatch('[a-f0-9]{32}',value['id'])):raise StudioError('invalid_job_id')
        if 'ack' in value and type(value['ack']) is not bool:raise StudioError('invalid_ack')
        return value
    def schedule():
        from modules.radio.bulletin_generator import load_bulletins_config
        import yaml
        file=paths['root']/'config/bulletins.yaml'
        try:
            if file.exists():cfg=yaml.safe_load(file.read_text())
            else:cfg=load_bulletins_config(paths)
            result=calendar(cfg.get('post_hours',[]),datetime.now(ZONE),2)
            return result
        except (ValueError,OSError,AttributeError,yaml.YAMLError):raise StudioError('calendar_unavailable') from None
    @app.after_request
    def studio_headers(response):
        if request.path.startswith(('/api/admin/radio','/radio-studio')):response.headers['Cache-Control']='no-store'
        return response
    @app.get('/radio-studio')
    def studio_page():
        from modules.web.atlas_web import INDEX_TEMPLATE,BASE_CSS,GLOBAL_SCRIPT,_branding,_get_icecast_url
        from modules.web.web_security import html_template
        from markupsafe import Markup
        start=INDEX_TEMPLATE.index('<div class="page">');end=INDEX_TEMPLATE.index('<footer>')
        content='''<div class="page"><section id="radio-studio"><h1>Radio Studio</h1><p>Lecture locale et diffusion Icecast sont des observations distinctes. Aucun accusé de réception d'écoute.</p><p id="studio-message" aria-live="polite"></p>
<div><button data-studio="refresh">Actualiser</button><button data-studio="dry-run">Dry-run</button><button data-studio="reconcile">Demander réconciliation</button><button data-studio="disable">Suspendre les nouveaux créneaux</button><button data-studio="enable">Réactiver les créneaux</button></div>
<h2>Canaux</h2><pre id="studio-channels"></pre><h2>Métriques privées</h2><pre id="studio-metrics"></pre>
<h2>Calendrier — cibles et lancements prévus</h2><div id="studio-calendar"></div><h2>Publications et jobs</h2><div id="studio-jobs"></div><h2>TTS</h2><pre id="studio-tts"></pre></section></div>'''
        template=INDEX_TEMPLATE[:start].replace('— Archives</title>','— Radio Studio</title>')+content+INDEX_TEMPLATE[end:]
        return html_template(template).render(**_branding(paths),css=Markup(BASE_CSS),global_script=Markup(GLOBAL_SCRIPT),icecast_url=_get_icecast_url(paths),breaking_banner='',flash_button_html='',flash_button_js='',today_file=None,year=datetime.now().year)
    @app.get('/api/admin/radio')
    def studio_status():
        if request.args:raise StudioError('unexpected_query')
        data=journal.snapshot();data['calendar']=schedule()
        for item in data['calendar']:
            item['status']=next((row['status'] for row in data['jobs'] if row['kind']=='bulletin' and row['slot']==item['slot']),'planned')
        data['next_slot']=next((v for v in data['calendar'] if datetime.fromisoformat(v['target_at'])>datetime.now(ZONE)),None)
        for info in data['channels'].values():
            try:age=max(0,journal.clock()-datetime.fromisoformat(info['observed_at']).timestamp())
            except (ValueError,KeyError):age=None
            info['observation_age_seconds']=age;info['fresh']=age is not None and age<15
        profiles=config.get('tts_bridge',{}).get('profiles',{})
        data['tts']={'default_engine':'edge','bridge_enabled':config.get('tts_bridge',{}).get('enabled') is True,'alternative_models_qualified':False,'availability':'not_probed',
            'profiles':{name:[{'tag':tag,'engine':plan.get('engine') if plan.get('engine') in ('edge','piper') else 'unavailable','voice':plan['voice'],'qualified_for_runtime':plan.get('engine')=='edge' and plan.get('qualified') is True,'fallback_enabled':plan.get('fallback_enabled') is True} for tag,plan in values.items() if tag in ('[VOIX1]','[VOIX2]','[VOIX3]','[VOIX_BREAKING]') and isinstance(plan,dict) and isinstance(plan.get('voice'),str) and re.fullmatch(r'[\w-]{1,100}',plan['voice'])] for name,values in profiles.items() if name in ('bulletin','flash','intro','breaking','transition','conclusion') and isinstance(values,dict)}}
        return jsonify(status='ok',**data)
    @app.get('/api/admin/radio/jobs/<identity>')
    def studio_job(identity):
        row=journal.load()[0]['jobs'].get(identity)
        if not row:return jsonify(status='error',reason='job_not_found'),404
        return jsonify(status='ok',job=row)
    @app.get('/api/admin/radio/jobs/<identity>/audio')
    def studio_audio(identity):
        row=journal.load()[0]['jobs'].get(identity)
        if not row or not row['publication'] or journal.publication(row)!=row['publication']:raise StudioError('publication_invalid')
        return send_file(journal.path_for(row),mimetype='audio/mpeg',conditional=False)
    @app.post('/api/admin/radio/control')
    def studio_control():
        value=body();action=value.get('action')
        if action=='dry-run':
            if value['version']!=journal.load()[1]:raise StudioConflict('version_conflict')
            return jsonify(status='ok',dry_run=True,calendar=schedule(),generation=False,publication=False)
        journal.control(action,value['version'],identity=value.get('id'),ack=value.get('ack',False))
        return jsonify(status='ok',requested=action)
