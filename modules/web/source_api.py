"""Administrative adapters; global auth/CSRF hooks apply to every endpoint."""
import fcntl
import json
import os
from pathlib import Path
import threading
import stat
import re
from urllib.parse import urlsplit, urlunsplit
from flask import request, jsonify, Response
from itsdangerous import URLSafeTimedSerializer,BadSignature,SignatureExpired
from modules.core.source_manager import SourceManager,SourceError,Conflict,NotFound,source_url


def redacted_url(value):
    if not value:return None
    try:
        p=urlsplit(value)
        return urlunsplit((p.scheme,p.netloc,p.path,'',''))
    except ValueError:return None


def install_source_api(app,paths):
    manager=SourceManager(paths['feeds_file'],paths['data']/'feed_ingestion_state.json')
    app.extensions['atlas_source_manager']=manager
    # Per-process admission, plus flock across Web workers on the same POSIX volume.
    gate=threading.BoundedSemaphore(1)
    previews=URLSafeTimedSerializer(app.secret_key,salt='atlas-source-import-preview-v1')

    @app.after_request
    def private_source_responses(response):
        if request.path.startswith(('/api/admin/sources','/config/feeds')):response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(SourceError)
    def source_error(error):
        code=409 if isinstance(error,Conflict) else 404 if isinstance(error,NotFound) else 400
        return jsonify(status='error',msg=str(error)),code

    def body(allowed,required=()):
        if request.content_length is None or request.content_length>65536:raise SourceError('Corps absent ou trop volumineux')
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError()
                result[key]=value
            return result
        try:value=json.loads(request.get_data(),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise SourceError('JSON invalide') from None
        if not isinstance(value,dict) or set(value)-set(allowed) or set(required)-set(value):raise SourceError('Champs invalides')
        if 'version' in value and (not isinstance(value['version'],str) or not re.fullmatch('[0-9a-f]{64}',value['version'])):raise SourceError('Version requise et invalide')
        return value

    def changed(action,data,expected=None):
        manager.mutate(action,data,expected)
        from modules.fetch.atlas_fetch import invalidate_feeds_cache
        invalidate_feeds_cache()
        return jsonify(status='ok',**manager.listing())

    @app.get('/api/admin/sources')
    def sources_list():
        if set(request.args)-{'page','per_page','q','category','health'} or any(len(request.args.getlist(k))!=1 for k in request.args):raise SourceError('Filtres invalides')
        try:page=int(request.args.get('page','1'));size=int(request.args.get('per_page','50'))
        except ValueError:raise SourceError('Pagination invalide') from None
        if not 1<=page<=10000 or not 1<=size<=100 or any(len(v)>2048 for v in request.args.values()):raise SourceError('Pagination ou filtre invalide')
        result=manager.listing();query=request.args.get('q','').casefold()
        rows=[s for s in result['sources'] if query in (s['url']+' '+s['name']).casefold() and (not request.args.get('category') or s['category']==request.args['category']) and (not request.args.get('health') or s['health']==request.args['health'])]
        result.update(sources=rows[(page-1)*size:page*size],total=len(rows),page=page,per_page=size)
        return jsonify(status='ok',**result)

    @app.get('/api/admin/sources/stats')
    def sources_stats():
        result=manager.listing();health={}
        for row in result['sources']:health[row['health']]=health.get(row['health'],0)+1
        return jsonify(status='ok',stats=result['stats'],health=health,version=result['version'])

    @app.get('/api/admin/sources/<identity>')
    def sources_detail(identity):
        result=manager.listing();row=next((s for s in result['sources'] if s['id']==identity),None)
        if row is None:raise NotFound('Source absente')
        return jsonify(status='ok',source=row,version=result['version'])

    @app.post('/api/admin/sources')
    def sources_add():
        data=body({'url','category','name','enabled','version'},('url','category','version'));expected=data.pop('version')
        return changed('add',data,expected)

    @app.route('/api/admin/sources/<identity>',methods=['PATCH','DELETE'])
    def sources_update(identity):
        data=body({'url','category','name','enabled','version'} if request.method=='PATCH' else {'version'},('version',));version=data.pop('version');data['id']=identity
        return changed('delete' if request.method=='DELETE' else 'update',data,version)

    @app.post('/api/admin/sources/actions/<action>')
    def sources_action(action):
        allowed={'enable':{'id'},'disable':{'id'},'move':{'id','category','direction'},'reorder':{'category','ids'},'category':{'category'},'rename_category':{'category','new_category'}}
        if action not in allowed:raise NotFound('Action absente')
        data=body(allowed[action]|{'version'},('version',));version=data.pop('version')
        return changed(action,data,version)

    @app.get('/api/admin/sources/export')
    def sources_export():
        if set(request.args)-{'format'}:raise SourceError('Paramètre invalide')
        fmt=request.args.get('format','yaml');output=manager.export(fmt)
        return Response(output,mimetype='application/xml' if fmt=='opml' else 'application/yaml',headers={'Content-Disposition':'attachment; filename="nova-sources.'+('opml' if fmt=='opml' else 'yaml')+'"','Cache-Control':'no-store'})

    @app.post('/api/admin/sources/import')
    def sources_import():
        data=body({'content','format','apply','version','fingerprint','preview_token'},('content','format'))
        apply=data.get('apply',False)
        if type(apply) is not bool:raise SourceError('Application explicite requise')
        incoming,report=manager.preview(data['content'],data['format'])
        if not apply:return jsonify(status='ok',dry_run=True,preview_token=previews.dumps({'fingerprint':report['fingerprint'],'version':report['version']}),**report)
        try:receipt=previews.loads(data.get('preview_token',''),max_age=600)
        except (BadSignature,SignatureExpired,TypeError):raise Conflict('Prévisualisation absente ou expirée') from None
        if receipt!={'fingerprint':report['fingerprint'],'version':data.get('version')}:raise Conflict('Contenu ou version modifié depuis prévisualisation')
        if data.get('fingerprint')!=report['fingerprint'] or not data.get('version'):raise Conflict('Prévisualisation et version requises')
        return changed('import',{'document':incoming},data['version'])

    @app.post('/api/admin/sources/test')
    def sources_test():
        data=body({'url'},('url',));url=source_url(data['url'])
        if not gate.acquire(blocking=False):return jsonify(status='error',msg='Test déjà actif'),429
        lockfd=None
        try:
            lockpath=Path(paths['feeds_file']).with_name('source-tests.lock')
            lockfd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            info=os.fstat(lockfd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise SourceError('Verrou de tests invalide')
            try:fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return jsonify(status='error',msg='Test déjà actif'),429
            # Persist bounded admission/embargo records separately from News-owned health.
            from modules.fetch.ingestion import FeedIngestor,FeedState,key
            testpath=paths['data']/'source_test_state.json'
            ingestor=FeedIngestor(testpath,config={'feed_timeout':10,'max_articles_per_feed':100,'feed_min_poll_seconds':60})
            if not ingestor.state.writable:raise SourceError('État de limitation indisponible')
            now=ingestor.now().timestamp()
            if sum(r.get('last_attempt',0)>now-60 for r in ingestor.state.records.values())>=5:return jsonify(status='error',msg='Maximum cinq tests par minute'),429
            # Respect active News embargoes without writing its state or clearing validators.
            news=FeedState(manager.health_path)
            if not news.writable:raise SourceError('État de santé invalide')
            for identity,rec in news.records.items():
                if max(rec.get('host_until',0),rec.get('retry_until',0),rec.get('next_attempt_at',0))>now:
                    if rec.get('host_until',0)>now and rec.get('rate_host',rec.get('host')):ingestor.host_budget.defer_key(rec.get('rate_host',rec.get('host')),rec['host_until'])
                    if identity==key(url):ingestor.state.records[identity]=dict(rec)
            result=ingestor.fetch(url)
            if not ingestor.state.writable:raise SourceError('Limitation indisponible, test non renouvelable')
            titles=[e.title for e in result.entries[:3]]
            # Detect format from parser's normalized provenance where available; no second download.
            return jsonify(status='ok',result={'state':result.status,'format':result.format or None,'reason':result.reason,'http_status':result.http_status,'entries':len(result.entries),'titles':titles,
                'latest_published_at':max((e.published_at.isoformat() for e in result.entries if e.published_at),default=None),
                'duration':result.duration,'warnings':list(result.warnings),'url':redacted_url(url),'final_url':redacted_url(result.response_url),'deferred':result.deferred})
        finally:
            if lockfd is not None:os.close(lockfd)
            gate.release()

    @app.get('/config/feeds')
    def feeds_list():
        # Compatibility projection. Invalid/private historical files fail closed.
        try:
            result=manager.listing();feeds={cat:[s['url'] for s in result['sources'] if s['category']==cat and s['enabled']] for cat in result['categories']}
            return jsonify(status='ok',feeds=feeds,stats=result['stats'],version=result['version'])
        except SourceError:return jsonify(status='error',msg='Fichier invalide, correction locale requise',feeds={},stats={'categories':0,'active':0,'disabled':0}),400

    @app.post('/config/feeds/<action>')
    def feeds_compat(action):
        data=body({'category','url','direction','version'})
        expected=data.pop('version',None)
        if action=='reset':
            if data:raise SourceError('Champs invalides')
            manager.read()
            from modules.fetch.atlas_fetch import invalidate_feeds_cache
            invalidate_feeds_cache();return jsonify(status='ok')
        if action in ('add','category'):return changed(action,data,expected)
        if action not in ('remove','toggle','move'):raise NotFound('Action absente')
        # Resolve identity and mutate within the same optimistic version: stale reads conflict.
        doc=manager.read();src=next((s for s in doc.sources if s.category==data.get('category') and s.url==data.get('url')),None)
        if src is None:raise NotFound('Source absente')
        payload={'id':src.id}
        if action=='move':payload['direction']=data.get('direction')
        result=changed('disable' if action=='remove' else action,payload,expected or doc.version)
        response=result.get_json()
        if action=='toggle':response['new_state']='disabled' if src.enabled else 'active'
        if action=='move':response['direction']=data.get('direction')
        return jsonify(response)
