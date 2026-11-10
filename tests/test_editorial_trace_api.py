from tests.test_admin_security import app,password_hash,client,get
from tests.test_frontend_public_api import content
from modules.fetch.editorial_trace import save


def test_private_editorial_traces_have_no_public_routes_or_projection(client,app):
    paths,day,before=content(app)
    marker='PRIVATE-EDITORIAL-TRACE-SYNTHETIC'
    assert save(paths['data'],{'source_transmitted':marker,'raw_response':marker},{'enabled':True})
    identity=next((paths['data']/'editorial_traces').glob('*.json')).stem
    for route in ['/api/articles?date='+day,'/api/public/articles','/api/public/site','/','/live']:
        response=get(client,route)
        assert marker not in response.text and 'source_transmitted' not in response.text
    for route in ['/editorial_traces/'+identity+'.json','/data/editorial_traces/'+identity+'.json','/api/editorial_traces/'+identity]:
        assert get(client,route).status_code==404
    assert (paths['articles']/f'{day}_articles.json').read_bytes()==before
