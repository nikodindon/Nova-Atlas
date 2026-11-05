"""Every HTTP operation uses Flask's client and synthetic state under tmp_path."""
import json
import pytest
from modules.http import external as http
from modules.fetch import ingestion
from tests.test_admin_security import app,password_hash,client,get,post,login,csrf

PREFIX='/api/admin/sources'


def auth(client):
    login(client);return {'X-CSRF-Token':csrf(client)}


@pytest.mark.parametrize('path',[PREFIX,PREFIX+'/stats',PREFIX+'/export',PREFIX+'/bad','/config/feeds'])
def test_all_reads_private(client,path):assert get(client,path).status_code==401


@pytest.mark.parametrize('path',[PREFIX,PREFIX+'/test',PREFIX+'/import',PREFIX+'/actions/category'])
def test_all_writes_auth_csrf(client,path):
    assert post(client,path,json={}).status_code==401
    login(client)
    assert post(client,path,json={}).status_code==403
    assert post(client,path,json={},headers={'X-CSRF-Token':'bad'}).status_code==403


def test_list_detail_filter_stats_version_crud(client):
    headers=auth(client);listing=get(client,PREFIX).json;version=listing['version'];sid=listing['sources'][0]['id']
    assert get(client,PREFIX+'/'+sid).json['source']['health']=='never_checked'
    assert get(client,PREFIX+'?q=missing').json['total']==0
    assert get(client,PREFIX+'?category=tech&health=never_checked&per_page=1').json['total']==1
    assert get(client,PREFIX+'/stats').json['stats']['active']==1
    data={'category':'tech','url':'https://new.invalid/rss','version':version}
    result=post(client,PREFIX,json=data,headers=headers);assert result.status_code==200
    assert post(client,PREFIX,json={**data,'url':'https://lost.invalid/rss'},headers=headers).status_code==409
    row=result.json['sources'][-1];version=result.json['version']
    result=client.patch(PREFIX+'/'+row['id'],base_url='https://localhost',json={'enabled':False,'name':'<img src=x onerror=alert(1)>','version':version},headers=headers)
    assert result.status_code==200 and result.json['sources'][-1]['health']=='disabled'
    result=client.delete(PREFIX+'/'+row['id'],base_url='https://localhost',json={'version':result.json['version']},headers=headers)
    assert result.status_code==200 and len(result.json['sources'])==1
    assert result.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('query',['page=0','page=x','per_page=101','unknown=x','page=1&page=2'])
def test_invalid_query(client,query):
    auth(client);assert get(client,PREFIX+'?'+query).status_code==400


@pytest.mark.parametrize('body',[{}, {'category':'x','url':'http://127.0.0.1/','version':'a'*64},{'category':[],'url':'https://x.invalid/','version':'a'*64},{'version':4},{'unknown':'x'}, {'version':'a'*64,'url':'https://x.invalid/?api_key=secret','category':'x'}])
def test_invalid_bodies_no_file_change(client,app,body):
    headers=auth(client);path=app.extensions['atlas_paths']['feeds_file'];old=path.read_bytes()
    assert post(client,PREFIX,json=body,headers=headers).status_code in (400,409)
    assert path.read_bytes()==old


def test_duplicate_json_fields_rejected(client):
    headers=auth(client)
    assert post(client,PREFIX+'/test',data='{"url":"https://ok.invalid/","url":"https://bad.invalid/"}',content_type='application/json',headers=headers).status_code==400


@pytest.mark.parametrize('fmt',['yaml','opml'])
def test_import_dry_run_confirm_export(client,app,fmt):
    headers=auth(client);path=app.extensions['atlas_paths']['feeds_file'];old=path.read_bytes()
    output=get(client,PREFIX+'/export?format='+fmt);assert output.status_code==200
    preview=post(client,PREFIX+'/import',json={'format':fmt,'content':output.text},headers=headers)
    assert preview.status_code==200 and preview.json['dry_run'] and path.read_bytes()==old
    assert preview.json['duplicates']==1
    result=post(client,PREFIX+'/import',json={'format':fmt,'content':output.text,'apply':True,'version':preview.json['version'],'fingerprint':preview.json['fingerprint'],'preview_token':preview.json['preview_token']},headers=headers)
    assert result.status_code==200 and len(result.json['sources'])==1
    assert path.with_name('feeds.yaml.bak').read_bytes()==old


def test_import_confirmation_required(client,app):
    headers=auth(client);old=app.extensions['atlas_paths']['feeds_file'].read_bytes()
    result=post(client,PREFIX+'/import',json={'format':'yaml','content':'new:\n - https://new.invalid/rss\n','apply':True},headers=headers)
    assert result.status_code==409 and app.extensions['atlas_paths']['feeds_file'].read_bytes()==old


@pytest.mark.parametrize('fixture,expected,fmt',[('rss20.xml','success','rss20'),('atom10.xml','success','atom10')])
def test_source_test_parser_no_add(client,app,monkeypatch,fixture,expected,fmt):
    from pathlib import Path
    payload=(Path(__file__).parent/'fixtures/feeds'/fixture).read_bytes()
    monkeypatch.setattr(http,'get',lambda url,**kw:http.Response(payload,200,{'content-type':'application/xml','etag':'"one"'},url))
    headers=auth(client);path=app.extensions['atlas_paths']['feeds_file'];old=path.read_bytes()
    result=post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers)
    assert result.status_code==200
    assert result.json['result']['state']==expected and result.json['result']['format']==fmt
    assert path.read_bytes()==old and not app.extensions['atlas_paths']['data'].joinpath('feed_ingestion_state.json').exists()
    assert len(result.json['result']['titles'])<=3
    again=post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers)
    assert again.json['result']['deferred']


@pytest.mark.parametrize('failure,state,code',[(http.HTTPStatusError(304),'permanently_unavailable',304),(http.HTTPStatusError(429,'120'),'temporarily_unavailable',429),(http.HTTPStatusError(503),'temporarily_unavailable',503),(http.HTTPStatusError(404),'permanently_unavailable',404),(http.FetchError('deadline'),'temporarily_unavailable',None),(http.BlockedURL('private_ip'),'blocked_by_security',None)])
def test_source_test_errors(client,monkeypatch,failure,state,code):
    def fail(*args,**kwargs):raise failure
    monkeypatch.setattr(http,'get',fail);headers=auth(client)
    response=post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers)
    assert response.status_code==200
    assert response.json['result']['state']==state and response.json['result']['http_status']==code


@pytest.mark.parametrize('payload',[b'<rss>',b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss version="2.0"><channel/></rss>'])
def test_test_invalid_xml(client,monkeypatch,payload):
    monkeypatch.setattr(http,'get',lambda url,**kw:http.Response(payload,200,{},url));headers=auth(client)
    assert post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers).json['result']['state']=='invalid_feed'


def test_test_304_cache_and_repeat_limit(client,app,monkeypatch):
    from pathlib import Path
    payload=(Path(__file__).parent/'fixtures/feeds/rss20.xml').read_bytes();calls=[]
    monkeypatch.setattr(http,'get',lambda url,**kw:(calls.append(kw) or http.Response(payload,200,{'etag':'"v1"'},url)))
    headers=auth(client);assert post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers).json['result']['state']=='success'
    path=app.extensions['atlas_paths']['data']/'source_test_state.json';state=json.loads(path.read_text())
    for rec in state['records'].values():rec['next_attempt_at']=0;rec['last_attempt']=0
    path.write_text(json.dumps(state))
    monkeypatch.setattr(http,'get',lambda url,**kw:(calls.append(kw) or http.Response(b'',304,{},url)))
    result=post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers)
    assert result.json['result']['state']=='not_modified' and calls[-1]['conditional'].etag=='"v1"'


def test_five_tests_per_minute_shared_persistence(client,app,monkeypatch):
    monkeypatch.setattr(http,'get',lambda url,**kw:http.Response(b'<rss version="2.0"><channel/></rss>',200,{},url));headers=auth(client)
    for i in range(5):assert post(client,PREFIX+'/test',json={'url':f'https://public{i}.invalid/rss'},headers=headers).status_code==200
    assert post(client,PREFIX+'/test',json={'url':'https://six.invalid/rss'},headers=headers).status_code==429


def test_legacy_adapters_use_shared_validation_and_reactivate(client):
    headers=auth(client)
    assert post(client,'/config/feeds/toggle',json={'category':'tech','url':'https://example.invalid/feed'},headers=headers).status_code==200
    assert get(client,'/config/feeds').json['stats']['disabled']==1
    assert post(client,'/config/feeds/toggle',json={'category':'tech','url':'https://example.invalid/feed'},headers=headers).status_code==200
    assert get(client,'/config/feeds').json['stats']['active']==1
    assert post(client,'/config/feeds/category',json={'category':'x:\nbad'},headers=headers).status_code==400


def test_source_tests_concurrency_gate_and_lock(client,app,monkeypatch):
    import fcntl,os
    headers=auth(client);lockpath=app.extensions['atlas_paths']['feeds_file'].with_name('source-tests.lock')
    fd=os.open(lockpath,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=post(client,PREFIX+'/test',json={'url':'https://public.invalid/rss'},headers=headers)
        assert result.status_code==429
    finally:os.close(fd)


def test_source_test_does_not_reset_news_embargo(client,app,monkeypatch):
    from modules.fetch.ingestion import key
    import time
    headers=auth(client);path=app.extensions['atlas_paths']['data']/'feed_ingestion_state.json';url='https://public.invalid/rss'
    payload={'version':1,'records':{key(url):{'status':'temporarily_unavailable','http_status':429,'last_attempt':time.time(),'next_attempt_at':time.time()+3600,'host_until':time.time()+3600,'host':key('public.invalid')}}}
    path.write_text(json.dumps(payload));original=path.read_bytes()
    monkeypatch.setattr(http,'get',lambda *a,**kw:pytest.fail('News embargo bypassed'))
    response=post(client,PREFIX+'/test',json={'url':url},headers=headers)
    assert response.json['result']['deferred'] and path.read_bytes()==original


def test_import_hostile_categories_or_tokens_not_reflected(client):
    headers=auth(client)
    for content in ['<svg onload=x>:\n - https://good.invalid/rss\n','tech:\n - https://u:synthetic-secret@good.invalid/rss\n','tech:\n - https://good.invalid/rss?api_key=synthetic-secret\n']:
        response=post(client,PREFIX+'/import',json={'format':'yaml','content':content},headers=headers)
        assert response.status_code==400 and 'synthetic-secret' not in response.text


def test_null_version_is_not_unconditional_write(client,app):
    headers=auth(client);path=app.extensions['atlas_paths']['feeds_file'];original=path.read_bytes()
    response=post(client,PREFIX,json={'category':'tech','url':'https://new.invalid/rss','version':None},headers=headers)
    assert response.status_code==400 and path.read_bytes()==original
