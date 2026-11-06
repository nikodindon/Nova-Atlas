"""Explicit admin review. Public routes never download, decode or infer rights."""
from flask import request,jsonify,Response,send_from_directory
from pathlib import Path
import json
import re
from modules.media.service import MediaService
from modules.media.store import MediaError,MediaConflict,FALLBACK


def install_media_api(app,paths,config):
    service=MediaService(paths['articles'],paths['data'],config.get('media',{}))
    app.extensions['atlas_media']=service

    @app.errorhandler(MediaError)
    def media_error(error):return jsonify(status='error',msg=str(error)),409 if isinstance(error,MediaConflict) else 400

    def body(fields,required=()):
        if not request.is_json or not request.content_length or request.content_length>8192:raise MediaError('bounded_json_required')
        def pairs(items):
            result={}
            for k,v in items:
                if k in result:raise ValueError()
                result[k]=v
            return result
        try:value=json.loads(request.get_data(),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise MediaError('invalid_json') from None
        if not isinstance(value,dict) or set(value)-set(fields) or set(required)-set(value):raise MediaError('invalid_fields')
        if 'version' in value and (not isinstance(value['version'],str) or not re.fullmatch('[0-9a-f]{64}',value['version'])):raise MediaError('invalid_version')
        return value

    @app.after_request
    def media_headers(response):
        if request.path.startswith(('/api/admin/media','/media/images','/api/media')):response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/admin/media')
    def media_list():
        if set(request.args)-{'page'}:raise MediaError('invalid_query')
        try:page=int(request.args.get('page','1'))
        except ValueError:raise MediaError('invalid_page') from None
        if not 1<=page<=10:raise MediaError('invalid_page')
        state,version=service.store.load();rows=list(state['records'].values())
        return jsonify(status='ok',mode=service.store.mode,version=version,count=len(rows),records=[{**r,'approval_binding':service.store.binding(r) if r.get('artifact') else None} for r in rows[(page-1)*20:page*20]])

    @app.get('/api/admin/media/targets')
    def media_targets():return jsonify(status='ok',targets=service.targets())

    @app.post('/api/admin/media/search')
    def media_search():
        data=body({'query','target'},('query','target'))
        return jsonify(status='ok',candidates=service.search(data['query'],data['target']))

    @app.post('/api/admin/media/discover')
    def media_discover():
        data=body({'target','page'},('target',));page=data.get('page',False)
        if type(page) is not bool:raise MediaError('invalid_page_flag')
        return jsonify(status='ok',candidates=service.discover_html(data['target']) if page else service.discover_cached(data['target']))

    @app.post('/api/admin/media/cancel')
    def media_cancel():body({});service.cancel.set();return jsonify(status='ok')

    @app.post('/api/admin/media/<identity>/<action>')
    def media_action(identity,action):
        if not re.fullmatch('[0-9a-f]{64}',identity):raise MediaError('invalid_candidate')
        if action=='refresh':
            body({});result=service.refresh(identity)
        elif action=='prepare':
            data=body({'version'},('version',));result=service.prepare(identity,data['version'])
        else:
            data=body({'version','binding','ack','caption'},('version',))
            state,_=service.store.load()
            if action=='approve':
                row=state['records'].get(identity)
                if not row or not service.valid_target(row['target']):raise MediaConflict('target_changed_reload_required')
            result=service.store.decide(identity,action,expected=data['version'],binding=data.get('binding'),ack=data.get('ack',False),caption=data.get('caption'))
        return jsonify(status='ok',result=result)

    @app.get('/api/admin/media/<identity>/preview/<int:size>.jpg')
    def media_preview(identity,size):
        state,_=service.store.load();row=state['records'].get(identity)
        if not row or not row.get('artifact'):return jsonify(status='error',msg='preview_unavailable'),404
        return Response(service.store.bytes(row,size),mimetype='image/jpeg')

    @app.get('/api/media')
    def media_public():
        if set(request.args)!={'kind','id','fingerprint'} or any(len(request.args.getlist(k))!=1 for k in request.args):return jsonify(status='error',msg='invalid_query'),400
        target=dict(request.args)
        if target['kind'] not in ('article','event') or not re.fullmatch('[0-9a-f]{64}',target['fingerprint']) or len(target['id'])>100:return jsonify(status='error',msg='invalid_query'),400
        # The public query only selects an existing approved context, never an external URL.
        result=dict(FALLBACK)
        if service.store.mode=='approved_only':
            try:
                state,_=service.store.load()
                for row in state['records'].values():
                    if all(row['target'].get(k)==v for k,v in target.items()) and service.valid_target(row['target']):result=service.store.record_projection(row) or result;break
            except (ValueError,OSError,KeyError,TypeError):pass
        return jsonify(status='ok',image=result)

    @app.get('/media/images/<approval>/<int:size>.jpg')
    def media_image(approval,size):
        if re.fullmatch('[0-9a-f]{64}',approval) and size in (320,768) and service.store.mode=='approved_only':
            try:
                state,_=service.store.load()
                for row in state['records'].values():
                    if (row.get('approval') or {}).get('id')==approval and service.valid_target(row['target']) and service.store.record_projection(row):return Response(service.store.bytes(row,size),mimetype='image/jpeg')
            except (ValueError,OSError,KeyError,TypeError):pass
        return send_from_directory(Path(__file__).with_name('assets'),'news-fallback.svg',mimetype='image/svg+xml')
