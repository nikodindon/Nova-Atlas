from unittest.mock import Mock
from datetime import datetime,timezone
import json
from tests.test_admin_security import app,password_hash,get
from tests.test_editorial_events import article
from modules.web.atlas_web import create_app
from modules.media.store import MediaStore


def test_one_manifest_validation_per_public_page(app,monkeypatch):
    paths=app.extensions['atlas_paths'];day=datetime.now(timezone.utc).strftime('%Y%m%d')
    (paths['articles']/f'{day}_articles.json').write_text(json.dumps([article(i,when=datetime.now(timezone.utc)) for i in range(3)]))
    spy=Mock(return_value=({'records':{}},'fixture-version'));monkeypatch.setattr(MediaStore,'load',spy)
    measured=create_app({'media':{'mode':'approved_only'}},root=paths['root'])
    response=get(measured.test_client(),'/api/public/articles?date='+day+'&per_page=2')
    assert response.status_code==200 and len(response.json['articles'])==2
    assert spy.call_count==1


def test_text_projection_cache_is_content_addressed_but_rights_always_rechecked(app,monkeypatch):
    from modules.editorial.events import Coverage
    paths=app.extensions['atlas_paths'];day=datetime.now(timezone.utc).strftime('%Y%m%d');file=paths['articles']/f'{day}_articles.json'
    rows=[article(i,when=datetime.now(timezone.utc)) for i in range(3)];file.write_text(json.dumps(rows))
    original=Coverage.from_article;calls=[]
    def coverage(value,*args,**kwargs):calls.append(value);return original(value,*args,**kwargs)
    monkeypatch.setattr(Coverage,'from_article',coverage)
    rights=Mock(return_value=({'records':{}},'fixture-version'));monkeypatch.setattr(MediaStore,'load',rights)
    measured=create_app({'media':{'mode':'approved_only'}},root=paths['root']);client=measured.test_client();path='/api/public/articles?date='+day
    assert get(client,path).status_code==200 and get(client,path).status_code==200
    assert len(calls)==3 and rights.call_count==2
    rows[0]['title']='Changed authoritative text';file.write_text(json.dumps(rows))
    assert 'Changed authoritative text' in get(client,path).text and len(calls)==6
    file.write_text('{broken');assert get(client,path).status_code==503
    file.unlink();assert get(client,path).json['articles']==[]
    file.touch();assert get(client,path).status_code==503
