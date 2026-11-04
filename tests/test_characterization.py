"""Legacy behavior, including limitations: no live sources, LLM or audio service."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from modules.fetch.atlas_fetch import ArticleFetcher
from modules.radio.bulletin_generator import synthesize_all_segments
from modules.utils.helpers import clean_for_tts

FIXTURES = Path(__file__).parent / 'fixtures'

@pytest.fixture
def fetcher():
    obj = ArticleFetcher.__new__(ArticleFetcher)
    obj.log = Mock()
    obj.max_per_feed = 8
    return obj

@pytest.mark.parametrize('name,count', [('rss.xml', 1), ('atom.xml', 0)])
def test_legacy_feed_parser(fetcher, monkeypatch, name, count):
    response = Mock(content=(FIXTURES / name).read_bytes())
    monkeypatch.setattr('modules.fetch.atlas_fetch.source_http.get', Mock(return_value=response))
    result = fetcher._fetch_rss('https://example.invalid/feed')
    assert len(result) == count  # Atom deliberately characterizes current limitation.
    if result:
        assert result[0] == {'title': 'Un titre français',
                             'link': 'https://example.invalid/article',
                             'pub_date': 'Mon, 02 Nov 2026 10:00:00 GMT'}


def test_inaccessible_article_remains_retryable(fetcher, monkeypatch, tmp_path):
    fetcher._apply_config({"paths": {"data_dir": str(tmp_path), "articles_dir": str(tmp_path / "articles")}})
    monkeypatch.setattr(fetcher, '_fetch_content', lambda url, **kw: '')
    seen, existing, articles = set(), set(), []
    item = {'title': 'Titre synthétique assez long pour le filtre',
            'link': 'https://example.invalid/unavailable'}
    assert fetcher._process_item(item, 'tech', seen, existing, articles) is False
    assert seen == existing == set()
    assert fetcher._state['records'][fetcher._hash(item['link'])]['status'] == 'retryable'
    assert articles == []


def test_article_summary_to_spoken_text():
    article = json.loads((FIXTURES / 'articles.json').read_text())[0]
    assert clean_for_tts(article['summary']) == 'Une information synthétique à Paris.'


def test_failed_audio_segment_is_currently_skipped(monkeypatch, tmp_path):
    # Synthetic artifacts only; no valid MP3, Edge TTS, FFmpeg or Icecast.
    def synthesize(text, voice, path):
        if text == 'failure':
            return False
        path.write_bytes(b'synthetic-audio-placeholder')
        return True
    monkeypatch.setattr('modules.radio.bulletin_generator.synthesize_segment', synthesize)
    paths = synthesize_all_segments([('[VOIX1]', 'first'), ('[VOIX1]', 'failure'),
                                    ('[VOIX1]', 'last')], {'[VOIX1]': 'fake'}, tmp_path)
    assert [p.name for p in paths] == ['seg_000_VOIX1.mp3', 'seg_002_VOIX1.mp3']
