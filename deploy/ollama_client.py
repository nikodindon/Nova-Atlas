"""The engine's `ollama run MODEL` stdin contract, against an explicit remote server.

Deployment-only adapter: /api/generate never calls /api/pull or starts a model server.
Unknown/uninstalled models fail; no bundled GPU libraries, host CLI or model files.
"""
import json
import os
import sys
import urllib.request
from deploy.runtime import trusted_url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def generate(model,prompt,endpoint):
    if not trusted_url(endpoint) or not model or len(model)>512 or len(prompt)>256000:
        raise ValueError('Invalid remote generation request')
    request=urllib.request.Request(endpoint.rstrip('/')+'/api/generate',
        data=json.dumps({'model':model,'prompt':prompt,'stream':False}).encode(),
        headers={'Content-Type':'application/json'},method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=1200) as response:
        body=response.read(1024*1024+1)
    if len(body)>1024*1024:raise ValueError('Response too large')
    result=json.loads(body)
    if result.get('error') or not isinstance(result.get('response'),str):raise ValueError('Generation failed')
    return result['response']


def main():
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='run':raise ValueError('Only run is supported')
        prompt=sys.stdin.read(256001)
        print(generate(sys.argv[2],prompt,os.environ.get('OLLAMA_HOST','')))
    except Exception:
        print('Remote Ollama generation unavailable; no model download attempted.',file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
