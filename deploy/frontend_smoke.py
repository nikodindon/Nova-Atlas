"""Disposable CI only: actual Flask + Node + nginx, synthetic data, no producer."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from deploy.init import initialize
from deploy.smoke import ROOT, run, _REDACTIONS, diagnostic


def main(*,allow_local=False):
    if os.environ.get('GITHUB_ACTIONS')!='true' and not allow_local:raise SystemExit('CI-only smoke; explicit --local-fixtures required outside CI')
    if allow_local and shutil.disk_usage(tempfile.gettempdir()).free<4*1024**3:
        raise SystemExit('Local smoke requires at least 4 GiB free staging space plus Docker image capacity')
    with tempfile.TemporaryDirectory(prefix='atlas-frontend-smoke-') as temporary:
        root=Path(temporary)/'state';password=secrets.token_urlsafe(32);day=datetime.now(ZoneInfo('Europe/Paris')).strftime('%Y%m%d')
        initialize(root,'synthetic-admin',password)
        _REDACTIONS[:]=[password,*[p.read_text().strip() for p in (root/'secrets').iterdir() if p.is_file()]]
        (root/f'data/articles/{day}_articles.json').write_text(json.dumps([{'hash':'a'*32,'title':'Synthetic headline for frontend','title_original':'Synthetic headline','summary':'An isolated news fixture.','category':'tech','source':'Fixture','link':'https://fixture.invalid/article','timestamp':datetime.now(timezone.utc).isoformat(),'pub_date':datetime.now(timezone.utc).isoformat()}]))
        (root/f'data/reports/{day}_report.md').write_text('# Synthetic report\n\nA **synthetic** document.\n')
        from deploy.rc_validation import seed_media
        import yaml
        image=seed_media(root,json.loads((root/f'data/articles/{day}_articles.json').read_text()))
        config=yaml.safe_load((root/'config/config.yaml').read_text());config['media']={'mode':'approved_only'}
        (root/'config/config.yaml').write_text(yaml.safe_dump(config))
        from modules.editorial.index import EventIndex
        index=EventIndex(root/'data/articles',root/'data');preview=index.rebuild();index.rebuild(apply=True,expected=preview['input_sha'])
        identity=uuid.uuid4().hex[:10]
        images=['nova-atlas:s12-test-'+identity,'nova-atlas-frontend:s12-test-'+identity,'nova-atlas-frontend-gateway:s12-test-'+identity]
        with (root/'compose.env').open('a') as out:out.write('ATLAS_IMAGE='+images[0]+'\nATLAS_FRONTEND_IMAGE='+images[1]+'\nATLAS_FRONTEND_GATEWAY_IMAGE='+images[2]+'\n');out.write('ATLAS_WEB_PORT=0\nATLAS_FRONTEND_PORT=0\nATLAS_PUBLIC_SCHEME=http\nATLAS_COOKIE_SECURE=0\n')
        command=['docker','compose','-p','atlas-frontend-ci-'+uuid.uuid4().hex[:8],'-f',str(ROOT/'docker-compose.yml'),'-f',str(ROOT/'docker-compose.frontend.yml'),'--env-file',str(root/'compose.env'),'--profile','web','--profile','frontend']
        restored_command=None
        try:
            run(command+['config','--quiet']);run(command+['build','web','frontend','frontend-gateway'])
            run(command+['up','-d','--wait','--wait-timeout','150','web','frontend','frontend-gateway'])
            print('Runtime versions:',run(command+['exec','-T','frontend','node','--version']).stdout.strip(),run(command+['exec','-T','frontend-gateway','nginx','-v']).stdout.strip(),flush=True)
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
            browser=subprocess.run(['node',str(ROOT/'frontend/tests/rc-proxy.mjs')],cwd=ROOT/'frontend',input=json.dumps({'origin':origin,'username':'synthetic-admin','password':password,'day':day}),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
            if browser.returncode:diagnostic(browser.stdout);raise RuntimeError('RC proxy browser failed')
            print(browser.stdout,flush=True)
            run(command+['restart','frontend']);run(command+['up','-d','--wait','--wait-timeout','90','frontend'])
            with request('/healthz') as response:assert response.read()==b'ok'
            for service in ('frontend','frontend-gateway'):
                cid=run(command+['ps','-q',service]).stdout.strip()
                assert run(['docker','inspect','--format','{{.HostConfig.ReadonlyRootfs}}',cid]).stdout.strip()=='true'
            logs=run(command+['logs','--no-color','web','frontend','frontend-gateway']).stdout
            assert all(secret not in logs for secret in _REDACTIONS)
            from deploy.recovery import snapshot,restore,RecoveryError
            from deploy.preflight import check
            import hashlib
            with request(image['url']) as response:expected_image=hashlib.sha256(response.read()).hexdigest()
            try:snapshot(root,Path(temporary)/'must-not-exist',apply=True,stopped=True,include_secrets=True)
            except RecoveryError as error:assert str(error)=='producer_active'
            else:raise AssertionError('live snapshot accepted')
            run(command+['stop','frontend','frontend-gateway','web'])
            snapshot(root,Path(temporary)/'snapshot',apply=True,stopped=True,include_secrets=True)
            restored=Path(temporary)/'restored'
            restore(Path(temporary)/'snapshot',restored,apply=True)
            env=restored/'compose.env';env.write_text(env.read_text().replace('ATLAS_STORAGE='+str(root)+'\n','ATLAS_STORAGE='+str(restored)+'\n'))
            report=check(restored,inspect_data=True,profiles=('frontend',))
            assert not any(row['status']=='fail' for row in report['checks'])
            run(command+['down','--volumes','--remove-orphans'])
            restored_command=list(command);restored_command[restored_command.index('-p')+1]+='-restore'
            restored_command[restored_command.index('--env-file')+1]=str(env)
            run(restored_command+['up','-d','--no-build','--wait','--wait-timeout','150','web','frontend','frontend-gateway'])
            port=run(restored_command+['port','frontend-gateway','8080']).stdout.strip().rsplit(':',1)[1];origin='http://127.0.0.1:'+port
            assert obj('/api/public/articles?date='+day)['total']==1
            with request(image['url']) as response:assert hashlib.sha256(response.read()).hexdigest()==expected_image
            assert obj('/api/auth/login')['configured']
            print('RC restoration Docker PASS: producers stopped, all file hashes/modes/mtimes verified, new directory/project, retargeted synthetic env, reused images, actual Flask/Node/nginx restarted, approved image bytes preserved. No production data.')
            print('Frontend smoke PASS: pinned images; non-root/read-only; nginx same-origin; actual Flask JSON/CSRF/session/logout; Node SSR; nonce CSP; restart; no News/Radio/LLM/TTS/external source.')
        except Exception:
            diagnostic(run(command+['logs','--no-color','web','frontend','frontend-gateway'],check=False).stdout)
            raise
        finally:
            if restored_command is not None:run(restored_command+['down','--volumes','--remove-orphans'],check=False)
            run(command+['down','--volumes','--remove-orphans'],check=False)
            run(['docker','image','rm',*images],check=False)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-fixtures',action='store_true',help='Explicit isolated smoke; new temporary data, unique project and image tags only')
    main(allow_local=parser.parse_args().local_fixtures)
