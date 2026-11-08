"""Static contract checks: no Docker daemon, private configuration or network."""
from pathlib import Path
import yaml
import pytest
ROOT=Path(__file__).resolve().parents[1]
OVERLAY=yaml.safe_load((ROOT/'docker-compose.frontend.yml').read_text())

@pytest.mark.parametrize('name',['frontend','frontend-gateway'])
def test_frontend_is_opt_in_isolated(name):
    service=OVERLAY['services'][name]
    assert service['profiles']==['frontend']
    assert service['read_only'] and service['cap_drop']==['ALL']
    assert 'secrets' not in service and 'volumes' not in service
    assert service['stop_signal']=='SIGTERM' and service['restart']=='unless-stopped'
    assert service['healthcheck']['test'][0]=='CMD'
    assert service['logging']['options']['max-size']=='10m'
    assert service['user'] not in ('0','0:0','root')

def test_frontend_has_no_published_node_port():
    assert 'ports' not in OVERLAY['services']['frontend']
    assert OVERLAY['services']['frontend-gateway']['ports'][0].startswith('127.0.0.1:')
    assert OVERLAY['networks']['frontend_api']['internal']
    assert 'frontend_edge' in OVERLAY['services']['frontend-gateway']['networks']
    assert 'frontend_edge' not in OVERLAY['services']['frontend']['networks']
    assert 'frontend_edge' not in OVERLAY['services']['web']['networks']

def test_exact_proxy_peers_and_fixed_api_origin():
    web=OVERLAY['services']['web']
    assert '172.30.42.2' in web['environment']['ATLAS_TRUSTED_PROXIES']
    assert '172.30.42.3' in web['environment']['ATLAS_TRUSTED_PROXIES']
    assert OVERLAY['services']['frontend']['environment']['FLASK_ORIGIN']=='http://web:5055'
    assert '0.0.0.0/0' not in str(web)

def test_versioned_images_and_no_secrets_in_build_context():
    for name in ('frontend/Dockerfile','deploy/frontend/Dockerfile'):
        text=(ROOT/name).read_text();assert '@sha256:' in text and ':latest' not in text
        assert 'USER ' in text and 'STOPSIGNAL SIGTERM' in text
    ignore=(ROOT/'frontend/.dockerignore').read_text()
    assert ignore.startswith('*\n') and '!.env' not in ignore and '!tests' not in ignore

def test_nginx_discards_client_forwarded_headers_and_preserves_flask_routes():
    nginx=(ROOT/'deploy/frontend/nginx.conf').read_text()
    assert 'X-Forwarded-Proto ${ATLAS_PUBLIC_SCHEME}' in nginx
    assert 'X-Forwarded-For $remote_addr' in nginx
    assert 'proxy_pass $' not in nginx
    for route in ('api/','admin','assets/','media/','stream/','config','radio-studio'):
        assert route in nginx
    assert 'client_max_body_size 64k' in nginx
    for cache in ('client_body','proxy','fastcgi','uwsgi','scgi'):
        assert f'/tmp/{cache}' in nginx

def test_existing_business_services_are_not_overridden(monkeypatch):
    assert set(OVERLAY['services'])=={'web','frontend','frontend-gateway'}
    assert set(OVERLAY['services']['web'])=={'environment','networks'}
    from deploy.frontend_smoke import main
    from types import SimpleNamespace
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(SystemExit,match='explicit --local-fixtures'):main()
    monkeypatch.setattr('deploy.frontend_smoke.shutil.disk_usage',lambda _:SimpleNamespace(free=0))
    with pytest.raises(SystemExit,match='4 GiB'):main(allow_local=True)


@pytest.mark.parametrize('name',['frontend','frontend-gateway'])
def test_tmpfs_options_remain_one_absolute_mount(name):
    mounts=OVERLAY['services'][name]['tmpfs']
    assert len(mounts)==1
    assert mounts[0].startswith('/tmp:') and 'noexec,nosuid' in mounts[0]
