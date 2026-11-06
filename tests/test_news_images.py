"""Synthetic raster fixtures only; no external image or provider request."""
import base64
from dataclasses import replace
import hashlib
import io
import json
import os
import pytest
from PIL import Image
from modules.media.candidates import ImageCandidate,html_images
from modules.media.commons import records,Commons,API
from modules.media.rights import assess,digest,CC_BY
from modules.media.store import MediaStore,MediaError,MediaConflict,FALLBACK
from modules.media.worker import prepare
from modules.media.service import MediaService
from tests.test_http_security import client_for,FakeResponse,PUBLIC4
from modules.fetch.feed_parser import parse_feed,FeedEntry

TARGET={'kind':'article','id':'a'*32,'fingerprint':'b'*64,'title':'Synthetic article'}

def image(fmt='PNG',size=(640,360),**kwargs):
    stream=io.BytesIO();Image.new('RGB',size,'#123456').save(stream,fmt,**kwargs);return stream.getvalue()

def candidate(raw=None,**changes):
    raw=raw or image()
    proof={'provider':'commons','page_id':123,'page_revision':456,'file_sha1':hashlib.sha1(raw).hexdigest(),'size':len(raw),'source_page':'https://commons.wikimedia.org/wiki/File:Synthetic.png','license':'CC BY 4.0','license_url':CC_BY,'author':'Synthetic <creator>','credit':'Synthetic fixture','restrictions':'','attribution_required':True,'copyrighted':True}
    proof.update(changes)
    return ImageCandidate('https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png','commons',proof['source_page'],'Synthetic','commons','123',proof)

def payload(raw=None,**changes):
    c=candidate(raw,**changes);p=c.proof
    names={'Artist':'author','Credit':'credit','LicenseShortName':'license','LicenseUrl':'license_url','Restrictions':'restrictions','AttributionRequired':'attribution_required','Copyrighted':'copyrighted'}
    return json.dumps({'query':{'pages':[{'ns':6,'pageid':123,'lastrevid':p['page_revision'],'title':'File:Synthetic.png','imageinfo':[{'url':c.url,'descriptionurl':c.source_page,'sha1':p['file_sha1'],'size':p['size'],'mime':'image/png','extmetadata':{name:{'value':str(p[key]).lower() if isinstance(p[key],bool) else p[key]} for name,key in names.items()}}]}]}}).encode()

@pytest.fixture
def store(tmp_path):return MediaStore(tmp_path/'data',mode='approved_only',clock=lambda:100000,runner=prepare)

def prepared(store,raw=None):
    raw=raw or image();identity=store.add(candidate(raw),TARGET,trusted=True)
    store.prepare(identity,raw,expected=store.load()[1]);return identity

def approved(store):
    identity=prepared(store);state,version=store.load();row=state['records'][identity]
    store.decide(identity,'approve',expected=version,binding=store.binding(row),ack=True);return identity

@pytest.mark.parametrize('license,status',[('', 'rights_unknown'),('CC BY-SA 4.0','rights_restricted'),('CC BY-NC 4.0','rights_restricted'),('CC BY-ND 4.0','rights_restricted'),('Public domain','rights_restricted'),('CC0','rights_restricted'),('CC BY 4.0','rights_verified')])
def test_license_allowlist(license,status):assert assess(candidate(license=license).proof,trusted_commons=True)['status']==status

@pytest.mark.parametrize('changes,status',[({'license_url':'https://evil.invalid'},'rights_conflicting'),({'author':''},'verification_failed'),({'restrictions':'personality rights'},'rights_restricted'),({'attribution_required':False},'rights_conflicting'),({'page_revision':None},'verification_failed')])
def test_incomplete_or_conflicting_proof(changes,status):assert assess(candidate(**changes).proof,trusted_commons=True)['status']==status

def test_untrusted_provider_metadata_cannot_grant_rights():assert assess(candidate().proof)['status']=='rights_unknown'

@pytest.mark.parametrize('fmt',['JPEG','PNG','WEBP'])
def test_decoder_reencodes_safe_local_images(fmt):
    result=prepare(image(fmt));assert len(result['variants'])==2
    for v in result['variants']:
        with Image.open(io.BytesIO(base64.b64decode(v['data']))) as im:
            im.load();assert im.format=='JPEG' and not im.getexif() and im.width<=v['size']
    assert result['metrics']['decode_ms']>=0

@pytest.mark.parametrize('raw',[b'<svg onload="alert(1)"/>',b'not an image',b'',b'x'*(4*1024*1024+1)])
def test_decoder_refuses_invalid_and_oversized_bytes(raw):
    with pytest.raises((ValueError,OSError)):prepare(raw)

def test_decoder_refuses_truncation_pixels_and_animation():
    for raw in (image('JPEG')[:-20],image(size=(8193,1))):
        with pytest.raises((ValueError,OSError)):prepare(raw)
    out=io.BytesIO();a=Image.new('RGB',(2,2),'red');a.save(out,'PNG',save_all=True,append_images=[Image.new('RGB',(2,2),'blue')],duration=10)
    with pytest.raises(ValueError,match='animation'):prepare(out.getvalue())

def test_exif_removed_and_orientation_applied():
    exif=Image.Exif();exif[274]=6;exif[315]='sensitive synthetic author'
    result=prepare(image('JPEG',size=(20,10),exif=exif))
    assert (result['variants'][0]['width'],result['variants'][0]['height'])==(10,20)
    assert b'sensitive synthetic author' not in base64.b64decode(result['variants'][0]['data'])

@pytest.mark.parametrize('tag,expected',[
 ('<media:thumbnail url="/pic.png"/>','media_thumbnail'),
 ('<media:content url="/pic.png" type="image/png"/>','media_content'),
 ('<enclosure url="/pic.png" type="image/png"/>','enclosure'),
 ('<itunes:image href="/pic.png"/>','itunes_image')])
def test_rss_image_extensions(tag,expected):
    xml=('<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><item><title>Title</title><link>https://news.invalid/a</link>'+tag+'</item></channel></rss>').encode()
    entry=parse_feed(xml,'https://news.invalid/rss','https://news.invalid/rss').entries[0]
    assert entry.images and entry.images[0]['url']=='https://news.invalid/pic.png'
    assert FeedEntry.from_dict({k:v for k,v in entry.to_dict().items() if k!='images'}).images==()

def test_atom_image_and_bounded_candidates():
    xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title><entry><title>Title</title><id>one</id><link href="https://news.invalid/a"/><link rel="enclosure" type="image/png" href="/image.png"/></entry></feed>'
    entry=parse_feed(xml,'https://news.invalid/feed','https://news.invalid/feed').entries[0];assert entry.images
    html=b'<meta property="og:image" content="/pic.png"><meta name="twitter:image" content="/pic.png"><meta property="og:image" content="javascript:alert(1)">'
    rows=html_images(html,'https://news.invalid/final');assert len(rows)==1 and rows[0].url=='https://news.invalid/pic.png'
    assert len(html_images(b''.join(f'<meta property="og:image" content="/{i}.png">'.encode() for i in range(30)),'https://news.invalid'))<=5

def test_commons_fixed_adapter_and_html_metadata():
    calls=[]
    def get(url):
        calls.append(url);return type('R',(),{'status_code':200,'content':payload(author='<b>Creator</b><script>bad()</script>')})()
    rows=Commons(get).fetch(term='synthetic subject');assert len(rows)==1
    assert calls[0].startswith(API+'?') and 'gsrlimit=5' in calls[0]
    assert '<' not in rows[0].proof['author']
    assert records(payload(license='CC BY-SA 4.0'))[0].proof['license']=='CC BY-SA 4.0'
    with pytest.raises(ValueError):records(b'{bad')

def test_default_disabled_has_no_writes_or_downloads(tmp_path):
    s=MediaStore(tmp_path/'absent');assert s.add(candidate(),TARGET) is None
    assert s.public_for(TARGET)==FALLBACK and not s.root.exists()
    assert s.cleanup()['dry_run'] and not s.root.exists()


def test_exact_resource_required_before_publication(store):
    identity=prepared(store);state,version=store.load();row=state['records'][identity]
    assert store.public_for(TARGET)['fallback']
    for binding,ack in [(store.binding(row),False),('c'*64,True)]:
        with pytest.raises(MediaError):store.decide(identity,'approve',expected=version,binding=binding,ack=ack)
    store.decide(identity,'approve',expected=version,binding=store.binding(row),ack=True)
    projected=store.public_for(TARGET);assert projected['attribution']['license_url']==CC_BY and projected['url'].startswith('/media/images/')
    assert store.public_for({**TARGET,'fingerprint':'c'*64})['fallback']
    assert store.bytes(store.load()[0]['records'][identity],320).startswith(b'\xff\xd8')

@pytest.mark.parametrize('action',['reject','disable','caption'])
def test_decisions_remove_publication_without_deleting_artifacts(store,action):
    identity=approved(store);row=store.load()[0]['records'][identity];directory=store.root/'variants'/row['artifact']['id']
    store.decide(identity,action,expected=store.load()[1],caption='<script>inert</script>')
    assert store.public_for(TARGET)['fallback'] and directory.exists()
    assert store.cleanup()['paths']==[]


def test_license_change_invalidates_approval_preserves_history(store):
    identity=approved(store);store.add(candidate(license='CC BY-NC 4.0'),TARGET,trusted=True)
    row=store.load()[0]['records'][identity]
    assert row['status']=='rights_restricted' and row['history'][0]['approval']
    assert store.public_for(TARGET)['fallback'] and store.cleanup()['paths']==[]

def test_refresh_unchanged_and_rebuild_keep_exact_approval(store):
    identity=approved(store);before=store.load()[0]['records'][identity]['approval']
    store.add(candidate(),TARGET,trusted=True)
    store.prepare(identity,image(),expected=store.load()[1])
    assert store.load()[0]['records'][identity]['approval']==before


def test_stale_proof_and_corrupt_binary_fail_closed(store):
    identity=approved(store);row=store.load()[0]['records'][identity];store.clock=lambda:200000
    assert store.public_for(TARGET)['fallback']
    store.clock=lambda:100000;(store.root/'variants'/row['artifact']['id']/'320.jpg').write_bytes(b'broken')
    assert store.public_for(TARGET)['fallback']


def test_dedup_and_optimistic_version(store):
    first=prepared(store);second=store.add(candidate(),{**TARGET,'id':'d'*32},trusted=True)
    with pytest.raises(MediaConflict):store.prepare(second,image(),expected='0'*64)
    store.prepare(second,image(),expected=store.load()[1])
    assert len(list((store.root/'originals').iterdir()))==1 and len(list((store.root/'variants').iterdir()))==1
    assert store.load()[0]['records'][first]['artifact']['id']==store.load()[0]['records'][second]['artifact']['id']


def test_resource_bytes_must_match_official_sha_and_size(store):
    identity=store.add(candidate(),TARGET,trusted=True)
    with pytest.raises(MediaError,match='resource_version'):store.prepare(identity,image('JPEG'),expected=store.load()[1])
    assert not list((store.root/'variants').iterdir())


def test_crash_before_manifest_commit_leaves_no_publication(store,monkeypatch):
    identity=store.add(candidate(),TARGET,trusted=True);before=store.path.read_bytes()
    monkeypatch.setattr(store,'write',lambda _:(_ for _ in ()).throw(OSError('interruption')))
    with pytest.raises(OSError):store.prepare(identity,image(),expected=store.load()[1])
    assert store.path.read_bytes()==before and store.public_for(TARGET)['fallback']


def test_corrupt_manifest_preserved_and_symlinks_refused(store,tmp_path):
    prepared(store);store.path.write_text('{broken');before=store.path.read_bytes()
    with pytest.raises(MediaError):store.add(candidate(),TARGET,trusted=True)
    assert store.path.read_bytes()==before and store.public_for(TARGET)['fallback']
    root=tmp_path/'linked';root.symlink_to(store.data,target_is_directory=True)
    with pytest.raises(MediaError):MediaStore(root)


def test_unreferenced_cleanup_is_dry_run_and_version_checked(store):
    approved(store);orphan=store.root/'originals'/('0'*64+'.bin');orphan.write_bytes(b'orphan');os.utime(orphan,(1,1))
    store.clock=lambda:900000
    result=store.cleanup();assert result['dry_run'] and orphan.exists() and len(result['paths'])==1
    with pytest.raises(MediaConflict):store.cleanup(apply=True,expected='0'*64)
    store.cleanup(apply=True,expected=result['version']);assert not orphan.exists()


def test_media_network_reuses_pinned_secure_transport(tmp_path):
    from tests.test_http_security import client_for,FakeResponse,PUBLIC4
    client,calls,dns=client_for([FakeResponse(image(),headers={'Content-Type':'text/html'})])
    service=MediaService(tmp_path/'articles',tmp_path/'data',{'mode':'suggest'},transport=lambda url,timeout=None,**kw:client.get(url,**kw))
    with service.operation():assert service.get('https://images.invalid/pic').content==image()
    assert len(calls)==1 and calls[0][1]==PUBLIC4 and dns.call_count==1

@pytest.mark.parametrize('url',['http://127.0.0.1/a','http://100.64.0.1/a','https://[::1]/a','http://169.254.169.254/a'])
def test_forbidden_image_destinations(tmp_path,url):
    from tests.test_http_security import client_for,FakeResponse
    client,calls,_=client_for([FakeResponse()]);service=MediaService(tmp_path/'a',tmp_path/'d',{'mode':'suggest'},transport=lambda url,timeout=None,**kw:client.get(url,**kw))
    with service.operation(),pytest.raises((MediaError,ValueError)):service.get(url)
    assert calls==[]


def test_redirect_private_and_dns_mixed_rejected(tmp_path):
    from tests.test_http_security import client_for,FakeResponse,PUBLIC4
    for i,(responses,addresses) in enumerate([([FakeResponse(status=302,headers={'Location':'http://127.0.0.1/a'})],[PUBLIC4]),([FakeResponse()],[PUBLIC4,'10.0.0.1'])]):
        client,calls,_=client_for(responses,addresses=addresses);service=MediaService(tmp_path/'a',tmp_path/str(i),{'mode':'suggest'},transport=lambda url,timeout=None,**kw:client.get(url,**kw))
        with service.operation(),pytest.raises(MediaError):service.get('https://images.invalid/a')
        assert len(calls)<=1


def test_429_embargo_timeout_cancel_and_global_limits(tmp_path):
    from tests.test_http_security import client_for,FakeResponse
    client,calls,_=client_for([FakeResponse(status=429,headers={'Retry-After':'3600'})]);service=MediaService(tmp_path/'a',tmp_path/'d',{'mode':'suggest'},transport=lambda url,timeout=None,**kw:client.get(url,**kw))
    with service.operation(),pytest.raises(MediaError):service.get('https://images.invalid/a')
    with service.operation(),pytest.raises(MediaError):service.get('https://images.invalid/a')
    assert len(calls)==1
    with service.operation():
        service.cancel.set()
        with pytest.raises(ValueError):service.get('https://images.invalid/a')
    with pytest.raises(MediaError,match='three_media'):
        with service.operation():pass


def test_process_worker_resource_limits_and_no_inherited_secrets(tmp_path,monkeypatch):
    import subprocess
    from modules.media.store import decode_image
    monkeypatch.setenv('SYNTHETIC_PROVIDER_SECRET','never-forwarded')
    def run(command,**kwargs):
        assert command[1:]==['-m','modules.media.worker']
        assert 'SYNTHETIC_PROVIDER_SECRET' not in kwargs['env'] and kwargs['timeout']==10
        assert kwargs['cwd']=='/tmp'
        return type('Result',(),{'returncode':0,'stdout':json.dumps(prepare(kwargs['input'])).encode()})()
    monkeypatch.setattr(subprocess,'run',run)
    assert decode_image(image())['version'].startswith('pillow13')


def test_html_jsonld_and_credentials_refused():
    body=b'<script type="application/ld+json">{"@type":"NewsArticle","image":{"url":"/main.png"}}</script><meta property="og:image" content="https://u:password@news.invalid/x"><img itemprop="image" src="/other.png">'
    candidates=html_images(body,'https://news.invalid/a')
    assert {c.url for c in candidates}=={'https://news.invalid/main.png','https://news.invalid/other.png'}


def test_rejected_unknown_never_downloads_or_publishes(store):
    identity=store.add(ImageCandidate('https://news.invalid/a.png','og:image','https://news.invalid/a'),TARGET)
    with pytest.raises(MediaError):store.prepare(identity,image(),expected=store.load()[1])
    assert store.public_for(TARGET)['fallback'] and not list((store.root/'originals').iterdir())


def test_threads_updates_serialized_and_restart_stable(store):
    from concurrent.futures import ThreadPoolExecutor
    def add(i):return store.add(candidate(),{**TARGET,'id':str(i)*32},trusted=True)
    with ThreadPoolExecutor(max_workers=3) as pool:ids=list(pool.map(add,range(3)))
    restarted=MediaStore(store.data,mode='approved_only')
    assert set(restarted.load()[0]['records'])==set(ids)


def test_nested_corruption_never_exposes_artifacts(store):
    identity=approved(store);data=json.loads(store.path.read_text())
    data['records'][identity]['artifact']['id']='../../secret';store.path.write_text(json.dumps(data))
    with pytest.raises(MediaError):store.load()
    assert store.public_for(TARGET)['fallback']


def test_pixel_limit_and_lying_mime():
    # This is a valid PNG with a small compressed size but excessive decoded pixels.
    with pytest.raises(ValueError,match='pixel_limit'):prepare(image(size=(3000,3000)))
    assert prepare(image())['source_format']=='PNG' # no reliance on HTTP Content-Type


def test_disabled_policy_refuses_network_before_transport(tmp_path):
    calls=[];service=MediaService(tmp_path/'articles',tmp_path/'data',transport=lambda *a,**kw:calls.append(a))
    with pytest.raises(MediaError,match='downloads_disabled'):
        with service.operation():service.get('https://images.invalid/a')
    assert calls==[] and not service.store.root.exists()
