"""The old feed test cannot discover an HTML site's declared feeds."""
import pytest
from modules.fetch.feed_parser import parse_feed

HTML=b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss.xml"></head></html>'


def test_html_is_not_itself_a_validated_feed():
    assert parse_feed(HTML,'https://site.invalid/','https://site.invalid/').status=='invalid_feed'


def test_declared_relative_feed_requires_discovery():
    from modules.discovery.source_discovery import html_candidates
    assert html_candidates(HTML,'https://site.invalid/')[0].url=='https://site.invalid/rss.xml'


def test_http_link_requires_discovery():
    from modules.discovery.source_discovery import header_candidates
    assert header_candidates('</atom.xml>; rel="alternate"; type="application/atom+xml"','https://site.invalid/')[0].url=='https://site.invalid/atom.xml'
