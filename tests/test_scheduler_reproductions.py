"""Behavioral reproductions against main's news loop, with every service replaced."""
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import main
from modules.core import llm_client
from modules.fetch import atlas_fetch
from modules.report import atlas_report
from modules.editions import atlas_editions
from modules.posts import atlas_posts
from modules.radio import bulletin_generator
from modules.web import atlas_web


class EndProbe(BaseException):
    pass


def run_probe(monkeypatch, times, *, active=False, post_hours=(), config=None):
    times = iter(times)
    started, waits = [], []
    class Clock:
        @staticmethod
        def now():
            try:
                return next(times)
            except StopIteration:
                raise EndProbe
    class Stop:
        def is_set(self):
            return False
        def wait(self, delay):
            waits.append(delay)
            return False
    class Thread:
        def __init__(self, target, args=(), daemon=None, name=None):
            self.name = name
            self.alive = active
        def start(self):
            started.append(self.name)
        def is_alive(self):
            return self.alive
        def join(self, timeout=None):
            self.alive = False
    monkeypatch.setattr(main, 'setup_logging', Mock())
    monkeypatch.setattr(main, 'datetime', Clock)
    monkeypatch.setattr(main.threading, 'Thread', Thread)
    monkeypatch.setattr(main.threading, 'Event', Stop)
    monkeypatch.setattr(main.time, 'sleep', lambda delay: waits.append(delay))
    monkeypatch.setattr(llm_client, 'init_ollama', Mock())
    factories = []
    for module, name in ((atlas_fetch, 'ArticleFetcher'), (atlas_report, 'ReportGenerator'),
                         (atlas_editions, 'EditionGenerator'), (atlas_posts, 'PostsGenerator'),
                         (bulletin_generator, 'BulletinGenerator')):
        factory = Mock()
        monkeypatch.setattr(module, name, factory)
        factories.append(factory)
    monkeypatch.setattr(atlas_web, 'generate_static_site', Mock())
    with pytest.raises(EndProbe):
        main.run_news_engine(config if config is not None else {'radio': {'post_hours': list(post_hours)}})
    fetch_count = factories[0].return_value.run.call_count + sum(name.startswith('fetch') for name in started)
    return started, waits, fetch_count


def test_idle_news_loop_must_wait(monkeypatch):
    _, waits, _ = run_probe(monkeypatch, [datetime(2026, 11, 3, 10, 10)] * 5)
    assert waits and all(delay > 0 for delay in waits)


def test_startup_fetch_must_not_duplicate_current_slot(monkeypatch):
    _, _, fetch_count = run_probe(monkeypatch, [datetime(2026, 11, 3, 6, 0)] * 4)
    assert fetch_count == 1


def test_previous_day_same_fetch_time_is_a_new_slot(monkeypatch):
    _, _, fetch_count = run_probe(monkeypatch, [datetime(2026, 12, 30, 23, 29),
        datetime(2026, 12, 30, 23, 30), datetime(2026, 12, 31, 23, 30)])
    assert fetch_count == 3  # startup + two distinct dated slots


def test_long_posts_cannot_overlap_next_hour(monkeypatch):
    started, _, _ = run_probe(monkeypatch, [datetime(2026, 11, 3, 6, 59),
        datetime(2026, 11, 3, 7, 0), datetime(2026, 11, 3, 9, 0)], active=True, post_hours=(7, 9))
    assert sum(name.startswith('posts') for name in started) == 1


def test_autumn_repeated_hour_does_not_duplicate_civil_slots(monkeypatch):
    zone = ZoneInfo('Europe/Paris')
    _, _, fetch_count = run_probe(monkeypatch, [
        datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=0),
        datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0),
        datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=1),
        datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=1),
    ])
    assert fetch_count == 2
