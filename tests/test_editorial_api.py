import json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from modules.editorial.index import EventIndex,public_event
from tests.test_admin_security import app,password_hash,client,get,post
from tests.test_editorial_events import article,NOW


def prepare(app,rows=None):
    index=app.extensions['atlas_event_index'];index.clock=lambda:NOW
    path=index.articles/'20261106_articles.json';path.write_text(json.dumps(rows or [article(0),article(1)]))
    before=path.read_bytes();result=index.rebuild();index.rebuild(apply=True,expected=result['input_sha'])
    assert path.read_bytes()==before
    return index,result


def test_public_events_list_detail_pagination_no_private_fields(client,app):
    index,result=prepare(app)
    response=get(client,'/api/events');assert response.status_code==200 and response.json['count']==1
    event=response.json['events'][0];assert event['article_count']==2 and event['domain_count']==2
    assert event['independent_confirmation'] is False
    assert 'locations' not in response.text and 'history' not in response.text and 'manifest' not in response.text
    assert '20261106_articles.json' not in response.text
    detail=get(client,'/api/events/'+event['event_id']);assert detail.json['event']==event
    assert get(client,'/api/events?category=unknown').json['count']==0
    assert get(client,'/api/events?page=2&per_page=1').json['events']==[]
    assert get(client,'/api/events/not-found').status_code==404
    assert get(client,'/api/articles?date=20261106').status_code==200


@pytest.mark.parametrize('query',['page=0','per_page=51','page=bad','unknown=1','page=1&page=2','category='+'x'*101])
def test_invalid_event_parameters(client,query):assert get(client,'/api/events?'+query).status_code==400


def test_no_index_or_corrupt_index_has_no_rebuild_side_effect(client,app):
    index=app.extensions['atlas_event_index']
    assert get(client,'/api/events').json['reason']=='index_not_built' and not index.path.exists()
    index.path.write_text('broken')
    response=get(client,'/api/events');assert response.status_code==503 and index.path.read_text()=='broken'
    assert str(index.path) not in response.text


def test_event_api_does_not_expose_credential_urls(client,app):
    index,result=prepare(app,[article(0,link='https://u:synthetic-secret@news.invalid/?token=synthetic-secret')])
    response=get(client,'/api/events');assert response.status_code==200 and 'synthetic-secret' not in response.text
    assert response.json['events'][0]['articles'][0]['link'] is None


def test_event_html_hostile_operator_title_escaped(client,app):
    index,result=prepare(app);sid=result['events'][0]['articles'][0]['id']
    patch={'titles':{sid:'<img src=x onerror="window.pwned=1">'}}
    preview=index.update_overrides(patch);index.update_overrides(patch,apply=True,expected=preview['current_sha'],reason='synthetic title')
    index.rebuild(apply=True,expected=result['input_sha'])
    response=get(client,'/live')
    assert response.status_code==200 and '&lt;img' in response.text
    assert '<img src=x onerror=' not in response.text


def test_breaking_uses_index_and_no_confirmation_claim(client,app):
    from modules.web.atlas_web import get_breaking_news
    now=datetime.now(timezone.utc);index=app.extensions['atlas_event_index'];index.clock=lambda:now
    name=now.astimezone().strftime('%Y%m%d')+'_articles.json';(index.articles/name).write_text(json.dumps([article(0,when=now),article(1,when=now)]))
    result=index.rebuild();index.rebuild(apply=True,expected=result['input_sha'])
    items=get_breaking_news(app.extensions['atlas_paths'])
    assert items[0]['count']==2 and items[0]['source_count']==2 and items[0]['independent_confirmation'] is False
    assert len(items[0]['coverages'])==2
