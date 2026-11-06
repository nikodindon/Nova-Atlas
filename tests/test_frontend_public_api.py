from datetime import datetime,timezone
import json
import pytest
from tests.test_admin_security import app,password_hash,client,get,post,login,csrf
from tests.test_editorial_events import article


def content(app):
    paths=app.extensions['atlas_paths'];day=datetime.now().strftime('%Y%m%d')
    rows=[article(0,when=datetime.now(timezone.utc),title='<img src=x onerror="window.pwned=1"> 日本語 العربية',link='javascript:alert(1)')]
    path=paths['articles']/f'{day}_articles.json';path.write_text(json.dumps(rows));return paths,day,path.read_bytes()


def test_additive_articles_project_safe_bounded_readonly_fields(client,app):
    paths,day,before=content(app)
    response=get(client,'/api/public/articles?per_page=1');assert response.status_code==200
    row=response.json['articles'][0];assert row['link'] is None and row['image']['fallback']
    assert '日本語' in row['title'] and 'locations' not in row
    assert response.json['total']==1 and response.json['page']==1
    assert get(client,'/api/articles?date='+day).json['articles'][0]['title'].startswith('<img') # Historical contract intact.
    assert (paths['articles']/f'{day}_articles.json').read_bytes()==before

@pytest.mark.parametrize('query',['page=0','page=x','per_page=51','date=20260230','date=../../secret','date=20261106&date=20261107','url=http://127.0.0.1','category='+'x'*201])
def test_invalid_public_query(client,query):assert get(client,'/api/public/articles?'+query).status_code==400


def test_archives_and_documents_typed_sanitized_not_html_scraping(client,app):
    paths,day,before=content(app)
    (paths['reports']/f'{day}_report.md').write_text('# Rapport 日本語\n\n**Important**\n\n<script>window.pwned=1</script>\n\n[Bad](javascript:alert(1))\n\n![remote](https://evil.invalid/a.png)')
    (paths['editions']/f'{day}_matin.md').write_text('# Matin\n\nTexte de démonstration.')
    data=get(client,'/api/public/archives').json
    row=next(r for r in data['days'] if r['date']==day);assert row['report'] and row['articles'] and row['editions']==['matin']
    document=get(client,f'/api/public/documents/{day}/report').json
    assert document['title']=='Rapport 日本語' and 'nodes' in document
    raw=json.dumps(document);assert 'window.pwned' not in raw and 'javascript:' not in raw and 'evil.invalid' not in raw
    assert get(client,f'/api/public/documents/{day}/matin').status_code==200
    assert get(client,f'/api/public/documents/{day}/missing').status_code==400
    assert get(client,'/api/public/documents/20000101/report').status_code==404


def test_symlink_and_corrupt_article_file_refused(client,app,tmp_path):
    paths,day,_=content(app);path=paths['articles']/f'{day}_articles.json';path.unlink()
    target=tmp_path/'private.json';target.write_text('private synthetic fixture');path.symlink_to(target)
    response=get(client,'/api/public/articles');assert response.status_code==503 and 'private synthetic' not in response.text
    path.unlink();path.write_text('{broken');assert get(client,'/api/public/articles').status_code==503 and path.read_text()=='{broken'


def test_public_site_no_backend_credentials_or_private_source_urls(client):
    response=get(client,'/api/public/site');assert response.status_code==200
    assert response.json['streams']=={'primary':'/stream/nova','android':'/stream/nova-android'}
    for forbidden in ('password','api_key','synthetic-icecast','https://example.invalid/feed'):
        assert forbidden not in response.text
    assert get(client,'/api/public/site?unknown=x').status_code==400


def test_login_json_bootstrap_reuses_flask_session_csrf_and_logout(client,app):
    response=get(client,'/api/auth/login');assert response.status_code==200 and response.json['configured']
    token=response.json['csrf_token'];assert response.headers['Cache-Control']=='no-store'
    assert get(client,'/admin/csrf').status_code==401
    assert post(client,'/admin/login',data={'username':'admin-test','password':'synthetic-test-password','csrf_token':token}).status_code==303
    assert post(client,'/admin/logout',headers={'X-CSRF-Token':csrf(client)}).status_code==303
    assert get(client,'/admin/csrf').status_code==401


def test_no_token_in_public_pages_or_api_and_auth_responses_not_cached(client,app):
    content(app)
    assert 'csrf_token' not in get(client,'/api/public/articles').text
    login(client)
    assert get(client,'/api/public/articles').headers['Cache-Control']=='no-store'


def test_events_projection_uses_same_derived_index(client,app):
    from tests.test_editorial_api import prepare
    _,result=prepare(app)
    response=get(client,'/api/public/events');assert response.status_code==200 and response.json['total']==1
    event=response.json['events'][0];assert event['independent_confirmation'] is False and event['image']['fallback']
    assert get(client,'/api/public/events/'+event['event_id']).json['event']['article_count']==2
    assert get(client,'/api/public/events/not-found').status_code==404
    assert get(client,'/api/events').json['events'][0]['event_id']==event['event_id']


def test_login_bootstrap_closed_without_configuration(app,monkeypatch):
    from modules.web.atlas_web import create_app
    monkeypatch.delenv('ATLAS_ADMIN_USERNAME')
    closed=create_app({},root=app.extensions['atlas_paths']['root'])
    response=get(closed.test_client(),'/api/auth/login')
    assert response.status_code==503 and not response.json['configured']
    assert response.headers['Cache-Control']=='no-store'


def test_public_document_complexity_is_bounded(client,app):
    paths=app.extensions['atlas_paths']
    (paths['reports']/'20261106_report.md').write_text('\n\n'.join('paragraph' for _ in range(2100)))
    assert get(client,'/api/public/documents/20261106/report').status_code==503
