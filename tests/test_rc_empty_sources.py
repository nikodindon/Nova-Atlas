"""Fresh managed initialization writes {}, which must be editable without a fetch."""
import pytest
from modules.core.source_manager import SourceManager,SourceError
from tests.test_admin_security import app,password_hash,client,get,post,login,csrf


@pytest.mark.parametrize('raw',['{}\n','# retained initialization comment\n{}\n'])
def test_empty_mapping_can_receive_first_category_and_source(tmp_path,raw):
    path=tmp_path/'feeds.yaml';path.write_text(raw);manager=SourceManager(path)
    first=manager.read();assert not first.sources and not first.categories
    manager.mutate('category',{'category':'rc'},first.version)
    assert path.with_name('feeds.yaml.bak').read_text()==raw
    manager.mutate('add',{'category':'rc','url':'https://fixture.invalid/rss'},manager.read().version)
    assert manager.read().sources[0].url=='https://fixture.invalid/rss'
    if raw.startswith('#'):assert '# retained initialization comment' in path.read_text()
    for malformed in ('{}\nrc:\n','{rc: []}\n','# atlas-source {}\n{}\n'):
        path.write_text(malformed)
        with pytest.raises(SourceError):manager.read()
        assert path.read_text()==malformed


def test_fresh_install_admin_first_source_uses_existing_contract(client,app):
    feeds=app.extensions['atlas_paths']['feeds_file'];feeds.write_text('{}\n')
    login(client);listing=get(client,'/api/admin/sources');assert listing.status_code==200
    token=csrf(client);version=listing.json['version']
    response=post(client,'/api/admin/sources/actions/category',json={'category':'rc','version':version},headers={'X-CSRF-Token':token})
    assert response.status_code==200
    response=post(client,'/api/admin/sources',json={'url':'https://fixture.invalid/rss','category':'rc','version':response.json['version']},headers={'X-CSRF-Token':token})
    assert response.status_code==200 and response.json['sources'][0]['url']=='https://fixture.invalid/rss'
