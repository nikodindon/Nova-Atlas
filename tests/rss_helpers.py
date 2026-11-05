"""Typed synthetic feed results for article pipeline tests, no transport."""
from modules.fetch.feed_parser import FeedEntry,FeedResult

def result(items):
    return FeedResult('success' if items else 'empty',tuple(FeedEntry(
        feed_url='https://news.example/rss',entry_id='',title=item['title'],link=item['link'],published_raw=item.get('pub_date','')) for item in items))
