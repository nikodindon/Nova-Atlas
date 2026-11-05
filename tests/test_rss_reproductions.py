"""Sprint 05 acceptance reproductions, executed against the old parser first."""
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock
import pytest
from modules.fetch.atlas_fetch import ArticleFetcher
from modules.http.external import Response,FetchError

FIXTURES=Path(__file__).parent/'fixtures'

@pytest.mark.parametrize('case,expected',[
 ('rss','success'),('atom','success'),('304','not_modified'),
 ('offline','temporarily_unavailable'),('broken','invalid_feed'),('zone','success')])
def test_feed_results_are_explicit_and_preserve_dates(monkeypatch,case,expected):
    obj=ArticleFetcher.__new__(ArticleFetcher);obj.log=Mock();obj.max_per_feed=8
    raw=(FIXTURES/('atom.xml' if case=='atom' else 'rss.xml')).read_bytes()
    if case=='broken':raw=b'<rss><channel><item><title>broken</rss>'
    if case=='zone':raw=b'<rss version="2.0"><channel><item><title>News</title><link>https://example.invalid/a</link><pubDate>Wed, 04 Nov 2026 02:00:00 -0800</pubDate></item></channel></rss>'
    result=Response(raw,304 if case=='304' else 200,{},'https://example.invalid/feed')
    fetch=Mock(side_effect=FetchError('deadline')) if case=='offline' else Mock(return_value=result)
    monkeypatch.setattr('modules.fetch.atlas_fetch.source_http.get',fetch)
    actual=obj._fetch_rss('https://example.invalid/feed')
    assert actual.status==expected
    if case in ('rss','atom'):assert len(actual.entries)==1
    if case=='zone':assert actual.entries[0].published_at==datetime(2026,11,4,10,tzinfo=timezone.utc)
