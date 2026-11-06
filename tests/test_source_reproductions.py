"""Offline regressions: original writer destroyed evidence or accepted unsafe state."""
import pytest
from modules.core.feeds_loader import load_feeds, save_feeds, reorder_feeds


def test_corrupt_source_cannot_be_overwritten(tmp_path):
    path=tmp_path/'feeds.yaml';original='tech:\n - https://example.invalid/rss\nbroken [ yaml\n';path.write_text(original)
    with pytest.raises(ValueError):save_feeds(path,{'tech':['https://example.invalid/new']})
    assert path.read_text()==original


def test_categories_cannot_inject_yaml(tmp_path):
    with pytest.raises(ValueError):save_feeds(tmp_path/'feeds.yaml',{'tech:\nother':['https://example.invalid/rss']})


def test_reorder_cannot_duplicate_source():
    feeds={'tech':['https://example.invalid/a','https://example.invalid/b']}
    assert not reorder_feeds(feeds,'tech',['https://example.invalid/a','https://example.invalid/b','https://example.invalid/a'])
