"""Restricted provider relay: one configured destination per route, OS capacity leases.

LiteLLM has no egress network. This relay alone holds provider credentials. It does
not implement retries or fallback, and never forwards arbitrary headers/URLs.
"""
import math
from contextlib import contextmanager
import hmac
import json
import threading
import time
from flask import Flask,request,jsonify
from modules.core.llm_gateway import GatewayError,post_json,load_policy
from modules.core.llm_lock import LocalLLMLock,resource_path


class Capacity:
    def __init__(self,directory):
        self.directory=directory;self.guard=threading.Lock();self.requests={};self.cooldowns={}

    def defer(self,deployment,seconds):
        identity=deployment['provider']+':'+(deployment.get('resource','local-llm') if deployment['provider'] in ('ollama','llama-server') else '')
        with self.guard:self.cooldowns[identity]=max(self.cooldowns.get(identity,0),time.monotonic()+seconds)

    @contextmanager
    def hold(self,deployment):
        local=deployment['provider'] in ('ollama','llama-server')
        provider=deployment['provider']
        with self.guard:
            identity=provider+':'+(deployment.get('resource','local-llm') if local else '')
            remaining=self.cooldowns.get(identity,0)-time.monotonic()
            if remaining>0:raise GatewayError('rate_limit',status=429,retry_after=remaining)
        if not local:
            with self.guard:
                now=time.monotonic();recent=[t for t in self.requests.get(provider,[]) if now-t<60]
                if len(recent)>=deployment.get('rpm',30):raise GatewayError('rate_limit',status=429,retry_after=60-(now-recent[0]))
                recent.append(now);self.requests[provider]=recent
        resource=deployment.get('resource','local-llm') if local else 'cloud-'+provider
        slots=1 if local else deployment.get('concurrency',1)
        lease=None
        for index in range(slots):
            candidate=LocalLLMLock(resource_path(self.directory,resource if local else resource+'-'+str(index)))
            if candidate.acquire('atlas',0):lease=candidate;break
        if lease is None:raise GatewayError('rate_limit',status=429,retry_after=1)
        try:yield
        finally:lease.release()


def create_relay(policy,directory,key,provider_keys,*,transport=None):
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=1024*1024
    capacity=Capacity(directory);transport=transport or post_json
    # One provider quota across every alias/deployment, using its most restrictive declaration.
    for d in policy['deployments'].values():
        if d['provider'] in ('openrouter','ollama-cloud'):
            peers=[p for p in policy['deployments'].values() if p['provider']==d['provider']]
            d['concurrency']=min(p.get('concurrency',1) for p in peers)
            d['rpm']=min(p.get('rpm',30) for p in peers)
    @app.before_request
    def authenticate():
        if request.path=='/health' and request.method=='GET':return None
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+key):return jsonify(error={'code':'authentication'}),401
    @app.get('/health')
    def health():
        import os,tempfile
        try:
            with tempfile.NamedTemporaryFile(dir=directory,prefix='.gateway-health-') as probe:
                probe.write(b'ok');probe.flush();os.fsync(probe.fileno())
        except OSError:return {'status':'storage_unavailable'},503
        return {'status':'ok'}
    @app.post('/<name>/v1/chat/completions')
    def completion(name):
        d=policy['deployments'].get(name)
        if d is None:return jsonify(error={'code':'model_not_found'}),404
        try:
            body=request.get_json()
            messages=body['messages']
            if not isinstance(messages,list) or not messages or any(not isinstance(m,dict) or set(m)-{'role','content'} or m.get('role') not in ('user','system','assistant') or not isinstance(m.get('content'),str) for m in messages):raise GatewayError('request_rejected',status=400)
            if sum(len(m['content']) for m in messages)>d.get('max_input_chars',32000):raise GatewayError('context',status=400)
            tokens=body.get('max_tokens',4096)
            if type(tokens) is not int or not 1<=tokens<=d.get('max_output_tokens',4096):raise GatewayError('context',status=400)
            payload={'model':d['model'],'messages':messages,'stream':False,'max_tokens':tokens,'temperature':0.3}
            if d['provider']=='openrouter':
                # No provider-side implicit model/provider fallback or plugins.
                payload['provider']={'allow_fallbacks':False}
            timeout=float(request.headers.get('X-Atlas-Timeout','900'))
            if not math.isfinite(timeout) or not 0<timeout<=3600:raise GatewayError('request_rejected',status=400)
            with capacity.hold(d):
                try:data=transport(d['base_url'].rstrip('/')+'/chat/completions',payload,provider_keys.get(d['provider'],''),timeout)
                except GatewayError as exc:
                    if exc.retry_after is not None:capacity.defer(d,exc.retry_after)
                    raise
            from modules.core.llm_gateway import response_text
            text=response_text(data)
            # Deliberately reconstruct the response. No arbitrary provider metadata.
            return {'id':'atlas-response','object':'chat.completion','created':int(time.time()),'model':data.get('model') if isinstance(data.get('model'),str) and len(data['model'])<=200 else d['model'],
                    'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
                    'usage':{k:v for k,v in data.get('usage',{}).items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0} if isinstance(data.get('usage'),dict) else {}}
        except GatewayError as exc:
            code='context_length_exceeded' if exc.kind=='context' else exc.kind
            response=jsonify(error={'code':code,'message':'LLM request failed'})
            if exc.retry_after is not None:response.headers['Retry-After']=str(exc.retry_after)
            return response,exc.status or (504 if exc.kind=='timeout' else 502)
        except (ValueError,TypeError,KeyError):return jsonify(error={'code':'request_rejected'}),400
    return app
