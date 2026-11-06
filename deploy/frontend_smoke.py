"""Disposable CI only: actual Flask + Node + nginx, synthetic data, no producer."""
from datetime import datetime
from zoneinfo import ZoneInfo
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from deploy.init import initialize
from deploy.smoke import ROOT, run, _REDACTIONS


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    with tempfile.TemporaryDirectory(prefix='atlas-frontend-smoke-') as temporary:
        root=Path(temporary)/'state';password=secrets.token_urlsafe(32);day=datetime.now(ZoneInfo('Europe/Paris')).strftime('%Y%m%d')
        initialize(root,'synthetic-admin',password)
        _REDACTIONS[:]=[password,*[p.read_text().strip() for p in (root/'secrets').iterdir() if p.is_file()]]
        (root/f'data/articles/{day}_articles.json').write_text(json.dumps([{'hash':'a'*32,'title':'Synthetic headline for frontend','title_original':'Synthetic headline','summary':'An isolated news fixture.','category':'tech','source':'Fixture','link':'https://fixture.invalid/article','timestamp':'2026-11-06T12:00:00+01:00','pub_date':'2026-11-06T11:00:00Z'}]))
        (root/f'data/reports/{day}_report.md').write_text('# Synthetic report\n\nA **synthetic** document.\n')
        with (root/'compose.env').open('a') as out:out.write('ATLAS_WEB_PORT=0\nATLAS_FRONTEND_PORT=0\nATLAS_PUBLIC_SCHEME=http\nATLAS_COOKIE_SECURE=0\n')
        command=['docker','compose','-p','atlas-frontend-ci-'+uuid.uuid4().hex[:8],'-f',str(ROOT/'docker-compose.yml'),'-f',str(ROOT/'docker-compose.frontend.yml'),'--env-file',str(root/'compose.env'),'--profile','web','--profile','frontend']
        try:
            run(command+['config','--quiet']);run(command+['build','web','frontend','frontend-gateway'])
            run(command+['up','-d','--wait','--wait-timeout','150','web','frontend','frontend-gateway'])
            port=run(command+['port','frontend-gateway','8080']).stdout.strip().rsplit(':',1)[1]
            origin='http://127.0.0.1:'+port
            jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            def request(path,body=None,headers=None):
                return client.open(urllib.request.Request(origin+path,data=body,headers=headers or {}),timeout=15)
            def obj(path):
                with request(path) as response:return json.load(response)
            with request('/direct') as response:
                html=response.read().decode();csp=response.headers['Content-Security-Policy']
                assert 'Synthetic headline for frontend' in html
                assert 'nonce-' in csp and 'unsafe-eval' not in csp and 'unsafe-inline' not in csp
                assert response.headers['X-Content-Type-Options']=='nosniff'
            assert obj('/api/public/articles?date='+day)['total']==1
            assert 'nodes' in obj('/api/public/documents/'+day+'/report')
            bootstrap=obj('/api/auth/login');assert bootstrap['configured']
            login=urllib.parse.urlencode({'username':'synthetic-admin','password':password,'csrf_token':bootstrap['csrf_token']}).encode()
            with request('/admin/login',login,{'Content-Type':'application/x-www-form-urlencoded'}):pass
            with request('/administration') as response:
                assert 'Administration' in response.read().decode()
                assert 'no-store' in response.headers['Cache-Control']
            with request('/administration/sources') as response:assert response.status==200
            try:request('/api/admin/sources',b'{}',{'Content-Type':'application/json'})
            except urllib.error.HTTPError as error:assert error.code==403
            else:raise AssertionError('CSRF missing')
            token=obj('/admin/csrf')['csrf_token']
            with request('/admin/logout',b'',{'X-CSRF-Token':token}):pass
            try:obj('/admin/csrf')
            except urllib.error.HTTPError as error:assert error.code==401
            else:raise AssertionError('Logout did not invalidate session')
            run(command+['restart','frontend']);run(command+['up','-d','--wait','--wait-timeout','90','frontend'])
            with request('/healthz') as response:assert response.read()==b'ok'
            for service in ('frontend','frontend-gateway'):
                cid=run(command+['ps','-q',service]).stdout.strip()
                assert run(['docker','inspect','--format','{{.HostConfig.ReadonlyRootfs}}',cid]).stdout.strip()=='true'
            logs=run(command+['logs','--no-color','web','frontend','frontend-gateway']).stdout
            assert all(secret not in logs for secret in _REDACTIONS)
            run(command+['stop','frontend','frontend-gateway'])
            print('Frontend smoke PASS: pinned images; non-root/read-only; nginx same-origin; actual Flask JSON/CSRF/session/logout; Node SSR; nonce CSP; restart; no News/Radio/LLM/TTS/external source.')
        finally:run(command+['down','--volumes','--remove-orphans'],check=False)

if __name__=='__main__':main()
