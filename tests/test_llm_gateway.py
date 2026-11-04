"""All DNS/HTTP/providers simulated; only POSIX test locks are real."""
import copy
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import threading
import subprocess
_REAL_POPEN=subprocess.Popen
import urllib.error
from unittest.mock import Mock
import yaml
import pytest
from modules.core.llm_client import OllamaClient
from modules.core import llm_gateway as gw
from modules.core.llm_egress import create_relay,Capacity
from modules.core.llm_lock import LocalLLMLock,resource_path
from deploy.gateway import proxy_config

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def policy():
    return {'version':1,'local_only':True,'allow_paid':False,
        'deployments':{'local-main':{'provider':'llama-server','model':'synthetic','base_url':'http://fake.invalid:8080/v1','resource':'local-llm','languages':['*']}},
        'aliases':{a:{'deployments':['local-main'],'max_attempts':2,'timeout':20} for a in gw.ALIASES}}

@pytest.fixture
def setup(policy,tmp_path,monkeypatch):
    file=tmp_path/'routes.yaml';file.write_text(yaml.safe_dump(policy))
    from deploy import runtime
    secret=tmp_path/'secrets';secret.mkdir();key=secret/'litellm_master_key';key.write_text('synthetic-key-for-gateway-only');key.chmod(0o600)
    monkeypatch.setattr(runtime,'SECRETS',secret)
    cfg={'service':{'default_language':'fr'},'paths':{'data_dir':str(tmp_path/'data')},
         'llm':{'provider':'litellm','base_url':'http://gateway.invalid:4000','gateway':{'policy':str(file)}}}
    return cfg,file


def response(text='Texte français à préserver 🌍'):
    return {'choices':[{'message':{'content':text},'finish_reason':'stop'}],'usage':{'total_tokens':12}}

@pytest.mark.parametrize('caller,operation,alias',[
    ('fetch',None,'atlas-fast'),('posts',None,'atlas-fast'),('fetch','translate','atlas-translate'),
    ('report',None,'atlas-editorial'),('editions',None,'atlas-editorial'),('bulletin',None,'atlas-radio-script'),
    ('flash',None,'atlas-radio-script'),('atlas',None,'atlas-verify')])
def test_caller_alias_and_chat_contract(setup,caller,operation,alias):
    cfg,_=setup;transport=Mock(return_value=response());client=gw.GatewayClient(cfg,transport=transport)
    assert client.call('Réponds en français, été 🌍',caller,20,operation=operation)==response()['choices'][0]['message']['content']
    url,payload,key,timeout=transport.call_args.args
    assert url.endswith('/v1/chat/completions') and payload['model']==alias
    assert payload['messages']==[{'role':'user','content':'Réponds en français, été 🌍'}]
    assert payload['stream'] is False and payload['num_retries']==0 and timeout<=20

@pytest.mark.parametrize('data',[{},None,{'choices':[]},{'choices':[{}]},response(' '),{'choices':[{'message':{'content':'text'},'finish_reason':'length'}]},response(['not-text'])])
def test_invalid_or_incomplete_response_refused(data):
    with pytest.raises(gw.GatewayError,match='invalid_response'):gw.response_text(data)

@pytest.mark.parametrize('status,kind',[(401,'authentication'),(402,'payment'),(403,'forbidden'),(404,'model_not_found'),(429,'rate_limit'),(500,'unavailable'),(503,'unavailable'),(504,'timeout')])
def test_http_errors_are_typed_and_redacted(monkeypatch,status,kind):
    exc=urllib.error.HTTPError('http://fake.invalid',status,'sensitive-provider-message',{'Retry-After':'10'},io.BytesIO(b'{"error":{"message":"private-secret"}}'))
    opener=Mock();opener.open.side_effect=exc;monkeypatch.setattr(gw.urllib.request,'build_opener',lambda *a:opener)
    with pytest.raises(gw.GatewayError) as error:gw.post_json('http://fake.invalid',{},'synthetic-secret',3)
    assert error.value.kind==kind and error.value.retry_after==10
    assert 'private-secret' not in str(error.value) and 'sensitive' not in str(error.value)

@pytest.mark.parametrize('value,expected',[('12',12),('0',0),('-1',None),('NaN',None),('bad',None),(None,None),('Wed, 04 Nov 2026 12:00:10 GMT',10)])
def test_retry_after_parsing(value,expected):
    now=datetime(2026,11,4,12,tzinfo=timezone.utc).timestamp()
    assert gw.retry_after(value,now)==expected


def test_retry_after_never_shortened_to_budget(setup):
    cfg,_=setup;transport=Mock(side_effect=gw.GatewayError('rate_limit',status=429,retry_after=60))
    sleep=Mock();client=gw.GatewayClient(cfg,transport=transport,sleep=sleep)
    with pytest.raises(gw.GatewayError,match='timeout'):client.call('prompt','fetch',10)
    sleep.assert_not_called();assert transport.call_count==1


def test_backoff_and_recovery(setup):
    cfg,_=setup;transport=Mock(side_effect=[gw.GatewayError('unavailable'),response()]);sleep=Mock()
    client=gw.GatewayClient(cfg,transport=transport,sleep=sleep)
    assert client.call('prompt','fetch',20);assert transport.call_count==2
    assert 1<=sleep.call_args.args[0]<=1.25


def test_cancel_before_and_after_transport(setup):
    cfg,_=setup;event=threading.Event();event.set();transport=Mock(return_value=response());client=gw.GatewayClient(cfg,transport=transport)
    with pytest.raises(gw.GatewayError,match='cancelled'):client.call('prompt','fetch',20,cancel_event=event)
    transport.assert_not_called();event.clear()
    transport.side_effect=lambda *a:(event.set() or response())
    with pytest.raises(gw.GatewayError,match='cancelled'):client.call('prompt','fetch',20,cancel_event=event)


def test_explicit_fallback_and_exhaustion(setup,policy):
    cfg,file=setup;policy['deployments']['alternative']={**policy['deployments']['local-main'],'model':'synthetic-other'}
    for route in policy['aliases'].values():route['deployments']=['local-main','alternative'];route['max_attempts']=1
    file.write_text(yaml.safe_dump(policy));transport=Mock(side_effect=[gw.GatewayError('unavailable'),response()]);client=gw.GatewayClient(cfg,transport=transport)
    assert client.call('prompt','fetch',20)
    assert transport.call_args.args[1]['model']=='atlas-fast--alternative'
    transport.side_effect=gw.GatewayError('forbidden')
    with pytest.raises(gw.GatewayError,match='forbidden'):client.call('different','fetch',20)
    assert transport.call_count==4

@pytest.mark.parametrize('change',['paid','cloud_local_only','unknown','duplicate','url_credentials','cloud_url','wrong_concurrency'])
def test_policy_fail_closed(setup,policy,change):
    cfg,file=setup;d=policy['deployments']['local-main']
    if change=='paid':d.update(provider='openrouter',base_url='https://openrouter.ai/api/v1',paid=True);policy['local_only']=False
    if change=='cloud_local_only':d.update(provider='ollama-cloud',base_url='https://ollama.com/v1',paid=False)
    if change=='unknown':policy['aliases']['atlas-fast']['deployments']=['missing']
    if change=='duplicate':policy['aliases']['atlas-fast']['deployments']=['local-main','local-main']
    if change=='url_credentials':d['base_url']='https://secret:token@host.invalid/v1'
    if change=='cloud_url':d.update(provider='openrouter',base_url='https://evil.invalid/v1',paid=False);policy['local_only']=False
    if change=='wrong_concurrency':d['concurrency']=2
    file.write_text(yaml.safe_dump(policy))
    with pytest.raises(gw.GatewayError,match='configuration'):gw.GatewayClient(cfg)


def test_cache_namespace_language_prompt_model_task(setup,policy):
    cfg,file=setup;transport=Mock(return_value=response());client=gw.GatewayClient(cfg,transport=transport)
    assert client.call('prompt','fetch',20,cache_key='same')
    assert client.call('prompt','fetch',20,cache_key='same');assert transport.call_count==1
    assert client.call('changed prompt','fetch',20,cache_key='same');assert transport.call_count==2
    client.language='de';client.call('prompt','fetch',20,cache_key='same');assert transport.call_count==3
    client.prompt_version='v2';client.call('prompt','fetch',20,cache_key='same');assert transport.call_count==4
    client.policy['deployments']['local-main']['model']='changed-model';client.call('prompt','fetch',20,cache_key='same');assert transport.call_count==5
    records=list(client.directory.glob('*.json'));assert len(records)==5
    assert all(json.loads(p.read_text())['deployment']=='local-main' for p in records)


def test_relay_fixed_destination_no_headers_or_credentials_forwarded(policy,tmp_path):
    transport=Mock(return_value=response());app=create_relay(policy,tmp_path,'synthetic-bridge-token',{},transport=transport)
    client=app.test_client()
    assert client.post('/local-main/v1/chat/completions',json={}).status_code==401
    headers={'Authorization':'Bearer synthetic-bridge-token','Cookie':'untrusted=secret'}
    result=client.post('/local-main/v1/chat/completions',headers=headers,json={'model':'attacker-model','messages':[{'role':'user','content':'prompt'}],'api_base':'http://evil.invalid','max_tokens':10})
    assert result.status_code==200
    url,payload,key,_=transport.call_args.args
    assert url=='http://fake.invalid:8080/v1/chat/completions' and payload['model']=='synthetic'
    assert 'api_base' not in payload and key==''
    assert client.post('/missing/v1/chat/completions',headers=headers,json={}).status_code==404


def test_cloud_capacity_independent_of_local_and_direct_lock(policy,tmp_path):
    capacity=Capacity(tmp_path);local=policy['deployments']['local-main']
    cloud={'provider':'openrouter','concurrency':1,'rpm':20}
    direct=LocalLLMLock(resource_path(tmp_path));assert direct.acquire('fetch',0)
    try:
        with pytest.raises(gw.GatewayError,match='rate_limit'):
            with capacity.hold(local):pass
        with capacity.hold(cloud):
            with pytest.raises(gw.GatewayError,match='rate_limit'):
                with capacity.hold(cloud):pass
    finally:direct.release()
    with capacity.hold(local):pass


def test_proxy_has_no_retries_database_or_provider_keys(policy):
    config=proxy_config(policy);assert config['router_settings']['num_retries']==0
    assert config['router_settings']['fallbacks']==[]
    assert config['general_settings']['master_key']=='os.environ/LITELLM_MASTER_KEY'
    assert 'database_url' not in config['general_settings']
    names={m['model_name'] for m in config['model_list']};assert set(gw.ALIASES)<=names
    assert all(m['litellm_params']['api_base'].startswith('http://llm-egress:4041/') for m in config['model_list'])


def test_adapter_preserves_historical_empty_return_and_no_local_lock(setup,monkeypatch):
    from modules.core.llm_client import OllamaClient
    cfg,_=setup;client=OllamaClient(cfg)
    assert client._resource_lock is None
    client._gateway.transport=Mock(side_effect=gw.GatewayError('authentication'))
    assert client.call('prompt',caller='fetch')==''
    client._gateway.transport=Mock(return_value=response())
    assert client.call('prompt',caller='fetch')


def test_compose_gateway_internal_only_and_cloud_keys_isolated():
    config=yaml.safe_load((ROOT/'docker-compose.gateway.yml').read_text());services=config['services']
    assert services['litellm']['networks']==['llm_clients','llm_transport']
    assert services['llm-egress']['networks']==['llm_transport','egress']
    for role in ('litellm','llm-egress'):
        assert 'ports' not in services[role] and services[role]['profiles']==['gateway']
        assert services[role]['read_only'] is True and 'healthcheck' in services[role]
        assert len(services[role]['tmpfs'])==1 and services[role]['tmpfs'][0].startswith('/tmp:')
    assert 'openrouter_key' not in services['web']['secrets']
    assert 'openrouter_key' not in services['litellm']['secrets']

@pytest.mark.parametrize('error,kind',[(TimeoutError(),'timeout'),(urllib.error.URLError('private detail'),'network')])
def test_transport_timeouts_and_network_errors_are_safe(monkeypatch,error,kind):
    opener=Mock();opener.open.side_effect=error;monkeypatch.setattr(gw.urllib.request,'build_opener',lambda *a:opener)
    with pytest.raises(gw.GatewayError) as caught:gw.post_json('http://fake.invalid',{},'synthetic',1)
    assert caught.value.kind==kind and 'private' not in str(caught.value)

@pytest.mark.parametrize('body,kind',[(b'{"error":{"code":"context_length_exceeded","message":"private"}}','context'),(b'{}','request_rejected')])
def test_context_machine_code(monkeypatch,body,kind):
    opener=Mock();opener.open.side_effect=urllib.error.HTTPError('http://fake.invalid',400,'private',{},io.BytesIO(body))
    monkeypatch.setattr(gw.urllib.request,'build_opener',lambda *a:opener)
    with pytest.raises(gw.GatewayError) as caught:gw.post_json('http://fake.invalid',{},'synthetic',1)
    assert caught.value.kind==kind


def test_fallback_cache_reused_without_refailing_primary(setup,policy):
    cfg,file=setup;policy['deployments']['alternative']={**policy['deployments']['local-main'],'model':'other'}
    policy['aliases']['atlas-fast'].update(deployments=['local-main','alternative'],max_attempts=1)
    file.write_text(yaml.safe_dump(policy));transport=Mock(side_effect=[gw.GatewayError('forbidden'),response()])
    client=gw.GatewayClient(cfg,transport=transport)
    first=client.call('prompt','fetch',20,cache_key='article')
    assert client.call('prompt','fetch',20,cache_key='article')==first
    assert transport.call_count==2


def test_same_request_threads_generate_once_and_preserve_old_cache(setup):
    from concurrent.futures import ThreadPoolExecutor
    cfg,_=setup;started=threading.Event();finish=threading.Event()
    def transport(*args):started.set();assert finish.wait(3);return response()
    mock=Mock(side_effect=transport);client=gw.GatewayClient(cfg,transport=mock)
    old=client.directory.parent/'old.txt';old.write_text('historical summary')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.call,'prompt','fetch',5,cache_key='article')
        assert started.wait(2)
        second=pool.submit(client.call,'prompt','fetch',5,cache_key='article')
        finish.set();assert first.result(3)==second.result(3)
    assert mock.call_count==1 and old.read_text()=='historical summary'


def test_alias_capabilities_prevent_incompatible_fallback(setup,policy):
    cfg,file=setup;policy['deployments']['local-main']['languages']=['en']
    file.write_text(yaml.safe_dump(policy));transport=Mock(return_value=response())
    client=gw.GatewayClient(cfg,transport=transport)
    with pytest.raises(gw.GatewayError,match='context'):client.call('prompt','fetch',20)
    transport.assert_not_called()


def test_cloud_rpm_shared_by_provider_and_retry_after(policy,tmp_path):
    policy['local_only']=False
    local=policy['deployments']['local-main'];local.update(provider='openrouter',base_url='https://openrouter.ai/api/v1',paid=False,rpm=1,concurrency=2)
    app=create_relay(policy,tmp_path,'synthetic',{'openrouter':'private-cloud-key'},transport=Mock(return_value=response()))
    client=app.test_client();headers={'Authorization':'Bearer synthetic'};payload={'messages':[{'role':'user','content':'prompt'}]}
    assert client.post('/local-main/v1/chat/completions',headers=headers,json=payload).status_code==200
    result=client.post('/local-main/v1/chat/completions',headers=headers,json=payload)
    assert result.status_code==429 and float(result.headers['Retry-After'])>0
    assert b'private-cloud-key' not in result.data


def test_relay_error_redaction_retry_and_context(policy,tmp_path):
    transport=Mock(side_effect=gw.GatewayError('rate_limit',status=429,retry_after=42))
    client=create_relay(policy,tmp_path,'synthetic',{},transport=transport).test_client()
    headers={'Authorization':'Bearer synthetic'};body={'messages':[{'role':'user','content':'prompt'}]}
    result=client.post('/local-main/v1/chat/completions',headers=headers,json=body)
    assert result.status_code==429 and result.headers['Retry-After']=='42'
    transport.side_effect=gw.GatewayError('context',status=400)
    client=create_relay(policy,tmp_path,'synthetic',{},transport=transport).test_client()
    result=client.post('/local-main/v1/chat/completions',headers=headers,json=body)
    assert result.json['error']['code']=='context_length_exceeded'


def test_gateway_initialization_and_compose_resolution(tmp_path):
    import os,subprocess
    from deploy.init import initialize
    from deploy.gateway import initialize as initialize_gateway
    root=tmp_path/'state';initialize(root,'synthetic-admin','synthetic-strong-password')
    initialize_gateway(root,'synthetic-model','http://fake.invalid/v1')
    assert (root/'secrets/openrouter_key').read_text()==''
    assert (root/'secrets/litellm_master_key').stat().st_mode&0o777==0o400
    assert yaml.safe_load((root/'config/config.yaml').read_text())['llm']['provider']=='llama-server'
    with pytest.raises(gw.GatewayError):initialize_gateway(root,'other','http://fake.invalid/v1')
    command=['docker','compose','-f',str(ROOT/'docker-compose.yml'),'-f',str(ROOT/'docker-compose.gateway.yml'),'--env-file',str(root/'compose.env'),'--profile','full','--profile','gateway','config','--format','json']
    process=_REAL_POPEN(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={**os.environ,'DOCKER_CONFIG':str(tmp_path/'empty-docker-config')})
    output,error=process.communicate(timeout=20);assert process.returncode==0,error.decode()
    config=json.loads(output);assert set(config['services'])=={'web','news','radio','icecast','litellm','llm-egress'}
    assert 'ports' not in config['services']['litellm']
    for service in ('web','news','radio','llm-egress','litellm'):assert config['services'][service]['user']==f'{os.getuid()}:{os.getgid()}'


def test_undeclared_provider_model_substitution_is_not_cached(setup):
    cfg,_=setup;data={**response(),'model':'unexpected-model'}
    client=gw.GatewayClient(cfg,transport=Mock(return_value=data))
    assert client.call('prompt','fetch',20,cache_key='same')
    assert not list(client.directory.glob('*.json'))


def test_relay_health_checks_writable_storage(policy,tmp_path):
    client=create_relay(policy,tmp_path/'missing','synthetic',{}).test_client()
    assert client.get('/health').status_code==503
    (tmp_path/'missing').mkdir();assert client.get('/health').status_code==200


def test_public_example_is_local_only_and_proxy_init_reaps():
    assert gw.load_policy(ROOT/'config/llm_routes.yaml.example')['local_only'] is True
    config=yaml.safe_load((ROOT/'docker-compose.gateway.yml').read_text())
    assert config['services']['litellm']['init'] is True

@pytest.mark.parametrize('key,path,body,status',[
    ('wrong','/v1/chat/completions',{'model':'atlas-fast'},401),
    ('é','/v1/chat/completions',{'model':'atlas-fast'},401),
    ('synthetic-key','/key/generate',{},403),
    ('synthetic-key','/v1/chat/completions',{'model':'unlisted'},403),
    ('synthetic-key','/v1/chat/completions',{'model':'atlas-fast','api_base':'http://evil.invalid'},400),
    ('synthetic-key','/v1/chat/completions',{'model':'atlas-fast','extra_headers':{'Authorization':'evil'}},400),
    ('synthetic-key','/v1/chat/completions',{'model':'atlas-fast','num_retries':1},400),
    ('synthetic-key','/v1/chat/completions',{'model':'atlas-fast'},200),
])
def test_proxy_custom_auth_no_database_and_inference_only(monkeypatch,key,path,body,status):
    import asyncio,sys,types
    from deploy import gateway_auth
    class HTTPException(Exception):
        def __init__(self,*,code,message,type,param):self.status_code=code
    monkeypatch.setitem(sys.modules,'litellm.proxy._types',types.SimpleNamespace(UserAPIKeyAuth=lambda **kwargs:kwargs,ProxyException=HTTPException))
    monkeypatch.setenv('LITELLM_MASTER_KEY','synthetic-key')
    monkeypatch.setattr(gateway_auth,'allowed_models',lambda:{'atlas-fast'})
    async def get_body():return body
    request=types.SimpleNamespace(method='POST',url=types.SimpleNamespace(path=path),json=get_body)
    if status==200:assert asyncio.run(gateway_auth.authenticate(request,key))=={'api_key':'synthetic-key'}
    else:
        with pytest.raises(HTTPException) as caught:asyncio.run(gateway_auth.authenticate(request,key))
        assert caught.value.status_code==status


def test_relay_provider_retry_after_embargo_even_if_intermediary_drops_header(policy,tmp_path):
    transport=Mock(side_effect=gw.GatewayError('rate_limit',status=429,retry_after=30))
    client=create_relay(policy,tmp_path,'synthetic',{},transport=transport).test_client()
    headers={'Authorization':'Bearer synthetic'};body={'messages':[{'role':'user','content':'prompt'}]}
    first=client.post('/local-main/v1/chat/completions',headers=headers,json=body)
    second=client.post('/local-main/v1/chat/completions',headers=headers,json=body)
    assert first.status_code==second.status_code==429
    assert 0<float(second.headers['Retry-After'])<=30
    assert transport.call_count==1


def test_litellm_prefixed_retry_after_never_shortens_provider_delay(monkeypatch):
    opener=Mock();opener.open.side_effect=urllib.error.HTTPError('http://fake.invalid',429,'safe',{'Retry-After':'1','llm_provider-retry-after':'30'},io.BytesIO(b'{}'))
    monkeypatch.setattr(gw.urllib.request,'build_opener',lambda *a:opener)
    with pytest.raises(gw.GatewayError) as caught:gw.post_json('http://fake.invalid',{},'synthetic',1)
    assert caught.value.retry_after==30

@pytest.mark.parametrize('provider,url,paid',[
    ('openrouter','https://openrouter.ai/api/v1',False),
    ('ollama-cloud','https://ollama.com/v1',False),
    ('openrouter','https://openrouter.ai/api/v1',True),
])
def test_explicitly_authorized_cloud_policies_route_without_local_lock(setup,policy,provider,url,paid):
    cfg,file=setup;policy.update(local_only=False,allow_paid=paid)
    policy['deployments']['local-main'].update(provider=provider,base_url=url,paid=paid)
    file.write_text(yaml.safe_dump(policy));transport=Mock(return_value=response())
    client=gw.GatewayClient(cfg,transport=transport)
    assert client.call('prompt français','fetch',20)
    assert transport.call_args.args[1]['model']=='atlas-fast'


def test_fallback_cannot_silently_reduce_required_output_capacity(setup,policy):
    cfg,file=setup;policy['deployments']['small']={**policy['deployments']['local-main'],'max_output_tokens':128}
    policy['aliases']['atlas-radio-script'].update(deployments=['local-main','small'],max_attempts=1)
    file.write_text(yaml.safe_dump(policy));transport=Mock(side_effect=gw.GatewayError('unavailable'))
    client=gw.GatewayClient(cfg,transport=transport)
    with pytest.raises(gw.GatewayError,match='context'):client.call('script','bulletin',20)
    assert transport.call_count==1


def test_relay_rejects_non_ascii_auth_without_server_error(policy,tmp_path):
    client=create_relay(policy,tmp_path,'synthetic',{}).test_client()
    result=client.post('/local-main/v1/chat/completions',headers={'Authorization':'Bearer é'},json={})
    assert result.status_code==401


def test_editions_legacy_model_parameter_cannot_override_editorial_alias(setup):
    cfg,_=setup;transport=Mock(return_value=response());client=gw.GatewayClient(cfg,transport=transport)
    client.call('édition','editions',20,model='atlas-fast')
    assert transport.call_args.args[1]['model']=='atlas-editorial'


def test_web_update_preserves_gateway_provider(tmp_path):
    from modules.web.config_security import update_yaml
    path=tmp_path/'config.yaml';path.write_text('llm:\n  provider: litellm\n  model: atlas-fast\n')
    update_yaml(path,'llm','provider','litellm')
    assert yaml.safe_load(path.read_text())['llm']['provider']=='litellm'
