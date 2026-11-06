"""Synchronous bounded discovery, private cached previews and cooperative cancellation."""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import threading
import time
from flask import request,jsonify
from modules.core.source_manager import SourceError,read_file,atomic_write,digest,source_url
from modules.discovery.source_discovery import Discovery,SearXNG,query,ranking


def install_discovery_api(app,paths,config):
    manager=app.extensions['atlas_source_manager'];guard=threading.Lock();state={'active':False,'cancel':None,'started_at':None}
    cachepath=paths['data']/'source_discovery.json';lockpath=Path(paths['feeds_file']).with_name('source-tests.lock')
    settings=config.get('discovery',{})
    if not isinstance(settings,dict):raise SourceError('Configuration découverte invalide')
    endpoint=os.environ.get('ATLAS_SEARXNG_URL','')
    backend=None;backend_error=False
    if endpoint:
        try:backend=SearXNG(endpoint)
        except SourceError:backend_error=True
    app.extensions['atlas_discovery']={'factory':Discovery,'state':state}

    def body(fields):
        if not request.is_json or not request.content_length or request.content_length>4096:raise SourceError('JSON ≤4 Kio requis')
        def pairs(values):
            result={}
            for key,value in values:
                if key in result:raise ValueError()
                result[key]=value
            return result
        try:value=json.loads(request.get_data(),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,RecursionError,UnicodeError):raise SourceError('JSON invalide') from None
        if not isinstance(value,dict) or set(value)!=set(fields):raise SourceError('Champs invalides')
        return value

    def cache():
        try:
            raw=read_file(cachepath)
            if not raw:return {'version':1,'attempts':[],'key':None,'expires':0,'result':None}
            if len(raw.encode())>256*1024:raise ValueError()
            result=json.loads(raw)
            if not isinstance(result,dict) or result.get('version')!=1 or set(result)-{'version','attempts','key','expires','result'} or not isinstance(result.get('attempts'),list) or len(result['attempts'])>3:raise ValueError()
            if any((type(x) not in (int,float) or not math.isfinite(x)) for x in result['attempts']):raise ValueError()
            if type(result.get('expires')) not in (int,float) or not math.isfinite(result['expires']):raise ValueError()
            if result.get('result') is not None and (not isinstance(result['result'],dict) or not isinstance(result['result'].get('candidates'),list) or len(result['result']['candidates'])>20):raise ValueError()
            if result.get('key') is not None and (not isinstance(result['key'],str) or len(result['key'])!=64):raise ValueError()
            if result.get('result') is None and result['expires']!=0:raise ValueError()
            if result.get('result') is not None:
                for row in result['result']['candidates']:
                    if not isinstance(row,dict) or row.get('id')!=digest(source_url(row.get('url'))):raise ValueError()
                    for field,maximum in (('title',300),('description',500),('state',40),('reason',128)):
                        if not isinstance(row.get(field),str) or len(row[field])>maximum:raise ValueError()
                    for field in ('site_url','final_url'):
                        if row.get(field):source_url(row[field])
                    for field in ('samples','warnings','existing','criteria'):
                        if not isinstance(row.get(field),list):raise ValueError()
                    if len(row['samples'])>3 or len(row['existing'])>512 or len(row['warnings'])>100 or len(row['criteria'])!=5:raise ValueError()
            return result
        except (ValueError,TypeError,KeyError,RecursionError):raise SourceError('Cache découverte invalide ; original préservé') from None

    def save(value):
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>256*1024:raise SourceError('Résultat trop volumineux')
        atomic_write(cachepath,raw)

    @contextmanager
    def admitted():
        with guard:
            if state['active']:raise SourceError('Découverte déjà active')
            state.update(active=True,cancel=threading.Event(),started_at=time.time())
        fd=None
        try:
            fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise SourceError('Verrou non sûr')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise SourceError('Opération source déjà active') from None
            yield
        finally:
            if fd is not None:os.close(fd)
            with guard:state.update(active=False,cancel=None)

    @app.after_request
    def discovery_headers(response):
        if request.path.startswith('/api/admin/discovery'):response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/admin/discovery/status')
    def discovery_status():
        with guard:return jsonify(status='ok',active=state['active'],started_at=state['started_at'],search_available=backend is not None,search_configuration_error=backend_error)

    @app.post('/api/admin/discovery/cancel')
    def discovery_cancel():
        body(())
        with guard:
            if state['cancel'] is not None:state['cancel'].set()
        return jsonify(status='ok')

    @app.post('/api/admin/discovery/<mode>')
    def discovery_run(mode):
        if mode not in ('site','search'):raise SourceError('Mode invalide')
        data=body(('url',) if mode=='site' else ('query',));value=source_url(data['url']) if mode=='site' else query(data['query'])
        if mode=='search' and backend is None:raise SourceError('SearXNG absent ou configuration non sûre')
        with admitted():
            now=time.time();stored=cache();identity=digest(json.dumps([mode,value,settings,endpoint],sort_keys=True))
            if stored.get('key')==identity and stored.get('expires',0)>now:
                result=stored['result'];cached=True
            else:
                attempts=[t for t in stored['attempts'] if t>now-60]
                if len(attempts)>=3:return jsonify(status='error',msg='Trois découvertes par minute maximum'),429
                # Admission persisted BEFORE any network, including failures/crash.
                stored.update(attempts=attempts+[now],key=None,expires=0,result=None);save(stored)
                factory=app.extensions['atlas_discovery']['factory']
                engine=factory(manager,config=settings,search=backend,cancel=state['cancel'])
                result=engine.run(mode,value);stored.update(key=identity,expires=time.time()+300,result=result);save(stored);cached=False
            # Membership/config version are always live, even on a cached network result.
            doc=manager.read();result=dict(result);rows=[]
            for row in result['candidates']:
                row=dict(row);row['existing']=[{'id':s.id,'category':s.category,'enabled':s.enabled} for s in doc.sources if s.url==row['url']];row['criteria']=[*row['criteria'][:-1],'existing' if row['existing'] else 'new'];rows.append(row)
            rows.sort(key=ranking)
            result.update(candidates=rows,version=doc.version)
            return jsonify(status='ok',cached=cached,**result)

    @app.post('/api/admin/discovery/preview')
    def discovery_preview():
        data=body(('id',))
        if not isinstance(data['id'],str) or len(data['id'])!=64:raise SourceError('Identifiant invalide')
        # No new download, URL input or raw HTML in preview. One last operation only.
        stored=cache()
        if stored.get('expires',0)<=time.time():raise SourceError('Découverte expirée ; relancez-la')
        row=next((c for c in stored.get('result',{}).get('candidates',[]) if c['id']==data['id']),None)
        if row is None:raise SourceError('Candidat absent')
        return jsonify(status='ok',candidate=row)
