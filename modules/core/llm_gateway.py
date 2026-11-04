"""Explicit Chat Completions routing. No SDK, implicit cloud, or provider text in errors."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import yaml
from modules.core.llm_lock import LocalLLMLock, resource_path

ALIASES=('atlas-fast','atlas-translate','atlas-editorial','atlas-radio-script','atlas-verify')
CALLERS={'fetch':'atlas-fast','posts':'atlas-fast','report':'atlas-editorial','editions':'atlas-editorial',
         'bulletin':'atlas-radio-script','flash':'atlas-radio-script','atlas':'atlas-verify'}
log=logging.getLogger('nova.gateway')


class GatewayError(RuntimeError):
    def __init__(self,kind,*,status=None,retry_after=None):
        self.kind=kind;self.status=status;self.retry_after=retry_after
        super().__init__('LLM gateway: '+kind)


def alias_for(caller,operation=None):
    if operation=='translate':return 'atlas-translate'
    return CALLERS.get(caller,'atlas-verify')


def cache_identity(prompt,language,alias,deployment,prompt_version,task):
    payload=[prompt,language,alias,deployment,prompt_version,task]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def safe_url(value):
    try:
        u=urlsplit(value);port=u.port
        return (u.scheme in ('http','https') and bool(u.hostname) and not u.username and not u.password
                and not u.query and not u.fragment and not any(c.isspace() for c in value) and '\\' not in value)
    except (ValueError,TypeError):return False


def load_policy(path):
    try:
        policy=yaml.safe_load(Path(path).read_text())
        if policy['version']!=1 or type(policy['local_only']) is not bool or type(policy.get('allow_paid',False)) is not bool:raise ValueError()
        deployments=policy['deployments'];aliases=policy['aliases']
        if not isinstance(deployments,dict) or not deployments or len(deployments)>16 or set(aliases)!=set(ALIASES):raise ValueError()
        import re
        for name,d in deployments.items():
            if not re.fullmatch('[a-z][a-z0-9-]{0,47}',name):raise ValueError()
            if set(d)-{'provider','model','base_url','resource','concurrency','paid','max_input_chars','max_output_tokens','languages','rpm'}:raise ValueError()
            if d['provider'] not in ('llama-server','ollama','ollama-cloud','openrouter'):raise ValueError()
            if not isinstance(d['model'],str) or not d['model'].strip() or len(d['model'])>200:raise ValueError()
            if not safe_url(d['base_url']):raise ValueError()
            if d['provider']=='openrouter' and d['base_url']!='https://openrouter.ai/api/v1':raise ValueError()
            if d['provider']=='ollama-cloud' and d['base_url']!='https://ollama.com/v1':raise ValueError()
            if d['provider'] in ('ollama','llama-server'):
                if not isinstance(d.get('resource','local-llm'),str) or not 1<=len(d.get('resource','local-llm'))<=128:raise ValueError()
                if d.get('concurrency',1)!=1:raise ValueError()
            else:
                if policy['local_only']:raise ValueError()
                if type(d.get('paid')) is not bool or (d['paid'] and not policy.get('allow_paid',False)):raise ValueError()
            for key,default,maximum in [('concurrency',1,16),('max_input_chars',32000,1000000),('max_output_tokens',4096,32768),('rpm',30,10000)]:
                v=d.get(key,default)
                if type(v) is not int or not 1<=v<=maximum:raise ValueError()
            if not isinstance(d.get('languages',['*']),list) or not d.get('languages',['*']) or any(not isinstance(v,str) or len(v)>16 for v in d.get('languages',['*'])):raise ValueError()
        for route in aliases.values():
            names=route['deployments']
            if not isinstance(names,list) or not names or len(names)>8 or len(set(names))!=len(names) or any(n not in deployments for n in names):raise ValueError()
            if type(route.get('max_attempts',2)) is not int or not 1<=route.get('max_attempts',2)<=4:raise ValueError()
            if not 0<float(route.get('timeout',900))<=3600:raise ValueError()
        return policy
    except (OSError,ValueError,TypeError,KeyError,yaml.YAMLError):raise GatewayError('configuration') from None


def retry_after(value,now=None):
    try:
        seconds=float(value)
        if math.isfinite(seconds) and seconds>=0:return seconds
    except (TypeError,ValueError):pass
    try:
        stamp=parsedate_to_datetime(value)
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return max(0,stamp.timestamp()-(time.time() if now is None else now))
    except (ValueError,TypeError,OverflowError):return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def post_json(url,payload,key,timeout):
    request=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        deadline=time.monotonic()+timeout
        with opener.open(request,timeout=timeout) as response:
            raw=bytearray()
            while True:
                if response.fp is None:break
                remaining=deadline-time.monotonic()
                if remaining<=0:raise GatewayError('timeout')
                response.fp.raw._sock.settimeout(remaining)
                chunk=response.read1(65536)
                if not chunk:break
                raw.extend(chunk)
                if len(raw)>2*1024*1024:raise GatewayError('invalid_response')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        status=exc.code
        # Only allowlisted machine codes are inspected; never forward raw bodies/messages.
        code=''
        try:
            body=json.loads(exc.read(8192));code=body.get('error',{}).get('code','')
        except (ValueError,TypeError,AttributeError):pass
        kinds={401:'authentication',402:'payment',403:'forbidden',404:'model_not_found',429:'rate_limit',408:'timeout',504:'timeout'}
        kind=kinds.get(status,'unavailable' if status>=500 else 'request_rejected')
        if code in ('context_length_exceeded','context_window_exceeded'):kind='context'
        raise GatewayError(kind,status=status,retry_after=retry_after(exc.headers.get('Retry-After'))) from None
    except (socket.timeout,TimeoutError):raise GatewayError('timeout') from None
    except (urllib.error.URLError,OSError):raise GatewayError('network') from None
    except (ValueError,UnicodeError):raise GatewayError('invalid_response') from None


def response_text(data):
    try:
        choice=data['choices'][0];text=choice['message']['content']
        if choice.get('finish_reason')!='stop' or not isinstance(text,str) or not text.strip():raise ValueError()
        return text.strip()
    except (KeyError,IndexError,TypeError,ValueError):raise GatewayError('invalid_response') from None


class GatewayClient:
    def __init__(self,config,*,transport=None,clock=None,sleep=None):
        llm=config['llm'];cfg=llm.get('gateway',{})
        self.policy=load_policy(cfg.get('policy','config/llm_routes.yaml'))
        self.url=llm.get('base_url','http://litellm:4000')
        if not safe_url(self.url):raise GatewayError('configuration')
        from deploy.runtime import read_secret
        self.key=read_secret('litellm_master_key')
        self.language=config.get('service',{}).get('default_language','fr')
        self.prompt_version=cfg.get('prompt_version','legacy-v1')
        self.transport=transport or post_json;self.clock=clock or time.monotonic;self.sleep=sleep or time.sleep
        self.directory=Path(llm.get('cache',{}).get('dir') or Path(config.get('paths',{}).get('data_dir','data'))/'llm_cache')/'gateway-v1'
        self.directory.mkdir(parents=True,exist_ok=True)
        self.enabled=llm.get('cache',{}).get('enabled',True)
        self.ttl=float(llm.get('cache',{}).get('ttl_days',7))*86400

    def _wait(self,seconds,deadline,cancel):
        if seconds>=deadline-self.clock():raise GatewayError('timeout')
        if cancel is not None:
            if cancel.wait(seconds):raise GatewayError('cancelled')
        else:self.sleep(seconds)

    def call(self,prompt,caller,timeout,*,operation=None,cache_key=None,use_cache=True,cancel_event=None,model=None):
        use_cache=use_cache and self.enabled
        alias=alias_for(caller,operation)
        if model in ALIASES:alias=model
        elif model is not None:
            # Historical editions pass their real model. It cannot override deployment policy.
            log.debug('Legacy model parameter mapped to configured editorial alias')
        route=self.policy['aliases'][alias]
        if not isinstance(prompt,str) or not prompt.strip() or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise GatewayError('request_rejected')
        deadline=self.clock()+min(timeout,float(route.get('timeout',900)))
        task=operation or caller
        fingerprint=cache_identity(prompt,self.language,alias,self.policy['deployments'],self.prompt_version,task)
        lock=LocalLLMLock(self.directory/(fingerprint+'.lock'))
        if not lock.acquire(caller,max(0,deadline-self.clock()),cancel_event=cancel_event):raise GatewayError('cancelled' if cancel_event is not None and cancel_event.is_set() else 'timeout')
        try:
            # A previously successful explicit alternative is reusable without re-failing the primary.
            if use_cache and cache_key:
                for name in route['deployments']:
                    d=self.policy['deployments'][name]
                    if len(prompt)>d.get('max_input_chars',32000) or (self.language not in d.get('languages',['*']) and '*' not in d.get('languages',['*'])):continue
                    identity=cache_identity(prompt,self.language,alias,{'id':name,'config':d},self.prompt_version,task)
                    try:
                        record=json.loads((self.directory/(identity+'.json')).read_text())
                        if record['identity']==identity and record['deployment']==name and (self.ttl<=0 or 0<=time.time()-record['ts']<=self.ttl) and isinstance(record['text'],str) and record['text'].strip():return record['text']
                    except (OSError,ValueError,KeyError,TypeError):pass
            last=GatewayError('unavailable')
            for index,name in enumerate(route['deployments']):
                d=self.policy['deployments'][name]
                if len(prompt)>d.get('max_input_chars',32000) or (self.language not in d.get('languages',['*']) and '*' not in d.get('languages',['*'])):
                    last=GatewayError('context');continue
                identity=cache_identity(prompt,self.language,alias,{'id':name,'config':d},self.prompt_version,task)
                path=self.directory/(identity+'.json')
                if use_cache and cache_key:
                    try:
                        record=json.loads(path.read_text())
                        if record['identity']==identity and record['deployment']==name and (self.ttl<=0 or 0<=time.time()-record['ts']<=self.ttl) and isinstance(record['text'],str) and record['text'].strip():return record['text']
                    except (OSError,ValueError,KeyError,TypeError):pass
                for attempt in range(route.get('max_attempts',2)):
                    if cancel_event is not None and cancel_event.is_set():raise GatewayError('cancelled')
                    remaining=deadline-self.clock()
                    if remaining<=0:raise GatewayError('timeout')
                    started=self.clock()
                    try:
                        payload={'model':alias if index==0 else alias+'--'+name,'messages':[{'role':'user','content':prompt}],
                                 'stream':False,'max_tokens':d.get('max_output_tokens',4096),'temperature':0.3,'num_retries':0,'extra_headers':{'X-Atlas-Timeout':str(remaining)}}
                        data=self.transport(self.url.rstrip('/')+'/v1/chat/completions',payload,self.key,remaining)
                        if cancel_event is not None and cancel_event.is_set():raise GatewayError('cancelled')
                        text=response_text(data)
                        usage=data.get('usage',{})
                        tokens={k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0} if isinstance(usage,dict) else {}
                        log.info('LLM alias=%s deployment=%s provider=%s duration=%.3f success=true tokens=%s cost_estimate=unknown',alias,name,d['provider'],self.clock()-started,tokens)
                        if use_cache and cache_key:
                            from modules.fetch.article_state import atomic_json
                            try:atomic_json(path,{'identity':identity,'deployment':name,'text':text,'ts':time.time()})
                            except OSError:log.warning('Gateway cache write failed; generated text retained')
                        return text
                    except GatewayError as exc:
                        last=exc
                        log.warning('LLM alias=%s deployment=%s duration=%.3f success=false kind=%s',alias,name,self.clock()-started,exc.kind)
                        if exc.kind=='cancelled':raise
                        if exc.kind not in ('rate_limit','unavailable','timeout','network','invalid_response'):break
                        delay=exc.retry_after if exc.retry_after is not None else min(8,2**attempt)+random.uniform(0,.25)
                        # Respect Retry-After across alternatives, too. Never shorten it to fit budget.
                        if exc.retry_after is not None or attempt+1<route.get('max_attempts',2):self._wait(delay,deadline,cancel_event)
            raise last
        finally:lock.release()
