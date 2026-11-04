"""Opt-in gateway provisioning and runtime. Never reads legacy private environment."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import yaml
from deploy.runtime import read_secret,check_storage,acquire_role
from modules.core.llm_gateway import ALIASES,load_policy,GatewayError


def initialize(directory,model,endpoint):
    from deploy.init import private_write
    from modules.core.llm_gateway import safe_url
    root=Path(directory).resolve()
    if os.getuid()==0 or not model or not safe_url(endpoint):raise GatewayError('configuration')
    targets=[root/'config/llm_routes.yaml',root/'secrets/litellm_master_key',root/'secrets/llm_egress_key',root/'secrets/openrouter_key',root/'secrets/ollama_cloud_key']
    if any(p.exists() or p.is_symlink() for p in targets) or not (root/'compose.env').is_file():raise GatewayError('configuration')
    policy={'version':1,'local_only':True,'allow_paid':False,
            'deployments':{'local-main':{'provider':'llama-server','model':model,'base_url':endpoint,'resource':'local-llm','concurrency':1,'max_input_chars':32000,'max_output_tokens':4096,'languages':['*']}},
            'aliases':{alias:{'deployments':['local-main'],'max_attempts':2,'timeout':900} for alias in ALIASES}}
    previous=os.umask(0o077)
    try:
        private_write(targets[0],yaml.safe_dump(policy,sort_keys=False))
        private_write(targets[1],'sk-'+secrets.token_hex(32)+'\n');private_write(targets[2],secrets.token_urlsafe(48)+'\n')
        private_write(targets[3],'');private_write(targets[4],'')
    finally:os.umask(previous)
    # Switching the application backend is an explicit operator action, not a migration.


def proxy_config(policy):
    models=[]
    for alias,route in policy['aliases'].items():
        for index,name in enumerate(route['deployments']):
            names=[alias+'--'+name]+([alias] if index==0 else [])
            for public_name in names:
                models.append({'model_name':public_name,'litellm_params':{'model':'openai/'+policy['deployments'][name]['model'],
                    'api_base':'http://llm-egress:4041/'+name+'/v1','api_key':'os.environ/ATLAS_EGRESS_KEY','max_retries':0}})
    return {'model_list':models,'router_settings':{'num_retries':0,'timeout':900,'fallbacks':[]},
            'litellm_settings':{'telemetry':False,'set_verbose':False,'turn_off_message_logging':True,'num_retries':0},
            'general_settings':{'master_key':'os.environ/LITELLM_MASTER_KEY','disable_spend_logs':True,'disable_error_logs':True}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['init','proxy','relay','health'])
    parser.add_argument('--directory',default='deploy/local');parser.add_argument('--model');parser.add_argument('--endpoint')
    args=parser.parse_args()
    if args.mode=='init':
        initialize(args.directory,args.model,args.endpoint);print('Gateway files initialized; backend unchanged, no service started.');return
    os.umask(0o077)
    if args.mode=='health':
        import urllib.request
        port=4000 if os.environ.get('ATLAS_GATEWAY_ROLE')=='proxy' else 4041
        path='/health/liveliness' if port==4000 else '/health'
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}{path}',timeout=3) as response:
            if response.status!=200:raise SystemExit(1)
        return
    if os.getuid()==0:raise GatewayError('configuration')
    policy=load_policy('/app/config/llm_routes.yaml')
    if args.mode=='proxy':
        os.environ['LITELLM_MASTER_KEY']=read_secret('litellm_master_key')
        os.environ['ATLAS_EGRESS_KEY']=read_secret('llm_egress_key')
        os.environ['DISABLE_ADMIN_UI']='True';os.environ['LITELLM_TELEMETRY']='False'
        os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
        if len(os.environ['LITELLM_MASTER_KEY'])<48:raise GatewayError('configuration')
        target=Path('/tmp/litellm.yaml');target.write_text(yaml.safe_dump(proxy_config(policy)))
        os.execvp('litellm',['litellm','--config',str(target),'--host','0.0.0.0','--port','4000','--num_workers','1'])
    else:
        from modules.core.llm_egress import create_relay
        from modules.web.lifecycle import serve
        from deploy import runtime
        runtime.require_local_filesystem('/app/data');acquire_role('llm-egress')
        keys={}
        for d in policy['deployments'].values():
            provider=d['provider']
            if provider in ('openrouter','ollama-cloud'):
                keys[provider]=read_secret('openrouter_key' if provider=='openrouter' else 'ollama_cloud_key')
        event=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:event.set())
        serve(create_relay(policy,Path('/app/data'),read_secret('llm_egress_key'),keys),'0.0.0.0',4041,event)


if __name__=='__main__':
    try:main()
    except Exception:print('Gateway preflight/runtime failed; verify private configuration.',file=sys.stderr);raise SystemExit(78)
