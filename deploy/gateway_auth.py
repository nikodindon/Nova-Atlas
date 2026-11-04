"""LiteLLM-only auth hook. No DB lookup or virtual/admin key endpoints.

Imports of upstream-only types stay inside the hook; the application image needs
no LiteLLM/FastAPI dependency. The route and model allowlists are enforced here,
not delegated to optional DB-dependent post-auth checks.
"""
from functools import lru_cache
import hmac
import os
from modules.core.llm_gateway import load_policy


@lru_cache(maxsize=1)
def allowed_models():
    policy=load_policy('/app/config/llm_routes.yaml')
    names=set(policy['aliases'])
    for alias,route in policy['aliases'].items():names.update(alias+'--'+name for name in route['deployments'])
    return names


async def authenticate(request,api_key):
    from starlette.exceptions import HTTPException
    from litellm.proxy._types import UserAPIKeyAuth
    expected=os.environ.get('LITELLM_MASTER_KEY','')
    if not expected or not isinstance(api_key,str) or not hmac.compare_digest(api_key,expected):
        raise HTTPException(status_code=401,detail='Gateway authentication required')
    if request.method!='POST' or request.url.path not in ('/chat/completions','/v1/chat/completions'):
        raise HTTPException(status_code=403,detail='Gateway route forbidden')
    try:body=await request.json()
    except Exception:raise HTTPException(status_code=400,detail='Invalid gateway request') from None
    if not isinstance(body,dict) or body.get('model') not in allowed_models():
        raise HTTPException(status_code=403,detail='Gateway model forbidden')
    if set(body)-{'model','messages','stream','max_tokens','temperature','num_retries','extra_headers'}:
        raise HTTPException(status_code=400,detail='Unsupported gateway parameter')
    if body.get('stream',False) is not False or body.get('num_retries',0)!=0:
        raise HTTPException(status_code=400,detail='Unsupported gateway request mode')
    headers=body.get('extra_headers',{})
    if not isinstance(headers,dict) or set(headers)-{'X-Atlas-Timeout'}:
        raise HTTPException(status_code=400,detail='Unsupported gateway headers')
    return UserAPIKeyAuth(api_key=api_key)
