import json
from datetime import datetime,timezone
import pytest
from tests.test_admin_security import app,password_hash,client,get,post,login,csrf
from tests.test_editorial_events import article
from tests.test_news_images import image,candidate,payload
from modules.media.worker import prepare
from modules.media.service import MediaService
from modules.media.store import MediaConflict
from modules.media.export import export_images


def setup(app):
    service=app.extensions['atlas_media'];service.store.mode='approved_only';service.store.runner=prepare
    path=service.articles/(datetime.now().strftime('%Y%m%d')+'_articles.json')
    path.write_text(json.dumps([article(0,when=datetime.now(timezone.utc))]));before=path.read_bytes()
    target=service.targets()[0];identity=service.store.add(candidate(),target,trusted=True)
    service.store.prepare(identity,image(),expected=service.store.load()[1]);return service,identity,path,before


def test_admin_routes_auth_csrf_and_public_no_leak(client,app):
    service,identity,path,before=setup(app)
    assert get(client,'/api/admin/media').status_code==401
    assert get(client,f'/api/admin/media/{identity}/preview/320.jpg').status_code==401
    assert post(client,'/api/admin/media/search',json={'query':'test'}).status_code==401
    assert login(client).status_code==303
    assert post(client,f'/api/admin/media/{identity}/approve',json={}).status_code==403
    listing=get(client,'/api/admin/media').json;row=listing['records'][0]
    response=post(client,f'/api/admin/media/{identity}/approve',json={'version':listing['version'],'binding':row['approval_binding'],'ack':True},headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==200
    target=row['target'];query='&'.join(f'{k}={target[k]}' for k in ('kind','id','fingerprint'))
    projection=get(client,'/api/media?'+query).json['image'];assert not projection['fallback']
    binary=get(client,projection['url']);assert binary.mimetype=='image/jpeg' and binary.headers['Cache-Control']=='no-store'
    assert "img-src 'self'" in binary.headers['Content-Security-Policy']
    assert b'EXIF' not in binary.data
    public=get(client,'/api/media?'+query).text
    assert 'file_sha1' not in public and 'approval_binding' not in public and 'origins' not in public
    assert 'synthetic-llm-credential' not in public
    assert path.read_bytes()==before
    # A changed article invalidates the context, independently of the manifest approval.
    path.write_text(json.dumps([article(0,title='Changed editorial context')]))
    assert get(client,'/api/media?'+query).json['image']['fallback']
    assert get(client,projection['url']).mimetype=='image/svg+xml'


def test_invalid_body_disabled_and_unknown_rights(client,app):
    assert login(client).status_code==303;headers={'X-CSRF-Token':csrf(client)}
    for value in ({'query':'x','target':{}},{'url':'http://127.0.0.1/'}):
        assert post(client,'/api/admin/media/search',json=value,headers=headers).status_code==400
    assert post(client,'/api/admin/media/cancel',data='{"a":1,"a":2}',content_type='application/json',headers=headers).status_code==400
    assert get(client,'/api/admin/media?page=0').status_code==400
    assert get(client,'/api/media?url=http://127.0.0.1').status_code==400
    assert get(client,'/media/images/bad/1.jpg').mimetype=='image/svg+xml'


def test_export_current_approved_only_and_revocation(app,tmp_path):
    service,identity,path,before=setup(app);row=service.store.load()[0]['records'][identity]
    service.store.decide(identity,'approve',expected=service.store.load()[1],binding=service.store.binding(row),ack=True)
    site=tmp_path/'export';export_images(service,site)
    files=list(site.rglob('*.jpg'));assert len(files)==2
    service.store.decide(identity,'disable',expected=service.store.load()[1]);export_images(service,site)
    assert list(site.rglob('*.jpg'))==[]
    assert path.read_bytes()==before and (service.store.root/'variants'/row['artifact']['id']).exists()


def test_event_separation_revokes_visual_context(app,monkeypatch):
    service,identity,path,before=setup(app)
    target={'kind':'event','id':'event1','fingerprint':'a'*64}
    monkeypatch.setattr('modules.editorial.index.EventIndex.load',lambda self:{'events':[]})
    assert service.valid_target(target) is False


def test_network_error_and_timeout_sanitized(app):
    from modules.http.external import FetchError
    service=app.extensions['atlas_media'];service.store.mode='suggest'
    def fail(*args,**kwargs):raise FetchError('synthetic-private-error-with-token')
    service.transport=fail
    with service.operation(),pytest.raises(ValueError,match='network_error'):service.get('https://images.invalid/photo')


def test_disabled_or_corrupt_media_does_not_break_static_fallback(app,tmp_path):
    service=app.extensions['atlas_media'];service.store.init();service.store.path.write_text('broken')
    site=tmp_path/'export';export_images(service,site)
    assert list((site/'media/images').iterdir())==[]
    assert service.store.path.read_text()=='broken'
