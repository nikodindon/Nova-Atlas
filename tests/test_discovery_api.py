"""Authenticated Flask requests, synthetic transport, never a real source."""
import json
import threading
import time
from pathlib import Path
import pytest
from modules.discovery.source_discovery import Discovery
from modules.http import external as http
from tests.test_admin_security import app,password_hash,client,login,csrf,get,post
from tests.test_source_discovery import RSS,HTML,transport

P='/api/admin/discovery'


def setup(client,app,mapping=None):
    login(client);headers={'X-CSRF-Token':csrf(client)};calls=[]
    fake=transport(mapping or {'https://site.invalid/':HTML,'https://site.invalid/feed':RSS},calls)
    app.extensions['atlas_discovery']['factory']=lambda manager,**kw:Discovery(manager,transport=fake,**kw)
    return headers,calls


@pytest.mark.parametrize('path',['/status','/preview','/site','/search','/cancel'])
def test_private_routes(client,path):
    response=get(client,P+path) if path=='/status' else post(client,P+path,json={})
    assert response.status_code==401


def test_csrf_required_and_invalid(client):
    login(client)
    assert post(client,P+'/site',json={'url':'https://site.invalid/'}).status_code==403
    assert post(client,P+'/site',json={'url':'https://site.invalid/'},headers={'X-CSRF-Token':'invalid'}).status_code==403


def test_discovery_preview_then_explicit_add_with_conflict(client,app):
    headers,calls=setup(client,app);path=app.extensions['atlas_paths']['feeds_file'];before=path.read_bytes()
    response=post(client,P+'/site',json={'url':'https://site.invalid/'},headers=headers)
    assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
    result=response.json;row=next(c for c in result['candidates'] if c['format']=='rss20')
    assert path.read_bytes()==before
    count=len(calls);preview=post(client,P+'/preview',json={'id':row['id']},headers=headers)
    assert preview.status_code==200 and preview.json['candidate']['samples'][0]['title']=='Actualite' and len(calls)==count
    added=post(client,'/api/admin/sources',json={'category':'tech','url':row['url'],'version':result['version']},headers=headers)
    assert added.status_code==200
    assert post(client,'/api/admin/sources',json={'category':'tech','url':'https://other.invalid/rss','version':result['version']},headers=headers).status_code==409
    cached=post(client,P+'/site',json={'url':'https://site.invalid/'},headers=headers).json
    assert cached['cached'] and len(calls)==count
    assert next(c for c in cached['candidates'] if c['id']==row['id'])['existing'][0]['category']=='tech'
    assert cached['version']!=result['version']
    assert next(c for c in cached['candidates'] if c['id']==row['id'])['criteria'][-1]=='existing'


def test_discovery_limits_persisted_and_no_cache_html(client,app):
    headers,calls=setup(client,app)
    for i in range(3):assert post(client,P+'/site',json={'url':f'https://site{i}.invalid/'},headers=headers).status_code==200
    assert post(client,P+'/site',json={'url':'https://four.invalid/'},headers=headers).status_code==429
    state=json.loads((app.extensions['atlas_paths']['data']/'source_discovery.json').read_text())
    assert len(state['attempts'])==3 and '<html>' not in json.dumps(state)


@pytest.mark.parametrize('data',[{}, {'url':'file:///etc/passwd'},{'url':'http://127.0.0.1/'},{'url':'https://u:secret@site.invalid/'},{'url':[]},{'url':'https://site.invalid/','extra':1}])
def test_invalid_site_body_no_calls(client,app,data):
    headers,calls=setup(client,app)
    assert post(client,P+'/site',json=data,headers=headers).status_code==400 and calls==[]


def test_query_without_provider_and_no_real_search(client,app):
    headers,calls=setup(client,app)
    assert not get(client,P+'/status').json['search_available']
    assert post(client,P+'/search',json={'query':'technologie japonaise'},headers=headers).status_code==400
    assert calls==[]


def test_concurrent_admission_and_cancellation(client,app):
    headers,calls=setup(client,app);state=app.extensions['atlas_discovery']['state'];event=threading.Event()
    state.update(active=True,cancel=event)
    assert get(client,P+'/status').json['active']
    assert post(client,P+'/site',json={'url':'https://site.invalid/'},headers=headers).status_code==400
    assert calls==[]
    assert post(client,P+'/cancel',json={},headers=headers).status_code==200 and event.is_set()
    state.update(active=False,cancel=None)


def test_corrupt_private_cache_preserved(client,app):
    headers,calls=setup(client,app);path=app.extensions['atlas_paths']['data']/'source_discovery.json';path.write_text('broken')
    assert post(client,P+'/site',json={'url':'https://site.invalid/'},headers=headers).status_code==400
    assert path.read_text()=='broken' and calls==[]


def test_preview_expiry_and_no_arbitrary_url(client,app):
    headers,calls=setup(client,app)
    assert post(client,P+'/preview',json={'url':'https://site.invalid/'},headers=headers).status_code==400
    result=post(client,P+'/site',json={'url':'https://site.invalid/'},headers=headers).json
    path=app.extensions['atlas_paths']['data']/'source_discovery.json';state=json.loads(path.read_text());state['expires']=0;path.write_text(json.dumps(state))
    count=len(calls)
    assert post(client,P+'/preview',json={'id':result['candidates'][0]['id']},headers=headers).status_code==400 and len(calls)==count


def test_oversize_and_duplicate_json_fields(client,app):
    headers,calls=setup(client,app)
    assert post(client,P+'/site',data='{"url":"https://site.invalid/","url":"https://other.invalid/"}',content_type='application/json',headers=headers).status_code==400
    assert post(client,P+'/search',json={'query':'a'*5000},headers=headers).status_code==400
    assert calls==[]


def test_configured_search_api_uses_fixed_provider_only(app,monkeypatch):
    from modules.web.atlas_web import create_app
    from tests.test_source_discovery import RSS,HTML,transport
    monkeypatch.setenv('ATLAS_SEARXNG_URL','https://search.invalid/search')
    configured=create_app({},root=app.extensions['atlas_paths']['root']);client=configured.test_client()
    headers,calls=setup(client,configured,{'https://search.invalid/search?q=technologie&format=json&pageno=1&safesearch=1':json.dumps({'results':[{'url':'https://site.invalid/'}]}).encode(),'https://site.invalid/':HTML,'https://site.invalid/feed':RSS})
    assert get(client,P+'/status').json['search_available']
    result=post(client,P+'/search',json={'query':'technologie'},headers=headers)
    assert result.status_code==200 and any(c['format']=='rss20' for c in result.json['candidates'])
    assert calls[0].startswith('https://search.invalid/search?')
    assert post(client,P+'/search',json={'query':'technologie','endpoint':'https://evil.invalid/'},headers=headers).status_code==400
