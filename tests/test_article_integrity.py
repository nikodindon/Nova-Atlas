"""Offline recovery scenarios; only temporary JSON stores and mocked generators."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from unittest.mock import Mock

import pytest
from modules.fetch import atlas_fetch, article_state
from modules.fetch.article_state import StorageError, atomic_json, collection_lock
from modules.http import external as http
from tests.test_article_reproductions import fetcher_at
from tests.rss_helpers import result as feed_result

ITEM = {'title': 'Une information synthétique complète', 'link': 'https://news.example/article',
        'pub_date': 'Wed, 04 Nov 2026 10:00:00 GMT'}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    obj = fetcher_at(tmp_path, monkeypatch)
    now = [datetime(2026, 11, 4, 12)]
    obj._clock = lambda: now[0]
    monkeypatch.setattr(atlas_fetch, '_load_rss_sources', lambda: {'tech': ['https://news.example/rss']})
    monkeypatch.setattr(obj, '_fetch_rss', Mock(return_value=feed_result([dict(ITEM)])))
    obj.advance = lambda **kw: now.__setitem__(0, now[0] + timedelta(**kw))
    return obj


def record(engine):
    return json.loads(engine.state_file.read_text())['records'][engine._hash(ITEM['link'])]


def test_success_preserves_public_schema_and_deduplicates_url(engine):
    engine._fetch_rss.return_value = feed_result([ITEM, {**ITEM, 'title': 'Autre titre pour la même URL'}])
    assert engine.run() == 1
    articles = engine._load_today()
    assert len(articles) == 1
    assert set(articles[0]) == {'hash', 'timestamp', 'category', 'title', 'title_original',
                                'link', 'source', 'pub_date', 'summary'}
    assert record(engine)['status'] == 'succeeded'
    assert engine.run() == 0
    engine._summarize.assert_called_once()


@pytest.mark.parametrize('failure', [TimeoutError('synthetic'), http.FetchError('timeout'),
                                    ValueError('extraction')])
def test_temporary_error_recovered_even_after_feed_drops_article(engine, failure):
    engine._fetch_content.side_effect = [failure, 'A recovered article body']
    assert engine.run() == 0
    assert record(engine)['status'] == 'retryable'
    assert engine._load_seen() == set()
    engine._fetch_rss.return_value = feed_result([])
    assert engine.run() == 0
    assert engine._fetch_content.call_count == 1
    engine.advance(minutes=30)
    assert engine.run() == 1
    assert record(engine)['attempts'] == 2
    assert record(engine)['status'] == 'succeeded'


@pytest.mark.parametrize('status', [404, 410, 401, 403])
def test_permanent_http_failure_is_terminal(engine, status):
    engine._fetch_content.side_effect = http.HTTPStatusError(status)
    assert engine.run() == 0
    assert record(engine)['status'] == 'failed'
    engine.advance(days=2)
    assert engine.run() == 0
    assert engine._fetch_content.call_count == 1
    assert engine._load_seen() == set()


@pytest.mark.parametrize('status', [408, 425, 429, 500, 502, 503])
def test_temporary_http_status(engine, status):
    engine._fetch_content.side_effect = http.HTTPStatusError(status)
    assert engine.run() == 0
    assert record(engine)['status'] == 'retryable'
    assert record(engine)['reason'] == f'http_{status}'


@pytest.mark.parametrize('header', ['7200', 'date'])
def test_retry_after_delays_recovery(engine, header):
    if header == 'date':
        header = format_datetime(datetime.fromtimestamp(engine._now().timestamp() + 7200, timezone.utc))
    engine._fetch_content.side_effect = [http.HTTPStatusError(429, header), 'Recovered text']
    engine.run()
    engine.advance(minutes=119)
    assert engine.run() == 0
    assert engine._fetch_content.call_count == 1
    engine.advance(minutes=1)
    assert engine.run() == 1


@pytest.mark.parametrize('header', ['nonsense', '-1', '', 'Wed, 99 Bad'])
def test_invalid_retry_after_uses_backoff(engine, header):
    engine._fetch_content.side_effect = http.HTTPStatusError(429, header)
    engine.run()
    assert record(engine)['next_attempt_at'] == engine._now().timestamp() + 1800


def test_long_retry_after_requires_manual_review(engine):
    engine._fetch_content.side_effect = http.HTTPStatusError(429, '999999999')
    engine.run()
    assert record(engine)['status'] == 'failed'
    engine.advance(days=40)
    engine.run()
    assert engine._fetch_content.call_count == 1


def test_security_refusal_is_not_retried(engine):
    engine._fetch_content.side_effect = http.BlockedURL('private_destination')
    engine.run()
    assert record(engine)['status'] == 'ignored'
    engine.advance(days=1)
    engine.run()
    assert engine._fetch_content.call_count == 1


@pytest.mark.parametrize('response', ['', '[Timeout]', None, '   '])
def test_invalid_summary_never_published_and_can_recover(engine, response):
    engine._summarize.side_effect = [response, 'Résumé valide et complet.']
    assert engine.run() == 0
    assert engine._load_today() == []
    assert record(engine)['reason'] == 'invalid_summary'
    engine.advance(minutes=30)
    assert engine.run() == 1


def test_llm_exception_then_recovery(engine):
    engine._summarize.side_effect = [TimeoutError('synthetic'), 'Résumé valide.']
    assert engine.run() == 0
    engine.advance(minutes=30)
    assert engine.run() == 1


def test_failed_article_does_not_block_other_articles(engine):
    engine._fetch_rss.return_value = feed_result([ITEM, {**ITEM, 'link': 'https://news.example/second'}])
    engine._fetch_content.side_effect = [TimeoutError(), 'Good content']
    assert engine.run() == 1
    assert len(engine._load_today()) == 1


def test_success_never_overwritten_after_network_failure(engine):
    assert engine.run() == 1
    raw = engine._today_file().read_bytes()
    engine._fetch_content.side_effect = TimeoutError()
    engine._fetch_rss.return_value = feed_result([ITEM, {**ITEM, 'link': 'https://news.example/second'}])
    assert engine.run() == 0
    assert engine._today_file().read_bytes() == raw
    engine._summarize.assert_called_once()


def test_attempt_budget_and_exponential_cap(engine):
    engine.max_attempts = 3
    engine.retry_cap = 2000
    engine._fetch_content.return_value = ''
    engine.run()
    assert record(engine)['next_attempt_at'] == engine._now().timestamp() + 1800
    engine.advance(seconds=1800)
    engine.run()
    assert record(engine)['next_attempt_at'] == engine._now().timestamp() + 2000
    engine.advance(seconds=2000)
    engine.run()
    assert record(engine)['status'] == 'failed'
    engine.advance(days=40)
    engine.run()
    assert engine._fetch_content.call_count == 3


def test_restart_retains_attempts_and_next_attempt(engine, tmp_path, monkeypatch):
    engine._fetch_content.side_effect = TimeoutError()
    engine.run()
    restarted = fetcher_at(tmp_path, monkeypatch, engine._now())
    monkeypatch.setattr(restarted, '_fetch_rss', Mock(return_value=feed_result([])))
    assert restarted.run() == 0
    restarted._fetch_content.assert_not_called()
    restarted._clock = lambda: engine._now() + timedelta(minutes=30)
    assert restarted.run() == 1
    assert record(restarted)['attempts'] == 2


def test_interrupted_article_replace_retains_checkpoint(engine, monkeypatch):
    original = article_state.os.replace
    def interrupt(src, dst):
        if str(dst).endswith('_articles.json'):
            raise OSError('synthetic interruption')
        return original(src, dst)
    monkeypatch.setattr(article_state.os, 'replace', interrupt)
    assert engine.run() == 0
    assert engine._load_today() == []
    assert record(engine)['status'] == 'processing'
    assert record(engine)['prepared']['summary']
    monkeypatch.setattr(article_state.os, 'replace', original)
    engine._fetch_content.side_effect = AssertionError('No refetch allowed')
    engine._summarize.side_effect = AssertionError('No regeneration allowed')
    assert engine.run() == 1
    assert len(engine._load_today()) == 1
    assert record(engine)['status'] == 'succeeded'


def test_interruption_after_article_write_is_reconciled(engine, monkeypatch):
    original = engine._save_state
    def interrupt():
        if any(r['status'] == 'succeeded' for r in engine._state['records'].values()):
            raise OSError('synthetic interruption after publication')
        original()
    monkeypatch.setattr(engine, '_save_state', interrupt)
    engine.run()
    assert len(engine._load_today()) == 1
    assert record(engine)['status'] == 'processing'
    monkeypatch.setattr(engine, '_save_state', original)
    assert engine.run() == 1
    assert record(engine)['status'] == 'succeeded'
    assert len(engine._load_today()) == 1
    engine._summarize.assert_called_once()


def test_generation_checkpoint_recovers_original_day_after_midnight(engine, monkeypatch):
    original = engine._save_today
    monkeypatch.setattr(engine, '_save_today', Mock(side_effect=OSError()))
    engine.run()
    target = engine._today_file()
    monkeypatch.setattr(engine, '_save_today', original)
    engine.advance(days=1)
    assert engine.run() == 1
    assert len(json.loads(target.read_text())) == 1
    assert engine._load_today() == []
    engine._summarize.assert_called_once()


def test_seen_does_not_hide_legacy_false_success(engine):
    engine.seen_file.write_text(json.dumps({engine._hash(ITEM['link']): '20261104'}))
    assert engine.run() == 1


def test_valid_legacy_article_without_hash_prevents_regeneration(engine):
    legacy = {**ITEM, 'summary': 'Résumé historique valide', 'custom_field': 'kept'}
    path = engine.data_dir / '20261103_articles.json'
    path.write_text(json.dumps([legacy]))
    original = path.read_bytes()
    assert engine.run() == 0
    engine._summarize.assert_not_called()
    assert path.read_bytes() == original


def test_optional_legacy_summary_repair_preserves_fields_and_backup(engine):
    legacy = {**ITEM, 'summary': '', 'custom_field': 'kept', 'no_content': True}
    engine._today_file().write_text(json.dumps([legacy]))
    engine.retry_summaries = True
    engine._fetch_rss.return_value = feed_result([])
    assert engine.run() == 1
    assert len(engine._load_today()) == 1
    assert engine._load_today()[0]['custom_field'] == 'kept'
    assert not engine._load_today()[0].get('no_content')
    assert list(engine.data_dir.glob('*.bak'))


@pytest.mark.parametrize('day', ['20261028', '20261029', '20261030', '20261104', '20261105', 'invalid', '20260230', 20261103])
def test_seven_calendar_dates_and_anomaly_backup(engine, day):
    engine.seen_file.write_text(json.dumps({'a': day}))
    expected = {'a'} if str(day) in ('20261029', '20261030', '20261104', '20261103') else set()
    assert engine._load_seen() == expected
    engine._save_seen(expected)
    assert set(json.loads(engine.seen_file.read_text())) == expected
    if str(day) in ('20261105', 'invalid', '20260230'):
        assert list(engine.seen_file.parent.glob('seen_hashes.json.pre-01e.*.bak'))


@pytest.mark.parametrize('name,raw', [('seen_hashes.json', '{broken'),
                                      ('article_processing.json', '{"version": 99}'),
                                      ('articles/20261104_articles.json', '[broken')])
def test_corrupt_store_pauses_without_replacing_original(engine, name, raw):
    path = engine.seen_file.parent / name
    path.write_text(raw)
    assert engine.run() == 0
    assert path.read_text() == raw
    engine._fetch_content.assert_not_called()
    assert list(path.parent.glob(path.name + '*.bak'))


def test_atomic_write_interruption_preserves_original(engine, monkeypatch):
    path = engine._today_file()
    path.write_text('[{"summary": "historical"}]')
    original = path.read_bytes()
    monkeypatch.setattr(article_state.os, 'replace', Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        atomic_json(path, [])
    assert path.read_bytes() == original
    assert not list(path.parent.glob('.' + path.name + '*'))


def test_collector_lock_skips_concurrent_run(engine):
    with collection_lock(engine.seen_file.with_name('article-integrity.lock')):
        assert engine.run() == 0
    engine._fetch_rss.assert_not_called()


def test_config_reload_preserves_retry_configuration(engine, monkeypatch):
    monkeypatch.setattr('modules.core.llm_client.init_ollama', Mock())
    engine.reload_config({**engine._config, 'fetch': {'max_attempts': 5, 'retry_base_seconds': 60,
                                                    'retry_max_seconds': 240}})
    assert (engine.max_attempts, engine.retry_base, engine.retry_cap) == (5, 60, 240)


def test_structured_http_error_does_not_contain_url_or_body():
    response = http.Response(b'synthetic private body', 429, {'retry-after': '7200'},
                             'https://news.example/private')
    with pytest.raises(http.HTTPStatusError) as caught:
        response.raise_for_status()
    assert str(caught.value) == 'http_status'
    assert caught.value.status_code == 429 and caught.value.retry_after == '7200'


def test_real_secure_transport_exposes_retry_after_without_reading_body():
    from tests.test_http_security import client_for, FakeResponse
    response = FakeResponse(status=429, headers={'Retry-After': '7200'})
    response.read1 = Mock(side_effect=AssertionError('No need to download error bodies'))
    client, calls, _ = client_for([response])
    with pytest.raises(http.HTTPStatusError) as caught:
        client.get('https://news.example/article')
    assert caught.value.status_code == 429 and caught.value.retry_after == '7200'
    response.close.assert_called_once()
    calls[0][2].close.assert_called_once()


def test_interrupted_processing_consumes_attempt_and_waits(engine, monkeypatch):
    engine._fetch_content.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        engine.run()
    assert record(engine)['status'] == 'processing' and record(engine)['attempts'] == 1
    engine._fetch_content.side_effect = None
    assert engine.run() == 0
    engine.advance(minutes=30)
    assert engine.run() == 1
    assert record(engine)['attempts'] == 2


def test_tracking_write_failure_never_starts_llm(engine, monkeypatch):
    monkeypatch.setattr(engine, '_save_state', Mock(side_effect=OSError()))
    assert engine.run() == 0
    engine._fetch_content.assert_not_called()
    engine._summarize.assert_not_called()


def test_category_cap_defers_discovered_articles_without_losing_them(engine):
    engine.max_per_category = 1
    engine._fetch_rss.return_value = feed_result([ITEM, {**ITEM, 'link': 'https://news.example/second'}])
    assert engine.run() == 1
    engine._fetch_rss.return_value = feed_result([])
    assert engine.run() == 1
    assert len(engine._load_today()) == 2


def test_retention_expires_pending_without_resetting_attempts(engine):
    engine._fetch_content.side_effect = TimeoutError()
    engine.run()
    engine.advance(days=31)
    engine.run()
    assert record(engine)['status'] == 'failed'
    assert record(engine)['reason'] == 'retention_expired'
    assert engine._fetch_content.call_count == 1


def test_logs_do_not_include_response_exception_or_title(engine):
    engine._fetch_content.side_effect = TimeoutError('SYNTHETIC_PRIVATE_VALUE')
    engine._fetch_rss.return_value = feed_result([{**ITEM, 'title': 'SYNTHETIC_PRIVATE_TITLE'}])
    engine.run()
    assert 'SYNTHETIC_PRIVATE' not in str(engine.log.mock_calls)


@pytest.mark.parametrize('value', [{}, {'version': 1, 'records': {'bad': {}}},
                                  {'version': 1, 'records': {'bad': None}}])
def test_malformed_tracking_schema_preserves_original(engine, value):
    engine.state_file.write_text(json.dumps(value))
    original = engine.state_file.read_bytes()
    assert engine.run() == 0
    assert engine.state_file.read_bytes() == original
    engine._summarize.assert_not_called()


def test_rejected_concurrent_run_does_not_reset_active_commit_count(engine):
    engine._committed_count = 2
    with collection_lock(engine.seen_file.with_name('article-integrity.lock')):
        assert engine.run() == 0
        assert engine._committed_count == 2


def test_partial_cycle_publication_still_requests_site_rebuild(engine, monkeypatch):
    monkeypatch.setattr(engine, '_save_seen', Mock(side_effect=OSError()))
    assert engine.run() == 1
    assert len(engine._load_today()) == 1


def test_corrupted_prepared_article_never_publishes_wrong_url(engine, monkeypatch):
    monkeypatch.setattr(engine, '_save_today', Mock(side_effect=OSError()))
    engine.run()
    state = json.loads(engine.state_file.read_text())
    state['records'][engine._hash(ITEM['link'])]['prepared']['link'] = 'https://other.example/'
    engine.state_file.write_text(json.dumps(state))
    before = engine.state_file.read_bytes()
    assert engine.run() == 0
    assert engine.state_file.read_bytes() == before
    assert engine._load_today() == []
