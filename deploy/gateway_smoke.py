"""Disposable CI only, complete gateway path with a synthetic HTTP provider."""
import os
from pathlib import Path
import secrets
import tempfile
import uuid
import yaml
from deploy.init import initialize
from deploy.gateway import initialize as gateway_init
from deploy import smoke


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    with tempfile.TemporaryDirectory(prefix='atlas-gateway-ci-') as tmp:
        state=Path(tmp)/'state';initialize(state,'synthetic-admin',secrets.token_urlsafe(32))
        gateway_init(state,'synthetic-model','http://fake-provider:8080/v1')
        path=state/'config/config.yaml';cfg=yaml.safe_load(path.read_text())
        cfg['llm'].update(provider='litellm',base_url='http://litellm:4000',model='atlas-fast',gateway={'policy':'config/llm_routes.yaml'})
        path.write_text(yaml.safe_dump(cfg))
        smoke._REDACTIONS[:]=[p.read_text().strip() for p in (state/'secrets').iterdir() if p.stat().st_size]
        fake=Path(tmp)/'fake.yml'
        fake.write_text(yaml.safe_dump({'services':{'fake-provider':{
            'image':'nova-atlas:02-foundation','profiles':['gateway'],'user':f'{os.getuid()}:{os.getgid()}',
            'entrypoint':['python','/fake_llm.py'],'networks':['llm_transport'],'read_only':True,
            'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
            'volumes':[str(smoke.ROOT/'deploy/fake_llm.py')+':/fake_llm.py:ro'],
            'healthcheck':{'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')"],'interval':'2s','timeout':'3s','retries':10}}}}))
        command=['docker','compose','-p','atlas-gateway-ci-'+uuid.uuid4().hex[:8],'-f',str(smoke.ROOT/'docker-compose.yml'),'-f',str(smoke.ROOT/'docker-compose.gateway.yml'),'-f',str(fake),'--env-file',str(state/'compose.env'),'--profile','gateway']
        try:
            print('Gateway smoke: build and start isolated proxy/relay/fake',flush=True)
            smoke.run(command+['config','--quiet']);smoke.run(command+['build','web','litellm'])
            smoke.run(command+['up','-d','--wait','--wait-timeout','180','fake-provider','litellm'])
            code="""from modules.core.config import load_config
from modules.core.llm_client import OllamaClient
from modules.core.llm_gateway import ALIASES,post_json,GatewayError
cfg=load_config('/app/config/config.yaml');client=OllamaClient(cfg)
for alias in ALIASES:
 assert client.call('Réponds en français 🌍',model=alias,caller='atlas',timeout=30)=='Réponse synthétique française 🌍'
try:post_json('http://litellm:4000/v1/chat/completions',{'model':'atlas-fast','messages':[{'role':'user','content':'fake'}]},'wrong-key',5)
except GatewayError as exc:assert exc.kind=='authentication'
else:raise AssertionError('Unauthenticated gateway accepted')
"""
            probe=['run','--rm','--no-deps','--entrypoint','python','news','-c',code]
            smoke.run(command+probe)
            errors="""import time
from modules.core.config import load_config
from modules.core.llm_client import OllamaClient
client=OllamaClient(load_config('/app/config/config.yaml'))
start=time.monotonic()
assert client.call('SIMULATE_429',caller='fetch',timeout=30)
assert time.monotonic()-start>=2
assert client.call('SIMULATE_500',caller='fetch',timeout=30)==''
"""
            smoke.run(command+['run','--rm','--no-deps','--entrypoint','python','news','-c',errors])
            counts="import json,urllib.request;data=json.load(urllib.request.urlopen('http://127.0.0.1:8080/counts'));assert data['SIMULATE_429']==2;assert data['SIMULATE_500']==2"
            smoke.run(command+['exec','-T','fake-provider','python','-c',counts])
            smoke.run(command+['stop','litellm','llm-egress'])
            for service in ('litellm','llm-egress'):
                container=smoke.run(command+['ps','-aq',service]).stdout.strip()
                assert smoke.run(['docker','inspect','--format','{{.State.ExitCode}}',container]).stdout.strip() in ('0','143')
            smoke.run(command+['up','-d','--wait','--wait-timeout','180','litellm'])
            smoke.run(command+probe)
            logs=smoke.run(command+['logs','--no-color']).stdout
            for value in smoke._REDACTIONS:assert value not in logs
            print('Gateway smoke PASS: five aliases, language, auth, secret isolation, health, SIGTERM and restart. No external providers.',flush=True)
        except BaseException:
            smoke.diagnostic(smoke.run(command+['logs','--no-color','--tail','80'],check=False).stdout)
            raise
        finally:smoke.run(command+['down','--remove-orphans'],check=False)

if __name__=='__main__':main()
