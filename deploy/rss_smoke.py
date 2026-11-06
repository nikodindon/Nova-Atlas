"""CI-only network-none container, synthetic loopback server and explicit test transport.
Production never selects this adapter. ExternalHTTPClient still validates every
URL/redirect/DNS answer; a loopback socket is supplied only as an offline test transport.
"""
import os
from deploy.smoke import run

CODE=r'''
import http.client,json,socket,threading,tempfile,faulthandler
from http.client import HTTPConnection
faulthandler.dump_traceback_later(30,exit=True)
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
  if self.path not in ('/rss','/atom','/site'):
   self.send_response(404);self.end_headers();return
  payload=b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>' if self.path=='/site' else RSS if self.path=='/rss' else ATOM
  tag='"rss-v1"' if self.path=='/rss' else '"atom-v1"'
  if self.headers.get('If-None-Match')==tag:
   self.send_response(304);self.send_header('ETag',tag);self.end_headers();return
  self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('ETag',tag);self.send_header('Last-Modified','Wed, 04 Nov 2026 10:00:00 GMT');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
worker=threading.Thread(target=server.serve_forever)
class LoopbackTransport(HTTPConnection):
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
worker.start()
try:
 with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
  root=Path(tmp);atlas_fetch.init_ollama=lambda config:None
  from modules.core.source_manager import SourceManager
  feeds=root/'feeds.yaml';feeds.write_text('tech:\n  - http://news.invalid/rss\n  - http://news.invalid/atom\n')
  manager=SourceManager(feeds,root/'feed_ingestion_state.json')
  atlas_fetch.DEFAULT_FEEDS_PATH=feeds
  atlas_fetch.invalidate_feeds_cache()
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
  version=manager.read().version;rss_id=manager.read().sources[0].id
  manager.mutate('disable',{'id':rss_id},version)
  assert atlas_fetch._load_rss_sources()=={'tech':['http://news.invalid/atom']}
  before=len(requests);assert restarted.run()==0
  assert [path for path,tag in requests[before:]]==['/atom']
  assert manager.listing()['sources'][0]['health']=='disabled'
  opml=manager.export('opml');incoming,preview=manager.preview(opml,'opml')
  assert preview['duplicates']==2 and preview['additions']==0
  manager.mutate('enable',{'id':rss_id})
  assert len(atlas_fetch._load_rss_sources()['tech'])==2
  from modules.discovery.source_discovery import Discovery
  def discovery_transport(url,**kwargs):
   kwargs.pop('timeout',None);return client.get(url,**kwargs)
  before=feeds.read_bytes()
  found=Discovery(manager,transport=discovery_transport).run('site','http://news.invalid/site')
  assert any(c['url']=='http://news.invalid/rss' and c['format']=='rss20' for c in found['candidates'])
  assert found['requests']<=12 and feeds.read_bytes()==before
  from modules.editorial.index import EventIndex
  from modules.editorial.selection import opt_in
  from datetime import datetime,timezone
  import hashlib
  article_file=next((root/'articles').glob('*_articles.json'))
  temporary_articles=json.loads(article_file.read_text());now=datetime.now(timezone.utc).isoformat()
  for i in range(2):
   url='https://editorial'+str(i)+'.invalid/article'
   temporary_articles.append({'hash':hashlib.md5(url.encode()).hexdigest(),'title':'Le ministre Martin annonce sa démission du gouvernement français','summary':'Le ministre Martin annonce sa démission du gouvernement français après un vote au Parlement. Les élus commentent cette démission.','source':'Média '+str(i),'link':url,'category':'personnalisee','timestamp':now,'pub_date':now})
  article_file.write_text(json.dumps(temporary_articles));unchanged=article_file.read_bytes()
  event_index=EventIndex(root/'articles',root);derived=event_index.rebuild()
  assert any(e['article_count']==2 and e['domain_count']==2 for e in derived['events'])
  event_index.rebuild(apply=True,expected=derived['input_sha'])
  assert article_file.read_bytes()==unchanged and event_index.load()['status']=='ready'
  assert opt_in(temporary_articles,{}) is temporary_articles
  assert len(opt_in(temporary_articles,{'paths':{'data_dir':str(root)},'editorial_events':{'enabled':True}}))==3

  # The production decoder runs in its bounded subprocess, with synthetic pixels.
  from modules.media.store import MediaStore,decode_image
  from modules.media.candidates import ImageCandidate
  from modules.media.rights import CC_BY
  from PIL import Image
  import io
  raster=io.BytesIO();Image.new('RGB',(640,360),'navy').save(raster,'PNG');raw=raster.getvalue()
  decoded=decode_image(raw);assert len(decoded['variants'])==2
  media=MediaStore(root,mode='approved_only')
  target={'kind':'article','id':'a'*32,'fingerprint':'b'*64,'title':'Synthetic'}
  proof={'provider':'commons','page_revision':1,'file_sha1':hashlib.sha1(raw).hexdigest(),'size':len(raw),'source_page':'https://commons.wikimedia.org/wiki/File:Synthetic.png','license':'CC BY 4.0','license_url':CC_BY,'author':'Synthetic creator','credit':'Fixture','attribution_required':True,'copyrighted':True}
  candidate=ImageCandidate('https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png','commons',proof['source_page'],'Fixture','commons','123',proof)
  identity=media.add(candidate,target,trusted=True);media.prepare(identity,raw,expected=media.load()[1])
  assert media.public_for(target)['fallback']
  row=media.load()[0]['records'][identity];media.decide(identity,'approve',expected=media.load()[1],binding=media.binding(row),ack=True)
  assert not media.public_for(target)['fallback']
  media.add(__import__('dataclasses').replace(candidate,proof={**proof,'license':'CC BY-NC 4.0'}),target,trusted=True)
  assert media.public_for(target)['fallback']

  try:client.get('http://news.invalid/redirect',accept_not_modified=True)
  except http.BlockedURL:pass
  else:raise AssertionError('private redirect accepted')
  assert not any(path=='/private' for path,_ in requests)
finally:
 http.get=real_get;server.shutdown();worker.join();server.server_close();faulthandler.cancel_dump_traceback_later()
assert not any(t.name.startswith('FeedHTTP') for t in threading.enumerate())
print('RSS smoke PASS: RSS/Atom, conditional HTTP 200/304, restart/cache replay, URL dedup, unchanged public JSON, bounded workers, Source Manager atomic disable/reload/export, static source discovery, derived editorial events and private redirect rejection; bounded raster worker, explicit rights approval and license-change revocation. No external source/model.')
'''


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    print(run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,noexec,nosuid,size=64m,mode=1777',
        '--entrypoint','python','nova-atlas:02-foundation','-c',CODE]).stdout)

if __name__=='__main__':main()
