import pytest
from modules.fetch.feed_parser import parse_feed


def test_rss_image_candidate_is_preserved_without_becoming_a_license():
    xml=b'<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title>News</title><link>https://news.invalid/a</link><media:thumbnail url="https://images.invalid/a.jpg"/></item></channel></rss>'
    entry=parse_feed(xml,'https://news.invalid/rss','https://news.invalid/rss').entries[0]
    assert entry.images[0]['url']=='https://images.invalid/a.jpg'


def test_open_graph_is_not_rights_verification():
    from modules.media.rights import assess
    assert assess({'method':'og:image','url':'https://news.invalid/photo.jpg'})['status']=='rights_unknown'
