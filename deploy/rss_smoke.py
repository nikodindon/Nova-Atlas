"""CI-only network-none container, synthetic loopback server and explicit test transport.
Production never selects this adapter. ExternalHTTPClient still validates every
URL/redirect/DNS answer; a loopback socket is supplied only as an offline test transport.
"""
import os
from deploy.smoke import run

CODE=r'''
import http.client,json,socket,threading,tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from modules.http import external as http
from modules.fetch import atlas_fetch
from modules.fetch.atlas_fetch import ArticleFetcher
RSS=b'<rss version="2.0"><channel><item><guid isPermaLink="false">urn:rss:1</guid><title>Actualite RSS</title><link>https://news.invalid/rss-article</link><pubDate>Wed, 04 Nov 2026 02:00:00 -0800</pubDate></item></channel></rss>'
ATOM=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:atom:1</id><title>Actualite Atom</title><link rel="alternate" href="https://news.invalid/atom-article"/><published>2026-11-04T11:00:00+01:00</published></entry></feed>'
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  assert self.headers.get('Host')=='news.invalid'
  assert not self.headers.get('Authorization') and not self.headers.get('Cookie')
  requests.append((self.path,self.headers.get('If-None-Match')))
  if self.path=='/redirect':
   self.send_response(302);self.send_header('Location','http://127.0.0.1/private');self.end_headers();return
  payload=RSS if self.path=='/rss' else ATOM
  tag='"rss-v1"' if self.path=='/rss' else '"atom-v1"'
  if self.headers.get('If-None-Match')==tag:
   self.send_response(304);self.send_header('ETag',tag);self.end_headers();return
  self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('ETag',tag);self.send_header('Last-Modified','Wed, 04 Nov 2026 10:00:00 GMT');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
worker=threading.Thread(target=server.serve_forever);worker.start()
class LoopbackTransport(http.client.HTTPConnection):
 def __init__(self,target,address,limits,deadline):
  assert address=='93.184.216.34'
  super().__init__('127.0.0.1',server.server_port,timeout=limits.io_timeout)
 def abort(self):
  if self.sock:
   try:self.sock.shutdown(socket.SHUT_RDWR)
   except OSError:pass
  self.close()
client=http.ExternalHTTPClient(resolver=lambda *a:['93.184.216.34'],connection_factory=LoopbackTransport)
real_get=http.get
http.get=lambda url,timeout=30,**kwargs:client.get(url,**kwargs)
try:
 with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
  root=Path(tmp);atlas_fetch.init_ollama=lambda config:None
  atlas_fetch._load_rss_sources=lambda:{'tech':['http://news.invalid/rss','http://news.invalid/atom']}
  config={'paths':{'data_dir':str(root),'articles_dir':str(root/'articles')},'rss':{'feed_min_poll_seconds':0}}
  engine=ArticleFetcher(config)
  engine._fetch_content=lambda *a,**kw:'Synthetic article body'
  generated=[]
  def summarize(*args):generated.append(args);return 'Resume synthetique valide.'
  engine._summarize=summarize;engine._translate_title=lambda title,**kw:title
  assert engine.run()==2
  assert engine.run()==0 and len(generated)==2
  restarted=ArticleFetcher(config)
  restarted._fetch_content=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('duplicate extraction'))
  assert restarted.run()==0
  assert sum(tag is not None for _,tag in requests)==4
  articles=engine._load_today();assert len(articles)==2
  assert all(set(a)=={'hash','timestamp','category','title','title_original','link','source','pub_date','summary'} for a in articles)
  state=json.load(open(root/'feed_ingestion_state.json'));assert all(r['http_status']==304 for r in state['records'].values())
  try:client.get('http://news.invalid/redirect',accept_not_modified=True)
  except http.BlockedURL:pass
  else:raise AssertionError('private redirect accepted')
  assert not any(path=='/private' for path,_ in requests)
finally:
 http.get=real_get;server.shutdown();worker.join();server.server_close()
assert not any(t.name.startswith('FeedHTTP') for t in threading.enumerate())
print('RSS smoke PASS: RSS/Atom, conditional HTTP 200/304, restart/cache replay, URL dedup, unchanged public JSON, bounded workers and private redirect rejection. No external source/model.')
'''


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    print(run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,noexec,nosuid,size=64m,mode=1777',
        '--entrypoint','python','nova-atlas:02-foundation','-c',CODE]).stdout)

if __name__=='__main__':main()
