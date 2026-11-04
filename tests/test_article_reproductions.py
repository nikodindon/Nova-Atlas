"""First written and run against the legacy implementation, all services mocked."""
from datetime import datetime
import json
from unittest.mock import Mock

import pytest
from modules.fetch import atlas_fetch
from modules.fetch.atlas_fetch import ArticleFetcher


def fetcher_at(tmp_path, monkeypatch, now=datetime(2026, 11, 4, 12)):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(atlas_fetch, 'datetime', Clock)
    obj = ArticleFetcher.__new__(ArticleFetcher)
    obj._clock = lambda: now
    obj._config = {'paths': {'data_dir': str(tmp_path), 'articles_dir': str(tmp_path / 'articles')}}
    obj._apply_config(obj._config)
    obj.log = Mock()
    monkeypatch.setattr(obj, '_fetch_content', Mock(return_value='Synthetic complete article. '*20))
    monkeypatch.setattr(obj, '_summarize', Mock(return_value='Un résumé synthétique valide.'))
    monkeypatch.setattr(obj, '_translate_title', Mock(side_effect=lambda title, **kw: title))
    monkeypatch.setattr(atlas_fetch.time, 'sleep', Mock())
    return obj


@pytest.mark.parametrize('today,yesterday', [('20261101', '20261031'), ('20270101', '20261231'), ('20240301', '20240229'), ('20230301', '20230228'), ('20240229', '20240228')])
def test_calendar_boundary_preserves_recent_seen(tmp_path, monkeypatch, today, yesterday):
    obj = fetcher_at(tmp_path, monkeypatch, datetime.strptime(today, '%Y%m%d'))
    obj.seen_file.write_text(json.dumps({'recent': yesterday}))
    assert obj._load_seen() == {'recent'}


def test_temporary_extraction_failure_is_not_seen(tmp_path, monkeypatch):
    obj = fetcher_at(tmp_path, monkeypatch)
    monkeypatch.setattr(obj, '_fetch_content', Mock(return_value=''))
    seen, existing, articles = set(), set(), []
    item = {'title': 'Un titre synthétique assez long', 'link': 'https://news.example/article'}
    assert not obj._process_item(item, 'tech', seen, existing, articles)
    assert seen == existing == set()
    assert articles == []


def test_empty_llm_summary_is_not_published(tmp_path, monkeypatch):
    obj = fetcher_at(tmp_path, monkeypatch)
    monkeypatch.setattr(obj, '_summarize', Mock(return_value=''))
    seen, existing, articles = set(), set(), []
    item = {'title': 'Un titre synthétique assez long', 'link': 'https://news.example/article'}
    assert not obj._process_item(item, 'tech', seen, existing, articles)
    assert articles == [] and seen == existing == set()
    assert not obj._today_file().exists()


def test_corrupt_article_file_is_never_overwritten_as_empty(tmp_path, monkeypatch):
    obj = fetcher_at(tmp_path, monkeypatch)
    path = obj._today_file()
    path.write_text('[{"synthetic": broken')
    original = path.read_bytes()
    try:
        articles = obj._load_today()
        obj._save_today(articles)
    except ValueError:
        pass
    assert path.read_bytes() == original


def test_seen_save_prunes_expired_entries(tmp_path, monkeypatch):
    obj = fetcher_at(tmp_path, monkeypatch)
    obj.seen_file.write_text(json.dumps({'expired': '20261001', 'recent': '20261103'}))
    obj._save_seen({'recent'})
    assert json.loads(obj.seen_file.read_text()) == {'recent': '20261103'}
