"""Local Chromium, every browser HTTP request fulfilled by Flask's test client.

No Flask server, real DNS, TTS, RSS, model or remote browser. The existing process
ban is relaxed only for Playwright's local driver/Chromium in this fixture.
"""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from datetime import datetime
import pytest
from playwright.sync_api import sync_playwright
import playwright as playwright_package
from modules.web import atlas_web as web
from tests.test_admin_security import app, password_hash

_REAL_POPEN=subprocess.Popen


@pytest.fixture
def browser_page(app,monkeypatch):
    cache=Path(web.__file__).resolve().parents[2]/'.venv-baseline/pw-browsers'
    if cache.exists():monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(cache))
    driver=Path(playwright_package.__file__).parent/'driver'
    def driver_only(command,*args,**kwargs):
        if (not isinstance(command,(tuple,list)) or len(command)!=3
                or Path(command[0]).resolve()!=(driver/'node').resolve()
                or Path(command[1]).resolve()!=(driver/'package/cli.js').resolve()
                or command[2]!='run-driver'):
            raise AssertionError('Only the local Playwright driver may start in browser tests')
        return _REAL_POPEN(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',driver_only)
    paths=app.extensions['atlas_paths'];day=datetime.now().strftime('%Y%m%d')
    articles=[{'title':'<img src=x onerror="window.pwned=1"> 日本語 العربية',
      'summary':'</script><script>window.pwned=2</script> texte', 'category':cat,
      'source':'<svg onload="window.pwned=3">', 'link':'javascript:window.pwned=4',
      'timestamp':datetime.now().isoformat()} for cat in ('tech','monde')]
    (paths['articles']/f'{day}_articles.json').write_text(json.dumps(articles))
    (paths['reports']/f'{day}_report.md').write_text('# Rapport\n\n**Important**\n\n<script>window.pwned=5</script>')
    errors=[];violations=[];requests=[];injections={}
    test_client=app.test_client(use_cookies=False)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,proxy={'server':'http://127.0.0.1:9'},
            args=['--disable-background-networking','--disable-dev-shm-usage','--host-resolver-rules=MAP * ~NOTFOUND'])
        context=browser.new_context(service_workers='block')
        def route_handler(route):
            request=route.request;url=urlsplit(request.url)
            if url.netloc!='atlas.invalid':
                route.abort();return
            requests.append((request.method,url.path))
            response=test_client.open(url.path+('?' +url.query if url.query else ''),base_url='https://atlas.invalid',
                method=request.method,headers=request.all_headers(),data=request.post_data_buffer)
            body=response.data
            if url.path in injections:
                body=body.replace(b'<div class="page">',b'<div class="page">'+injections[url.path])
            headers=dict(response.headers)
            headers.pop('Content-Length',None)
            route.fulfill(status=response.status_code,headers=headers,body=body)
        context.route('**/*',route_handler)
        page=context.new_page()
        page.set_default_timeout(10000)
        page.clock.install()
        page.add_init_script('''window.violations=[];document.addEventListener('securitypolicyviolation',e=>window.violations.push(e.effectiveDirective));
          Object.defineProperty(HTMLMediaElement.prototype,'paused',{get(){return !this.__playing;}});
          HTMLMediaElement.prototype.play=function(){this.__playing=true;this.dispatchEvent(new Event('play'));return Promise.resolve();};
          HTMLMediaElement.prototype.pause=function(){this.__playing=false;this.dispatchEvent(new Event('pause'));};
          HTMLMediaElement.prototype.load=function(){};
        ''')
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('https://atlas.invalid/live')
        yield page,errors,requests,injections
        violations.extend(page.evaluate('window.violations'))
        browser.close()
    assert errors==[]
    assert violations==[]


def test_hostile_content_csp_theme_filters_and_persistent_audio(browser_page):
    page,errors,requests,injections=browser_page
    assert page.locator('#live-grid img,#live-grid script,#live-grid svg').count()==0
    assert page.evaluate('window.pwned') is None
    page.locator('[data-action="theme"]').click()
    assert page.locator('body.light').count()==1
    page.locator('.filter-btn[data-cat="tech"]').click()
    assert page.locator('#live-grid [data-category="monde"]').is_hidden()
    page.locator('#live-grid [data-category="tech"] [data-action="summary"]').click()
    assert page.locator('.live-summary-text.expanded').count()==1
    page.locator('[data-action="radio-toggle"]').click()
    page.evaluate('window.savedAudio=window._novaAudio')
    page.locator('.topbar-nav a[href="/archives"]').click()
    page.wait_for_url('**/archives')
    assert page.evaluate('window.savedAudio===window._novaAudio && !window._novaAudio.paused')
    page.go_back();page.wait_for_url('**/live')
    page.wait_for_selector('#live-grid')
    assert page.evaluate('window.savedAudio===window._novaAudio')
    assert page.locator('#live-grid [data-category="monde"]').is_hidden()
    assert page.locator('body.light').count()==1


def test_admin_login_feeds_preferences_and_csrf_under_csp(browser_page):
    page,errors,requests,injections=browser_page
    page.goto('https://atlas.invalid/admin/login')
    page.locator('[name="username"]').fill('admin-test')
    page.locator('[name="password"]').fill('synthetic-test-password')
    # Redirects from intercepted HTTP responses bypass Playwright routing.
    # Submit the real form/cookie flow without following the 303, then navigate.
    page.evaluate("async()=>{const form=document.querySelector('form');await fetch(form.action,{method:'POST',body:new FormData(form),redirect:'manual'});}")
    page.goto('https://atlas.invalid/config/yaml')
    page.locator('[data-section="rss"]').click()
    page.locator('#rss-maxfeed').fill('9')
    page.locator('[data-action="saveRss"]').click()
    page.wait_for_function("()=>document.getElementById('save-toast').classList.contains('show')")
    page.goto('https://atlas.invalid/config/feeds/page')
    page.wait_for_selector('.feed-item')
    page.locator('#new-cat-name').fill('nouvelle_categorie')
    page.locator('#new-cat-form button').click()
    page.wait_for_function("()=>document.querySelector('#feeds-container').textContent.includes('nouvelle_categorie')")
    form=page.locator('form[data-cat="nouvelle_categorie"]')
    form.locator('input').fill('https://source.invalid/feed')
    form.locator('button').click()
    page.wait_for_selector('.feed-item[data-cat="nouvelle_categorie"]')
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('.feed-item[data-cat="nouvelle_categorie"] [data-action="remove"]').click()
    page.wait_for_selector('.feed-item[data-cat="nouvelle_categorie"]',state='detached')
    page.locator('#new-cat-name').fill('<svg onload="window.pwned=1">')
    page.locator('#new-cat-form button').click()
    page.wait_for_function("()=>document.querySelector('#toast').classList.contains('error')")
    assert page.evaluate('window.pwned') is None
    page.goto('https://atlas.invalid/preferences')
    page.wait_for_selector('.pref-card')
    page.locator('input[data-cat="tech"]').uncheck()
    page.wait_for_function("()=>document.querySelector('.pref-card.hidden')!==null")
    page.goto('https://atlas.invalid/live')
    assert page.locator('meta[name="csrf-token"]').count()==0
    assert page.locator('input[name="csrf_token"]').count()==0
    # Privileged public-page actions obtain a token only when needed, never in HTML.
    result=page.evaluate("async()=>{const r=await fetch('/api/preferences/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:'tech'})});return r.status;}")
    assert result==200
    assert ('GET','/admin/csrf') in requests


def test_live_refresh_preserves_filter_theme_and_audio(browser_page):
    page,errors,requests,injections=browser_page
    page.locator('.filter-btn[data-cat="tech"]').click()
    page.locator('[data-action="radio-toggle"]').click()
    page.evaluate('window.savedAudio=window._novaAudio')
    before=requests.count(('GET','/live'))
    page.clock.fast_forward(120001)
    page.wait_for_function('()=>document.querySelector(".filter-btn[data-cat=tech]").getAttribute("aria-pressed")==="true"')
    assert requests.count(('GET','/live'))>before
    assert page.locator('#live-grid [data-category="monde"]').is_hidden()
    assert page.evaluate('window._novaAudio===window.savedAudio && !window._novaAudio.paused')


def test_received_scripts_and_handlers_are_not_interpreted(browser_page):
    page,errors,requests,injections=browser_page
    injections['/archives']=b'<script>window.receivedScriptRan=1</script><img src="/missing" onerror="window.receivedHandlerRan=1"><a id="hostile-link" href="javascript:window.receivedHandlerRan=2">bad</a>'
    page.locator('.topbar-nav a[href="/archives"]').click();page.wait_for_url('**/archives')
    assert page.locator('.page script,.page [onerror]').count()==0
    assert page.evaluate('window.receivedScriptRan') is None
    assert page.evaluate('window.receivedHandlerRan') is None
    assert page.locator('#hostile-link').get_attribute('href') is None
    # This deliberately injected raw handler is also blocked by CSP during parsing.
    page.wait_for_function("()=>window.violations.includes('script-src-attr')")
    assert set(page.evaluate('window.violations'))=={'script-src-attr'}
    page.evaluate('window.violations=[]')


def test_csp_blocks_inline_script_and_handler(browser_page):
    page,errors,requests,injections=browser_page
    page.evaluate('''()=>{
      const script=document.createElement('script');script.textContent='window.inlineRan=1';document.body.append(script);
      const button=document.createElement('button');button.setAttribute('onclick','window.inlineHandlerRan=1');document.body.append(button);button.click();
    }''')
    page.wait_for_function('()=>window.violations.length>=2')
    assert page.evaluate('window.inlineRan') is None
    assert page.evaluate('window.inlineHandlerRan') is None
    assert set(page.evaluate('window.violations'))=={'script-src-elem','script-src-attr'}
    page.evaluate('window.violations=[]')  # These two attempted violations were deliberate.


def test_source_manager_test_import_health_and_hostile_names(browser_page,monkeypatch):
    from modules.http import external as http
    page,errors,requests,injections=browser_page
    payload=b'<rss version="2.0"><channel><item><title>&lt;img src=x onerror=window.pwned=1&gt; titre</title><link>https://news.invalid/article</link></item></channel></rss>'
    monkeypatch.setattr(http,'get',lambda url,**kwargs:http.Response(payload,200,{'etag':'"safe"'},url))
    page.goto('https://atlas.invalid/admin/login')
    page.locator('[name="username"]').fill('admin-test');page.locator('[name="password"]').fill('synthetic-test-password')
    page.evaluate("async()=>{const form=document.querySelector('form');await fetch(form.action,{method:'POST',body:new FormData(form),redirect:'manual'});}")
    page.goto('https://atlas.invalid/config/feeds/page');page.wait_for_selector('.feed-item')
    page.locator('#source-test-url').fill('https://test.invalid/rss');page.locator('#source-test-form button').click()
    page.wait_for_function("()=>document.getElementById('source-test-result').textContent.includes('rss20')")
    assert page.locator('.feed-item').count()==1  # Testing is never adding.
    assert page.locator('#source-test-result img').count()==0
    page.locator('details summary').click()
    content='imports:\n  - https://import.invalid/rss\n'
    page.locator('#source-import-content').fill(content);page.locator('#source-import-form button[type="submit"]').click()
    page.wait_for_function("()=>!document.getElementById('source-import-apply').disabled")
    assert page.locator('.feed-item').count()==1
    page.locator('#source-import-apply').click();page.wait_for_selector('.feed-item[data-cat="imports"]')
    page.locator('.feed-item[data-cat="imports"] [data-action="toggle"]').click()
    page.wait_for_function("()=>document.querySelector('.feed-item[data-cat=imports]').textContent.includes('disabled')")
    page.locator('#source-health').select_option('disabled');page.locator('#source-filter button').click()
    page.wait_for_function("()=>document.querySelectorAll('.feed-item').length===1")
    assert page.locator('.feed-item[data-cat="imports"]').count()==1
    page.locator('.feed-item[data-cat="imports"] [data-action="toggle"]').click()
    page.wait_for_function("()=>document.querySelectorAll('.feed-item').length===0")
    page.locator('#source-health').select_option('');page.locator('#source-filter button').click()
    page.wait_for_function("()=>document.querySelectorAll('.feed-item').length===2")
    status=page.evaluate("""async()=>{const listing=await (await fetch('/api/admin/sources')).json();return (await fetch('/api/admin/sources/'+listing.sources[0].id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:listing.version,name:'<img src=x onerror=window.pwned=1>'})})).status;}""")
    assert status==200
    page.reload();page.wait_for_function("()=>document.querySelector('#feeds-container').textContent.includes('<img')")
    assert page.locator('#feeds-container img').count()==0 and page.evaluate('window.pwned') is None
