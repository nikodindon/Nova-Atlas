"""Hostile synthetic input, isolated Flask client, no real sources or credentials."""
from datetime import datetime
import json
from pathlib import Path
import re
from bs4 import BeautifulSoup
import pytest
from modules.web import atlas_web as web
from modules.web.web_security import safe_url, render_markdown, CSP
from tests.test_admin_security import app, client, password_hash, get, login, post, csrf
from tests.test_xss_reproductions import hostile


@pytest.mark.parametrize('value', ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>',
                                  '//evil.invalid/', 'https://user:password@host.invalid/',
                                  'https://host.invalid/\nscript', 'https:\\evil.invalid',
                                  'java\tscript:alert(1)', '/admin/logout', '', None])
def test_external_link_policy(value):
    assert safe_url(value) == '#'


@pytest.mark.parametrize('value', ['https://news.example/記事?q=العربية', 'http://news.example/a?x=1&y=2'])
def test_external_unicode_url_preserved(value):
    assert safe_url(value) == value


@pytest.mark.parametrize('payload', ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
 '<svg><a xlink:href="javascript:alert(1)">bad</a></svg>', '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=alert(1)>">',
 '<a href="jav&#x61;script:alert(1)">link</a>', '<a href="data:text/html,test">link</a>',
 '<iframe srcdoc="<script>alert(1)</script>"></iframe>', '<form action=/admin/logout><button>bad</button></form>',
 '<p style="background:url(https://evil.invalid)">text</p>'])
def test_markdown_allowlist_removes_executable_content(payload):
    soup=BeautifulSoup(str(render_markdown(payload)), 'html.parser')
    assert not soup.select('script,img,svg,math,iframe,object,embed,style,form,button,input')
    for tag in soup.select('*'):
        assert not any(name.startswith('on') or name in ('style','srcdoc') for name in tag.attrs)
    assert not any(a.get('href','').startswith(('javascript:','data:')) for a in soup.select('a'))


def test_markdown_legitimate_formatting_preserved():
    result=render_markdown('## 日本語\n\n**重要** et *العربية*\n\n- point\n\n[Source](https://news.example/a)\n\n| A | B |\n|---|---|\n|1|2|')
    soup=BeautifulSoup(str(result),'html.parser')
    assert soup.h2.text=='日本語' and soup.strong.text=='重要' and soup.em.text=='العربية'
    assert soup.select_one('li') and soup.table and soup.a['rel']==['noopener','noreferrer']


@pytest.mark.parametrize('path', ['/', '/live', '/archives', '/index.html'])
def test_public_html_has_only_external_scripts_and_no_tokens(client, hostile, path):
    response=get(client,path)
    assert response.status_code==200
    soup=BeautifulSoup(response.data,'html.parser')
    assert all(script.get('src','').startswith('/assets/') for script in soup.select('script'))
    assert not soup.select('meta[name="csrf-token"],input[name="csrf_token"]')
    assert not any(any(key.startswith('on') for key in tag.attrs) for tag in soup.select('*'))
    assert 'synthetic-llm-credential' not in response.text


def test_authenticated_public_page_still_has_no_admin_token(client):
    login(client)
    response=get(client,'/live')
    assert 'csrf-token' not in response.text and 'csrf_token' not in response.text
    assert '/admin/logout' not in response.text


def test_hostile_attributes_category_source_and_json(client, app):
    paths=app.extensions['atlas_paths'];day=datetime.now().strftime('%Y%m%d')
    payload='\"><svg onload="window.pwned=1"> 日本語 </script><script>window.pwned=2</script>'
    article={key:payload for key in ('title','summary','source','category')}
    article.update(timestamp=datetime.now().isoformat(),link='https://news.example/\"onmouseover=alert(1)')
    (paths['articles']/f'{day}_articles.json').write_text(json.dumps([article]))
    soup=BeautifulSoup(get(client,'/live').data,'html.parser')
    assert not soup.select('#live-grid svg,#live-grid script')
    assert payload in soup.get_text()
    response=get(client,'/api/articles?date='+day)
    assert response.json['articles'][0]['title']==payload  # Keep Android JSON contracts.
    assert response.mimetype=='application/json' and response.headers['X-Content-Type-Options']=='nosniff'


def test_breaking_banner_escapes_url_and_branding(app, monkeypatch):
    monkeypatch.setattr(web,'get_breaking_news',lambda *_:[{'title':'<img src=x onerror=alert(1)>','link':'javascript:alert(1)','cat':'tech','count':3}])
    html=web.render_breaking_banner(app.extensions['atlas_paths'])
    soup=BeautifulSoup(str(html),'html.parser')
    assert not soup.img and soup.a['href']=='#'


@pytest.mark.parametrize('path', ['/', '/admin/login', '/missing', '/api/articles', '/assets/web.js'])
def test_policy_on_html_json_errors_and_assets(client,path):
    response=get(client,path)
    assert response.headers['Content-Security-Policy']==CSP
    assert "script-src 'self'" in CSP and 'unsafe-eval' not in CSP
    assert "script-src 'self' 'unsafe-inline'" not in CSP
    assert response.headers['X-Frame-Options']=='DENY'
    assert response.headers['Permissions-Policy'] and response.headers['Referrer-Policy']=='same-origin'
    assert 'Strict-Transport-Security' not in response.headers


def test_admin_configuration_is_escaped_and_csrf_enforced(client, app):
    login(client)
    payload='\"><script>window.pwned=1</script> العربية'
    response=post(client,'/config/yaml/save',json={'section':'service','key':'name','value':payload},headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==400  # Existing validation must remain strict.
    # Historical YAML can predate that validation: rendering is still safe.
    import yaml
    config_path=app.extensions['atlas_paths']['config_file']
    config=yaml.safe_load(config_path.read_text())
    config.setdefault('service',{})['name']=payload
    config_path.write_text(yaml.safe_dump(config))
    response=get(client,'/config/yaml')
    soup=BeautifulSoup(response.data,'html.parser')
    assert soup.select_one('#svc-name')['value']==payload
    assert not any(script.text.strip() for script in soup.select('script'))
    assert post(client,'/config/yaml/save',json={'section':'service','key':'name','value':'Changed'}).status_code==403


def test_hsts_opt_in_only_on_trusted_https(app, monkeypatch):
    monkeypatch.setenv('ATLAS_HSTS_MAX_AGE','86400')
    monkeypatch.setenv('ATLAS_COOKIE_SECURE','false')
    client=web.create_app({},root=app.extensions['atlas_paths']['root']).test_client()
    assert client.get('/',base_url='https://localhost').headers['Strict-Transport-Security']=='max-age=86400'
    assert 'Strict-Transport-Security' not in client.get('/',headers={'X-Forwarded-Proto':'https'}).headers


def test_asset_allowlist_cannot_read_repository_files(client):
    for path in ('/assets/atlas_web.py','/assets/../admin_security.py','/assets/config.yaml'):
        assert get(client,path).status_code==404
    assert get(client,'/assets/web.js').status_code==200


def test_all_active_js_is_static_without_dynamic_interpreter():
    for path in Path(web.__file__).with_name('assets').glob('*.js'):
        source=path.read_text()
        assert not re.search(r'\beval\s*\(|\bnew\s+Function\s*\(|\.innerHTML\s*=|\.outerHTML\s*=|document\.write\s*\(',source)


def test_edition_renderer_sanitizes_body_and_escapes_title(client, app):
    paths=app.extensions['atlas_paths'];day=datetime.now().strftime('%Y%m%d')
    (paths['editions']/f'{day}_matin.md').write_text('# Édition\n\n---\n\n<img src=x onerror="window.pwned=1">\n\n**Important**\n\n<script>window.pwned=2</script>')
    response=get(client,f'/editions/{day}_matin.html')
    assert response.status_code==200
    soup=BeautifulSoup(response.data,'html.parser')
    assert not soup.select('.edition-title img,.edition-body script,.edition-body img')
    assert soup.select_one('.edition-body strong').text=='Important'


def test_static_export_includes_only_owned_scripts_and_sanitized_html(app):
    root=app.extensions['atlas_paths']['root'];day=datetime.now().strftime('%Y%m%d')
    (root/'data/reports'/f'{day}_report.md').write_text('# Rapport\n\n<script>window.pwned=1</script>\n\n**Important**')
    web.generate_static_site({},full=True,root=root)
    soup=BeautifulSoup((root/'site'/f'{day}_report.html').read_text(),'html.parser')
    assert not soup.select('.report-body script')
    assert (root/'site/assets/web.js').exists()
    assert all(script.get('src','').startswith('/assets/') for script in soup.select('script'))
