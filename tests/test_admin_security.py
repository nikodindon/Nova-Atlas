"""Flask test client only. All credentials and files below are synthetic."""
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pytest
from werkzeug.security import generate_password_hash
import yaml

from modules.web.atlas_web import create_app
from modules.radio import bulletin_generator as bg


@pytest.fixture(scope='module')
def password_hash():
    return generate_password_hash('synthetic-test-password', method='scrypt:32768:8:1')


@pytest.fixture
def app(tmp_path, monkeypatch, password_hash):
    for name in ('ATLAS_ADMIN_USERNAME', 'ATLAS_ADMIN_PASSWORD_HASH', 'ATLAS_SESSION_SECRET',
                 'ATLAS_COOKIE_SECURE', 'ATLAS_TRUSTED_PROXIES'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('ATLAS_ADMIN_USERNAME', 'admin-test')
    monkeypatch.setenv('ATLAS_ADMIN_PASSWORD_HASH', password_hash)
    monkeypatch.setenv('ATLAS_SESSION_SECRET', 'synthetic-session-signing-secret-for-tests')
    root = tmp_path / 'installation'
    (root / 'config').mkdir(parents=True)
    (root / 'config' / 'config.yaml').write_text(yaml.safe_dump({
        'service': {'name': 'Test Atlas'},
        'icecast': {'password': 'synthetic-icecast-credential'},
        'llm': {'api_key': 'synthetic-llm-credential',
                'base_url': 'https://u:synthetic-url-credential@example.invalid/?token=synthetic-query'},
    }))
    (root / 'config' / 'feeds.yaml').write_text('tech:\n  - https://example.invalid/feed\n')
    for directory in ('data/articles', 'data/reports', 'data/editions', 'site', 'audio_queue'):
        (root / directory).mkdir(parents=True)
    app = create_app({}, root=root)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def get(client, path, **kwargs):
    return client.get(path, base_url='https://localhost', **kwargs)


def post(client, path, **kwargs):
    return client.post(path, base_url='https://localhost', **kwargs)


def login(client, password='synthetic-test-password'):
    page = get(client, '/admin/login')
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
    return post(client, '/admin/login', data={'username': 'admin-test', 'password': password, 'csrf_token': token})


def csrf(client):
    return get(client, '/admin/csrf').json['csrf_token']


PRIVATE_GET = ['/config', '/config/simple', '/config/yaml', '/config/feeds',
               '/config/feeds/page', '/preferences', '/api/flash/status/abc',
               '/audio/flash_abc.mp3', '/admin/csrf']
PRIVATE_POST = ['/config/save', '/config/yaml/save', '/config/restart',
                '/config/feeds/add', '/config/feeds/remove', '/config/feeds/reset',
                '/config/feeds/move', '/config/feeds/toggle', '/config/feeds/category',
                '/api/preferences/toggle', '/api/preferences/reset', '/api/flash', '/admin/logout']


@pytest.mark.parametrize('path', PRIVATE_GET)
def test_anonymous_admin_get_refused(client, path):
    assert get(client, path).status_code == 401


@pytest.mark.parametrize('path', PRIVATE_POST)
def test_anonymous_admin_post_refused(client, path):
    assert post(client, path, json={}).status_code == 401


def test_html_navigation_redirects_to_login(client):
    response = get(client, '/config', headers={'Accept': 'text/html'})
    assert response.status_code == 302
    assert response.location == '/admin/login'


def test_login_valid_cookie_and_secret_free_session(client, app):
    response = login(client)
    assert response.status_code == 303
    cookie = response.headers['Set-Cookie']
    assert all(flag in cookie for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'))
    assert get(client, '/config/feeds').status_code == 200
    with client.session_transaction() as state:
        assert set(state) == {'sid', 'csrf_token', '_permanent'}
        assert state['sid'] in app.extensions['atlas_admin']['sessions']


def test_invalid_login(client):
    assert login(client, 'wrong').status_code == 401
    assert get(client, '/config/feeds').status_code == 401


def test_login_requires_csrf(client):
    assert post(client, '/admin/login', data={'username': 'admin-test', 'password': 'synthetic-test-password'}).status_code == 403


@pytest.mark.parametrize('token', [None, 'invalid'])
@pytest.mark.parametrize('path', PRIVATE_POST)
def test_authenticated_post_requires_csrf(client, path, token):
    login(client)
    headers = {'X-CSRF-Token': token} if token else {}
    assert post(client, path, json={}, headers=headers).status_code == 403


def test_authorized_action_and_form_csrf(client, app):
    login(client)
    token = csrf(client)
    response = post(client, '/api/preferences/toggle', json={'category': 'tech'}, headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert json.loads((app.extensions['atlas_paths']['data'] / 'preferences.json').read_text())['hidden_categories'] == ['tech']
    assert post(client, '/api/preferences/reset', data={'csrf_token': token}).status_code == 200


def test_logout_revokes_replayed_cookie(client, app):
    login(client)
    stolen = client.get_cookie('atlas_admin').value
    assert post(client, '/admin/logout', headers={'X-CSRF-Token': csrf(client)}).status_code == 303
    assert get(client, '/config/feeds').status_code == 401
    replay = app.test_client()
    replay.set_cookie('atlas_admin', stolen)
    assert get(replay, '/config/feeds').status_code == 401
    assert get(client, '/admin/logout').status_code in (401, 404, 405)


def test_expiry_cannot_be_extended_by_requests(client, app):
    app.extensions['atlas_admin']['clock'] = lambda: 1000
    login(client)
    app.extensions['atlas_admin']['clock'] = lambda: 4599
    assert get(client, '/config/feeds').status_code == 200
    app.extensions['atlas_admin']['clock'] = lambda: 4600
    assert get(client, '/config/feeds').status_code == 401


def test_login_attempts_limited_even_with_spoofed_forwarded_ip(client, app):
    get(client, '/admin/login')
    with client.session_transaction() as state:
        token = state['csrf_token']
    for _ in range(10):
        assert post(client, '/admin/login', data={'csrf_token': token, 'username': 'nobody', 'password': 'wrong'}).status_code == 401
    response = post(client, '/admin/login', data={'csrf_token': token, 'username': 'admin-test', 'password': 'synthetic-test-password'},
                    headers={'X-Forwarded-For': '10.0.0.2'})
    assert response.status_code == 429
    assert response.headers['Retry-After'] == '300'
    app.extensions['atlas_admin']['clock'] = lambda: 10**12
    assert login(client).status_code == 303


@pytest.mark.parametrize('missing', ['ATLAS_ADMIN_USERNAME', 'ATLAS_ADMIN_PASSWORD_HASH', 'ATLAS_SESSION_SECRET'])
def test_missing_identity_fails_closed(app, monkeypatch, missing):
    monkeypatch.delenv(missing)
    other = create_app({}, root=app.extensions['atlas_paths']['root']).test_client()
    assert get(other, '/admin/login').status_code == 503
    assert get(other, '/config/feeds').status_code == 401
    assert post(other, '/config/feeds/reset').status_code == 401


def test_plaintext_hash_rejected(app, monkeypatch):
    monkeypatch.setenv('ATLAS_ADMIN_PASSWORD_HASH', 'not-a-password-hash')
    assert get(create_app({}, root=app.extensions['atlas_paths']['root']).test_client(), '/admin/login').status_code == 503


def test_headers_do_not_authenticate(client):
    assert get(client, '/config/feeds', headers={'X-Admin': 'true', 'X-Forwarded-User': 'admin-test',
               'Authorization': 'Bearer arbitrary', 'X-Remote-User': 'admin-test'}).status_code == 401
    response = client.get('/admin/login', headers={'X-Forwarded-Proto': 'https'})
    assert response.status_code == 403  # Untrusted peer cannot assert HTTPS.


def test_proxy_trust_is_explicit(app, monkeypatch):
    monkeypatch.setenv('ATLAS_TRUSTED_PROXIES', '127.0.0.1/32')
    trusted = create_app({}, root=app.extensions['atlas_paths']['root']).test_client()
    assert trusted.get('/admin/login', headers={'X-Forwarded-Proto': 'https'}).status_code == 200
    assert trusted.get('/config/feeds', headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-User': 'admin-test'}).status_code == 401


@pytest.mark.parametrize('path', ['/config/yaml', '/config/simple', '/config/feeds/page', '/preferences'])
def test_admin_pages_redact_and_inject_csrf(client, path):
    login(client)
    response = get(client, path)
    assert response.status_code == 200
    for forbidden in ('synthetic-icecast-credential', 'synthetic-llm-credential', 'synthetic-url-credential', 'synthetic-query'):
        assert forbidden not in response.text
    assert 'meta name="csrf-token"' in response.text
    assert 'X-CSRF-Token' in response.text
    assert '/admin/logout' in response.text
    assert response.headers['Cache-Control'] == 'no-store'


def test_secret_not_overwritten_by_empty_or_mask_and_atomic_write(client, app):
    login(client)
    path = app.extensions['atlas_paths']['config_file']
    for value in ('', '********', '[REDACTED]'):
        response = post(client, '/config/yaml/save', json={'section': 'icecast', 'key': 'password', 'value': value}, headers={'X-CSRF-Token': csrf(client)})
        assert response.status_code == 200
        assert yaml.safe_load(path.read_text())['icecast']['password'] == 'synthetic-icecast-credential'
    response = post(client, '/config/yaml/save', json={'section': 'service', 'key': 'name', 'value': 'Atlas Updated'}, headers={'X-CSRF-Token': csrf(client)})
    assert response.status_code == 200
    assert yaml.safe_load(path.read_text())['service']['name'] == 'Atlas Updated'
    assert list(path.parent.glob('.config.yaml*')) == []


@pytest.mark.parametrize('body', [
    {'section': 'paths', 'key': 'data_dir', 'value': '/tmp/escape'},
    {'section': 'icecast', 'value': {}},
    {'section': 'llm', 'key': 'timeout_fetch', 'value': -1},
    {'section': 'service', 'key': 'name', 'value': '<script>alert(1)</script>'},
    {'section': 'llm', 'key': 'base_url', 'value': 'https://u:credential@example.invalid'},
])
def test_config_validation_does_not_write(client, app, body):
    login(client)
    path = app.extensions['atlas_paths']['config_file']
    original = path.read_bytes()
    assert post(client, '/config/yaml/save', json=body, headers={'X-CSRF-Token': csrf(client)}).status_code == 400
    assert path.read_bytes() == original


def test_public_android_contracts(client, app):
    paths = app.extensions['atlas_paths']
    articles = [{'title': 'Synthetic news', 'summary': 'Synthetic summary', 'category': 'tech', 'timestamp': '2026-11-03T12:00:00', 'link': 'https://example.invalid/a'}]
    (paths['articles'] / '20261103_articles.json').write_text(json.dumps(articles))
    response = get(client, '/api/articles?date=20261103&category=tech&limit=1')
    assert response.status_code == 200
    assert response.json == {'status': 'ok', 'date': '20261103', 'count': 1, 'articles': articles}
    assert get(client, '/api/preferences').json['preferences']['hidden_categories'] == []
    assert get(client, '/api/subscription/status?device_id=synthetic-device').json == {
        'status': 'ok', 'device_id': 'synthetic-device', 'is_premium': False, 'expires_at': None, 'source': 'nova-atlas'}
    for path in ('/', '/archives', '/index.html', '/live', '/live.html'):
        assert get(client, path).status_code == 200


@pytest.mark.parametrize('path', ['/config.yaml', '/atlas_config.json', '/old-config.html', '/logs/server.log',
                                  '/api/articles?date=../../config/config', '/editions/../../config/config.html'])
def test_public_paths_cannot_read_private_files(client, path):
    assert get(client, path).status_code in (400, 404)


def test_no_real_files_and_factory_has_no_writes(app):
    root = app.extensions['atlas_paths']['root']
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    create_app({}, root=root)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert before == after
    assert (Path.cwd() / 'config').exists() is False


def test_internal_errors_do_not_expose_credentials(client, monkeypatch):
    login(client)
    monkeypatch.setattr('modules.web.config_security.atomic_text', Mock(side_effect=OSError('synthetic-credential-error')))
    response = post(client, '/config/yaml/save', json={'section': 'service', 'key': 'name', 'value': 'Updated'}, headers={'X-CSRF-Token': csrf(client)})
    assert response.status_code == 500
    assert 'synthetic-credential-error' not in response.text


def test_feed_credentials_are_not_reflected(client, app):
    path = app.extensions['atlas_paths']['feeds_file']
    path.write_text('tech:\n  - https://u:synthetic-private-feed@example.invalid/rss?api_key=synthetic-key\n')
    login(client)
    response = get(client, '/config/feeds')
    assert response.status_code == 200
    assert 'synthetic-private-feed' not in response.text and 'synthetic-key' not in response.text
    assert post(client, '/config/feeds/add', json={'category': 'tech', 'url': 'https://u:synthetic-private-feed@example.invalid/'}, headers={'X-CSRF-Token': csrf(client)}).status_code == 400


def test_new_routes_default_to_private(app, client):
    @app.route('/future-operation', methods=['GET', 'POST'])
    def future_operation():
        return {'status': 'ok'}
    assert get(client, '/future-operation').status_code == 401
    login(client)
    assert get(client, '/future-operation').status_code == 200
    assert post(client, '/future-operation').status_code == 403
    assert post(client, '/future-operation', headers={'X-CSRF-Token': csrf(client)}).status_code == 200


def test_all_registered_routes_classified(app):
    public = {'/', '/archives', '/index.html', '/live', '/live.html', '/stream/<path:mount>',
              '/api/preferences', '/api/articles', '/api/subscription/status',
              '/editions/<path:filename>', '/<path:filename>', '/admin/login'}
    private = set(PRIVATE_GET + PRIVATE_POST)
    private.discard('/api/flash/status/abc')
    private.discard('/audio/flash_abc.mp3')
    private.update({'/api/flash/status/<job_id>', '/audio/flash_<job_id>.mp3'})
    assert {r.rule for r in app.url_map.iter_rules()} == public | private


def test_flash_authorized_but_concurrency_limited(client, monkeypatch):
    # Suppress worker creation completely. No LLM/TTS code can execute.
    thread = Mock()
    monkeypatch.setattr('threading.Thread', thread)
    login(client)
    headers = {'X-CSRF-Token': csrf(client)}
    response = post(client, '/api/flash', json={'category': 'tech'}, headers=headers)
    assert response.status_code == 200
    thread.return_value.start.assert_called_once()
    job_id = response.json['job_id']
    status = get(client, '/api/flash/status/' + job_id)
    assert status.status_code == 200 and status.json['mp3_path'] is None
    assert post(client, '/api/flash', json={'category': 'tech'}, headers=headers).status_code == 429


def test_restart_authorized_with_mocked_rebuild(client, app, monkeypatch):
    rebuild = Mock()
    monkeypatch.setattr('modules.web.atlas_web.generate_static_site', rebuild)
    login(client)
    headers = {'X-CSRF-Token': csrf(client)}
    for _ in range(5):
        assert post(client, '/config/restart', headers=headers).status_code == 200
    assert post(client, '/config/restart', headers=headers).status_code == 429
    assert (app.extensions['atlas_paths']['data'] / '.reload_config').exists()
    assert rebuild.call_args.kwargs['root'] == app.extensions['atlas_paths']['root']


def test_feed_crud_and_simple_config_remain_usable(client, app):
    login(client)
    headers = {'X-CSRF-Token': csrf(client)}
    assert post(client, '/config/feeds/add', json={'category': 'tech', 'url': 'https://example.invalid/new'}, headers=headers).status_code == 200
    assert 'https://example.invalid/new' in get(client, '/config/feeds').json['feeds']['tech']
    assert post(client, '/config/feeds/remove', json={'category': 'tech', 'url': 'https://example.invalid/new'}, headers=headers).status_code == 200
    assert post(client, '/config/save', json={'fetch_timeout': 120, 'csrf_token': csrf(client)}, headers=headers).status_code == 200
    assert json.loads((app.extensions['atlas_paths']['data'] / 'atlas_config.json').read_text())['fetch_timeout'] == 120


def test_credential_replacement_stays_private(client, app):
    login(client)
    assert post(client, '/config/yaml/save', json={'section': 'icecast', 'key': 'password', 'value': 'synthetic-replacement'},
                headers={'X-CSRF-Token': csrf(client)}).status_code == 200
    assert yaml.safe_load(app.extensions['atlas_paths']['config_file'].read_text())['icecast']['password'] == 'synthetic-replacement'
    assert 'synthetic-replacement' not in get(client, '/config/yaml').text


def test_malformed_json_and_unicode_tokens_are_rejected(client):
    login(client)
    assert post(client, '/api/preferences/toggle', json={'category': 'tech'}, headers={'X-CSRF-Token': 'éè'}).status_code == 403
    assert post(client, '/api/preferences/toggle', json=[], headers={'X-CSRF-Token': csrf(client)}).status_code == 400
    assert post(client, '/api/preferences/toggle', data='{', content_type='application/json', headers={'X-CSRF-Token': csrf(client)}).status_code == 400


def test_http_override_does_not_disable_secure_cookie_on_https(app, monkeypatch):
    monkeypatch.setenv('ATLAS_COOKIE_SECURE', '0')
    http_app = create_app({}, root=app.extensions['atlas_paths']['root'])
    client = http_app.test_client()
    assert client.get('/admin/login').status_code == 200
    response = login(client)
    assert 'Secure;' in response.headers['Set-Cookie']


def test_symlink_escape_and_static_html_are_not_served(client, app, tmp_path):
    outside = tmp_path / 'outside-private.txt'
    outside.write_text('synthetic-outside-credential')
    paths = app.extensions['atlas_paths']
    (paths['site'] / 'leak.css').symlink_to(outside)
    (paths['site'] / 'old-config.html').write_text('synthetic-outside-credential')
    assert get(client, '/leak.css').status_code == 404
    assert get(client, '/old-config.html').status_code == 404
    (paths['articles'] / '20261103_articles.json').symlink_to(outside)
    response = get(client, '/api/articles?date=20261103')
    assert response.status_code == 500
    assert 'synthetic-outside-credential' not in response.text


def test_logs_do_not_echo_malformed_feed_credentials(client, app, caplog):
    login(client)
    app.extensions['atlas_paths']['feeds_file'].write_text('synthetic-secret-token-malformed-line\n')
    assert get(client, '/config/feeds').status_code == 200
    assert 'synthetic-secret-token-malformed-line' not in caplog.text


def test_yaml_parallel_field_updates_preserve_other_fields(app):
    from concurrent.futures import ThreadPoolExecutor
    from modules.web.config_security import update_yaml
    path = app.extensions['atlas_paths']['config_file']
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(update_yaml, path, 'service', 'name', 'Concurrent Atlas'),
                 pool.submit(update_yaml, path, 'service', 'tagline', 'Concurrent tagline')]
        for task in tasks:
            task.result()
    config = yaml.safe_load(path.read_text())
    assert config['service'] == {'name': 'Concurrent Atlas', 'tagline': 'Concurrent tagline'}
    assert config['icecast']['password'] == 'synthetic-icecast-credential'


def test_public_stream_contract_with_mock(client, monkeypatch):
    response = Mock(status_code=200, headers={'Content-Type': 'audio/mpeg'})
    response.iter_content.return_value = iter([b'synthetic-mp3'])
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    actual = get(client, '/stream/nova')
    assert actual.status_code == 200 and actual.data == b'synthetic-mp3'


def test_setup_creates_private_file_without_printing_credentials(tmp_path, monkeypatch, password_hash, capsys):
    import shlex
    from modules.web.admin_setup import main
    path = tmp_path / '.env.admin'
    monkeypatch.setattr('sys.argv', ['admin_setup', '--output', str(path)])
    monkeypatch.setattr('builtins.input', lambda _: 'synthetic-admin')
    monkeypatch.setattr('getpass.getpass', lambda _: 'synthetic-password-for-setup')
    monkeypatch.setattr('modules.web.admin_setup.generate_password_hash', lambda *a, **kw: password_hash)
    main()
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'synthetic-password-for-setup' not in path.read_text()
    for line in path.read_text().splitlines()[1:]:
        key, value = line.split('=', 1)
        assert len(shlex.split(value)) == 1
    output = capsys.readouterr().out
    assert password_hash not in output and 'synthetic-password-for-setup' not in output
    original = path.read_bytes()
    with pytest.raises(SystemExit):
        main()
    assert path.read_bytes() == original


def test_api_auth_errors_do_not_redirect_even_for_html_accept(client):
    assert get(client, '/config/feeds', headers={'Accept': 'text/html'}).status_code == 401
    assert get(client, '/api/flash/status/abc', headers={'Accept': 'text/html'}).status_code == 401


@pytest.mark.parametrize('url', ['http://127.0.0.1/rss', 'http://172.17.0.1/rss', 'http://100.64.0.1/rss', 'http://news.example:8080/rss'])
def test_admin_cannot_add_forbidden_source_url(client, url):
    login(client)
    assert post(client, '/config/feeds/add', json={'category': 'tech', 'url': url},
                headers={'X-CSRF-Token': csrf(client)}).status_code == 400


def test_icecast_head_closes_upstream_without_consuming_audio(client, monkeypatch):
    session = Mock()
    response = Mock(status_code=200)
    session.get.return_value = response
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    actual = client.head('/stream/nova', base_url='https://localhost')
    assert actual.status_code == 200
    actual.close()
    response.iter_content.assert_not_called()
    response.close.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize('valid', [True, False])
def test_flash_only_serves_complete_publication(client, app, monkeypatch, valid):
    from tests.test_radio_integrity import publish
    root = app.extensions['atlas_paths']['root']
    path = publish(root / 'audio_queue')
    if not valid:
        from modules.radio.audio_integrity import receipt_path
        receipt_path(path).unlink()
    monkeypatch.setattr(bg, 'get_flash_articles', Mock(return_value=[{'title': 'Synthetic news'}]))
    monkeypatch.setattr(bg, 'generate_flash_script', Mock(return_value='[VOIX1] Synthetic flash.'))
    builder = Mock()
    builder.build.return_value = path
    monkeypatch.setattr(bg, 'BulletinGenerator', Mock(return_value=builder))
    class Thread:
        def __init__(self, target, *, name, **kw):
            self.target, self.name = target, name
        def start(self):
            if self.name.startswith('FlashJob_'):
                self.target()  # Run the mocked job only, never a heartbeat/service.
        def join(self, **kw): pass
    monkeypatch.setattr('threading.Thread', Thread)
    assert login(client).status_code == 303
    response = post(client, '/api/flash', json={'category': 'tech'}, headers={'X-CSRF-Token': csrf(client)})
    assert response.status_code == 200
    job = response.json['job_id']
    status = get(client, '/api/flash/status/' + job).json
    assert status['status'] == ('done' if valid else 'error')
    assert builder.build.call_args.kwargs['profile'] == 'flash'
    audio = get(client, '/audio/flash_' + job + '.mp3')
    assert audio.status_code == (200 if valid else 404)
    if valid:
        assert audio.data == b'synthetic-valid-audio'
        path.write_bytes(b'modified after job completion')
        assert get(client, '/audio/flash_' + job + '.mp3').status_code == 404
