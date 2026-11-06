"""Offline proofs before changes: HTML rendering and response policy only."""
import json
from datetime import datetime
from bs4 import BeautifulSoup
import pytest
from modules.web import atlas_web as web
from tests.test_admin_security import app, client, password_hash, get


@pytest.fixture
def hostile(app):
    paths = app.extensions['atlas_paths']
    day = datetime.now().strftime('%Y%m%d')
    title = '<img src=x onerror="window.pwned=1"> 日本語 العربية'
    article = {'title': title, 'summary': '<script>window.pwned=2</script>',
               'category': 'tech', 'source': '<svg onload="window.pwned=3">',
               'link': 'javascript:window.pwned=4', 'timestamp': datetime.now().isoformat()}
    (paths['articles'] / f'{day}_articles.json').write_text(json.dumps([article]))
    (paths['reports'] / f'{day}_report.md').write_text('# Bulletin\n\n<script>window.pwned=5</script>\n\n**texte**')
    return paths, day, title


def test_untrusted_article_is_plain_text(client, hostile):
    soup = BeautifulSoup(get(client, '/live').data, 'html.parser')
    assert not soup.select('#live-grid script, #live-grid svg')
    assert all(img.get('src')=='/assets/news-fallback.svg' and not img.get('onerror') for img in soup.select('#live-grid img'))
    assert hostile[2] in soup.get_text()


def test_article_url_is_not_executable(client, hostile):
    soup = BeautifulSoup(get(client, '/live').data, 'html.parser')
    assert not any(a.get('href', '').lower().startswith('javascript:') for a in soup.select('a'))


def test_markdown_html_is_sanitized(client, hostile):
    soup = BeautifulSoup(get(client, '/' + hostile[1] + '_report.html').data, 'html.parser')
    assert not soup.select('.report-body script')
    assert soup.select_one('.report-body strong').get_text() == 'texte'


def test_scripts_do_not_interpret_received_html():
    assert 'eval(' not in web.GLOBAL_SCRIPT


def test_restrictive_csp_is_present(client):
    policy = get(client, '/').headers.get('Content-Security-Policy', '')
    assert "object-src 'none'" in policy
    assert "script-src 'self'" in policy
    assert 'unsafe-eval' not in policy
