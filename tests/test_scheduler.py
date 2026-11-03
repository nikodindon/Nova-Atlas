"""Scheduling decisions, races and shutdown, without external processes or long sleeps."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import main
from modules.core.scheduler import (
    DueTask, ThreadRunner, initial_fetch, news_tasks, radio_tasks, run_loop,
    validate_hours, wait_seconds,
)
from tests.test_scheduler_reproductions import EndProbe, run_probe
from modules.core import llm_client


class ManualThread:
    def __init__(self, target, *, daemon, name):
        self.target, self.daemon, self.name = target, daemon, name
        self.alive = False
    def start(self):
        self.alive = True
    def is_alive(self):
        return self.alive
    def finish(self):
        try:
            self.target()
        finally:
            self.alive = False
    def join(self):
        if self.alive:
            self.finish()


@pytest.fixture
def runner():
    threads = []
    def factory(**kwargs):
        thread = ManualThread(**kwargs)
        threads.append(thread)
        return thread
    return ThreadRunner(thread_factory=factory), threads


@pytest.mark.parametrize('hour', range(24))
@pytest.mark.parametrize('minute', [0, 1, 30, 31])
def test_fetch_at_historical_hours(hour, minute):
    tasks = news_tasks(datetime(2026, 11, 3, hour, minute), [])
    assert sum(t.group == 'fetch' for t in tasks) == 1


@pytest.mark.parametrize('minute', [2, 15, 29, 32, 59])
def test_fetch_outside_window(minute):
    assert all(t.group != 'fetch' for t in news_tasks(datetime(2026, 11, 3, 10, minute), []))


@pytest.mark.parametrize('hour,name', [(6, 'matin'), (12, 'midi'), (19, 'soir')])
@pytest.mark.parametrize('minute,expected', [(0, True), (4, True), (5, False)])
def test_editions(hour, name, minute, expected):
    tasks = [t for t in news_tasks(datetime(2026, 11, 3, hour, minute), []) if t.group == 'edition']
    assert len(tasks) == int(expected)
    if tasks:
        assert tasks[0].args == (name, '20261103')


@pytest.mark.parametrize('hour,minute,group', [(23, 0, 'report'), (23, 4, 'report'),
                                             (23, 30, 'rebuild'), (23, 34, 'rebuild')])
def test_daily_report_and_rebuild(hour, minute, group):
    assert group in {t.group for t in news_tasks(datetime(2026, 11, 3, hour, minute), [])}


def test_daily_report_and_rebuild_window_end():
    assert 'report' not in {t.group for t in news_tasks(datetime(2026, 11, 3, 23, 5), [])}
    assert 'rebuild' not in {t.group for t in news_tasks(datetime(2026, 11, 3, 23, 35), [])}


@pytest.mark.parametrize('hour,minute,expected', [(7, 0, True), (7, 2, True), (7, 3, False), (8, 0, False)])
def test_posts_respect_configured_hours(hour, minute, expected):
    tasks = [t for t in news_tasks(datetime(2026, 11, 3, hour, minute), [7, 9]) if t.group == 'posts']
    assert len(tasks) == int(expected)
    if tasks:
        assert tasks[0].args == ('20261103',)


@pytest.mark.parametrize('before,after', [
    (datetime(2026, 11, 3, 0), datetime(2026, 11, 4, 0)),
    (datetime(2026, 11, 30, 0), datetime(2026, 12, 1, 0)),
    (datetime(2026, 12, 31, 0), datetime(2027, 1, 1, 0)),
])
def test_slots_include_calendar_date(before, after):
    assert initial_fetch(before).slot != initial_fetch(after).slot


def test_same_civil_slot_ignores_dst_fold():
    zone = ZoneInfo('Europe/Paris')
    a = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=0)
    b = a.replace(fold=1)
    assert a.timestamp() != b.timestamp()
    assert news_tasks(a, [2]) == news_tasks(b, [2])


def test_spring_missing_hour_is_not_replayed(runner):
    instance, threads = runner
    zone = ZoneInfo('Europe/Paris')
    before = datetime(2026, 3, 29, 1, 59, tzinfo=zone)
    after = datetime(2026, 3, 29, 3, 0, tzinfo=zone)
    # Feed only real clock instants. No synthesized 02:00 task/catchup.
    assert all(t.group != 'posts' for now in (before, after) for t in news_tasks(now, [2]))
    assert initial_fetch(after).slot.endswith('T03:00')


@pytest.mark.parametrize('second,microsecond,expected', [(0, 0, 60), (30, 0, 30), (59, 500000, .5)])
def test_wait_is_bounded_and_aligned(second, microsecond, expected):
    assert wait_seconds(datetime(2026, 11, 3, 10, 10, second, microsecond)) == expected


def test_loop_is_interruptible_and_drains_startup(runner):
    instance, threads = runner
    stop = threading.Event()
    wait = Mock(side_effect=lambda delay: stop.set() or True)
    startup, fetch = Mock(), Mock()
    clock = Mock(return_value=datetime(2026, 11, 3, 10, 0))
    run_loop(lambda now: news_tasks(now, []), {'fetch': fetch}, stop,
             runner=instance, clock=clock, wait=wait, startup=startup)
    assert clock.call_count == 2  # Fresh time after initial fetch, then normal tick.
    wait.assert_called_once_with(60)
    startup.assert_called_once()
    fetch.assert_not_called()  # Same slot already reserved by startup.
    assert instance.active_count == 0
    assert all(t.daemon is False for t in threads)


def test_stop_before_loop_does_not_start_or_wait(runner):
    instance, threads = runner
    stop = threading.Event()
    stop.set()
    clock, wait = Mock(), Mock()
    run_loop(Mock(), {}, stop, runner=instance, clock=clock, wait=wait, startup=Mock())
    clock.assert_not_called()
    wait.assert_not_called()
    assert not threads


def test_shutdown_does_not_interrupt_active_job(runner):
    instance, threads = runner
    job = Mock()
    assert instance.submit(DueTask('fetch', '2026-11-03T10:00'), job)
    assert job.call_count == 0
    instance.close()
    job.assert_called_once()
    assert instance.active_count == 0
    assert not instance.submit(DueTask('fetch', '2026-11-03T10:30'), job)


def test_overlap_is_logged_and_consumed_once(runner, caplog):
    instance, threads = runner
    job = Mock()
    assert instance.submit(DueTask('fetch', 'first'), job)
    second = DueTask('fetch', 'second')
    assert not instance.submit(second, job)
    assert 'précédente exécution active' in caplog.text
    threads[0].finish()
    assert not instance.submit(second, job)  # No late retry within same observed slot.
    assert instance.submit(DueTask('fetch', 'third'), job)
    instance.close()
    assert job.call_count == 2


def test_different_groups_may_run_together(runner):
    instance, threads = runner
    assert instance.submit(DueTask('fetch', 'first'), Mock())
    assert instance.submit(DueTask('report', 'first'), Mock())
    assert instance.active_count == 2
    instance.close()


def test_completed_threads_and_history_are_bounded():
    threads = []
    def factory(**kwargs):
        thread = ManualThread(**kwargs)
        threads.append(thread)
        return thread
    instance = ThreadRunner(thread_factory=factory, history_limit=8)
    for i in range(100):
        assert instance.submit(DueTask('fetch', str(i)), Mock())
        threads[-1].finish()
        assert instance.active_count == 0
    assert instance.history_count == 8
    instance.close()


def test_thread_start_failure_does_not_leave_busy_group(caplog):
    broken = Mock()
    broken.start.side_effect = RuntimeError('synthetic start failure')
    instance = ThreadRunner(thread_factory=Mock(return_value=broken))
    assert not instance.submit(DueTask('fetch', 'slot'), Mock())
    assert instance.active_count == 0
    assert 'Démarrage impossible' in caplog.text
    instance.close()


def test_exception_does_not_block_next_slot(runner, caplog):
    instance, threads = runner
    assert instance.submit(DueTask('posts', 'slot1'), Mock(side_effect=ValueError('synthetic')))
    threads[0].finish()
    assert instance.active_count == 0
    assert 'Échec de tâche posts' in caplog.text
    assert instance.submit(DueTask('posts', 'slot2'), Mock())
    instance.close()


def test_concurrent_admission_is_atomic():
    # Real in-memory threads only; events/barrier, no sleep, network or subprocess.
    instance = ThreadRunner()
    ready = threading.Barrier(3)
    hold = threading.Event()
    entered = threading.Event()
    count, lock, results = [], threading.Lock(), []
    def job():
        with lock:
            count.append(1)
        entered.set()
        assert hold.wait(2)
    def caller(slot):
        ready.wait(timeout=2)
        result = instance.submit(DueTask('fetch', slot), job)
        with lock:
            results.append(result)
    callers = [threading.Thread(target=caller, args=(str(i),)) for i in range(2)]
    try:
        for thread in callers:
            thread.start()
        ready.wait(timeout=2)
        assert entered.wait(2)
        for thread in callers:
            thread.join(timeout=2)
            assert not thread.is_alive()
    finally:
        hold.set()
        instance.close()
    assert sorted(results) == [False, True]
    assert len(count) == 1 and instance.active_count == 0


def test_process_restart_keeps_historical_in_memory_policy(runner):
    first, threads = runner
    task, work = DueTask('fetch', '2026-11-03T10:00'), Mock()
    first.submit(task, work)
    first.close()
    second = ThreadRunner(thread_factory=ManualThread)
    assert second.submit(task, work)  # History is deliberately not durable.
    second.close()
    assert work.call_count == 2


@pytest.mark.parametrize('hours', [[-1], [24], ['7'], [True], None, '7,9'])
def test_invalid_hours(hours):
    with pytest.raises(ValueError):
        validate_hours(hours)


def test_valid_empty_and_duplicate_hours():
    assert validate_hours([]) == ()
    assert validate_hours([9, 7, 9]) == (7, 9)


def test_news_reload_applies_config_before_current_decision(monkeypatch, tmp_path):
    (tmp_path / 'data').mkdir()
    flag = tmp_path / 'data' / '.reload_config'
    flag.touch()
    new_config = {'radio': {'post_hours': [7]}}
    reload = Mock()
    monkeypatch.setattr(main, 'load_config', Mock(return_value=new_config))
    monkeypatch.setattr(llm_client, 'reload_ollama', reload)
    started, _, _ = run_probe(monkeypatch, [datetime(2026, 11, 3, 7, 0)]*2)
    assert sum(name.startswith('posts') for name in started) == 1
    reload.assert_called_once_with(new_config)
    assert not flag.exists()


def test_news_reload_does_not_reset_seen_slots(monkeypatch, tmp_path):
    from modules.core import scheduler
    initial = scheduler.ThreadRunner
    admitted = []
    class Runner(initial):
        def submit(self, task, function):
            result = super().submit(task, function)
            if result:
                admitted.append(task)
            return result
    monkeypatch.setattr(main, 'ThreadRunner', Runner)
    (tmp_path / 'data').mkdir()
    flag = tmp_path / 'data' / '.reload_config'
    flag.touch()
    monkeypatch.setattr(main, 'load_config', Mock(return_value={'radio': {'post_hours': [7]}}))
    monkeypatch.setattr(llm_client, 'reload_ollama', Mock())
    run_probe(monkeypatch, [datetime(2026, 11, 3, 7, 0)]*4, post_hours=[7])
    assert len({(t.group, t.slot) for t in admitted}) == len(admitted)


@pytest.mark.parametrize('now,hours,expected', [
    (datetime(2026, 11, 3, 6, 55), [7], '2026-11-03T07:00'),
    (datetime(2026, 11, 3, 7, 25), [7], '2026-11-03T07:30'),
    (datetime(2026, 11, 3, 7, 26, 59), [7], '2026-11-03T07:30'),
    (datetime(2026, 11, 3, 7, 55), [7], None),
    (datetime(2026, 11, 3, 7, 27), [7], None),
    (datetime(2026, 11, 3, 5, 25), [7], None),
    (datetime(2026, 12, 31, 23, 55), [0], '2027-01-01T00:00'),
])
def test_radio_target_hours_and_windows(now, hours, expected):
    tasks = radio_tasks(now, hours)
    assert ([task.slot for task in tasks] or [None]) == [expected]


def test_radio_dst_targets_real_future_instant():
    zone = ZoneInfo('Europe/Paris')
    spring = datetime(2026, 3, 29, 1, 55, tzinfo=zone)
    assert radio_tasks(spring, [3])[0].slot == '2026-03-29T03:00'
    assert radio_tasks(spring, [2]) == []
    autumn_first = datetime(2026, 10, 25, 2, 55, tzinfo=zone, fold=0)
    assert radio_tasks(autumn_first, [2])[0].slot == '2026-10-25T02:00'
    autumn_second = autumn_first.replace(fold=1)
    assert radio_tasks(autumn_second, [3])[0].slot == '2026-10-25T03:00'


def test_radio_does_not_duplicate_or_overlap(runner):
    instance, threads = runner
    job = Mock()
    for now in (datetime(2026, 11, 3, 7, 25), datetime(2026, 11, 3, 7, 26), datetime(2026, 11, 3, 7, 55)):
        for task in radio_tasks(now, [7, 8]):
            instance.submit(task, job)
    assert len(threads) == 1
    instance.close()
    job.assert_called_once()


def test_scheduler_stop_entry_points_do_not_initialize_services(monkeypatch):
    stop = threading.Event()
    stop.set()
    setup = Mock()
    monkeypatch.setattr(main, 'setup_logging', setup)
    main.run_news_engine({}, stop_event=stop)
    main.run_radio({}, stop_event=stop)
    setup.assert_not_called()


def test_parent_stops_scheduler_cooperatively():
    process = SimpleNamespace(scheduler_stop_event=Mock(), join=Mock(), terminate=Mock())
    main._stop_process(process)
    process.scheduler_stop_event.set.assert_called_once()
    process.join.assert_called_once_with()
    process.terminate.assert_not_called()


def test_parent_preserves_other_service_stop():
    process = SimpleNamespace(is_alive=lambda: True, terminate=Mock(), join=Mock())
    main._stop_process(process)
    process.terminate.assert_called_once()
    process.join.assert_called_once_with(timeout=5)


def test_scheduler_process_signals_only_request_stop(monkeypatch):
    handlers, stop, job = {}, Mock(), Mock()
    monkeypatch.setattr(main.signal, 'signal', lambda sig, fn: handlers.__setitem__(sig, fn))
    main._scheduler_process(job, ({'synthetic': True}, False), stop)
    job.assert_called_once_with({'synthetic': True}, False, stop_event=stop)
    handlers[main.signal.SIGINT](None, None)
    assert stop.set.call_count == 1
    if hasattr(main.signal, 'SIGTERM'):
        handlers[main.signal.SIGTERM](None, None)
        assert stop.set.call_count == 2


def test_restart_creates_fresh_stop_events_without_processes(monkeypatch):
    first, second = Mock(), Mock()
    events = Mock(side_effect=[first, second])
    factory = Mock()
    monkeypatch.setattr(main.multiprocessing, 'Event', events)
    monkeypatch.setattr(main.multiprocessing, 'Process', factory)
    main._start_process(main.run_news_engine, 'NewsEngine', {}, False)
    first.set()
    main._start_process(main.run_news_engine, 'NewsEngine', {}, False)
    assert factory.call_args.kwargs['target'] is main._scheduler_process
    assert factory.call_args.kwargs['args'] == (main.run_news_engine, ({}, False), second)
    second.set.assert_not_called()


def test_reload_is_deferred_without_consuming_flag_while_jobs_active(monkeypatch, tmp_path, caplog):
    (tmp_path / 'data').mkdir()
    flag = tmp_path / 'data' / '.reload_config'
    loader, reload = Mock(), Mock()
    monkeypatch.setattr(main, 'load_config', loader)
    monkeypatch.setattr(llm_client, 'reload_ollama', reload)
    def times():
        yield datetime(2026, 11, 3, 6, 59)  # startup
        yield datetime(2026, 11, 3, 7, 0)  # long fetch/posts
        flag.touch()
        yield datetime(2026, 11, 3, 7, 1)
        yield datetime(2026, 11, 3, 7, 2)
    run_probe(monkeypatch, times(), active=True, post_hours=[7])
    assert flag.exists()
    loader.assert_not_called()
    reload.assert_not_called()


def test_deferred_reload_applies_after_running_jobs_finish(monkeypatch, tmp_path):
    from modules.core.scheduler import ThreadRunner as Original
    instances = []
    class Runner(Original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)
    monkeypatch.setattr(main, 'ThreadRunner', Runner)
    (tmp_path / 'data').mkdir()
    flag = tmp_path / 'data' / '.reload_config'
    new_config = {'radio': {'post_hours': [8]}}
    loader, reload = Mock(return_value=new_config), Mock()
    monkeypatch.setattr(main, 'load_config', loader)
    monkeypatch.setattr(llm_client, 'reload_ollama', reload)
    def times():
        yield datetime(2026, 11, 3, 6, 59)
        yield datetime(2026, 11, 3, 7, 0)
        flag.touch()
        yield datetime(2026, 11, 3, 7, 1)
        instances[0].join_group('fetch')
        instances[0].join_group('posts')
        yield datetime(2026, 11, 3, 8, 0)
    started, _, _ = run_probe(monkeypatch, times(), active=True, post_hours=[7])
    assert sum(name.startswith('posts') for name in started) == 2
    reload.assert_called_once_with(new_config)
    assert not flag.exists()


def test_startup_order_and_fresh_clock_after_initial_work(runner):
    instance, threads = runner
    stop = threading.Event()
    trace = []
    def startup():
        trace.append('startup-done')
    def plan(now):
        assert trace == ['startup-done']
        assert now.hour == 12  # Startup can finish outside its original window.
        return []
    clock = Mock(side_effect=[datetime(2026, 11, 3, 11, 59), datetime(2026, 11, 3, 12, 0)])
    run_loop(plan, {}, stop, runner=instance, clock=clock,
             wait=lambda delay: stop.set() or True, startup=startup)


def test_stop_requested_during_startup_admits_no_other_job(runner):
    instance, threads = runner
    stop, plan, wait = threading.Event(), Mock(), Mock()
    run_loop(plan, {}, stop, runner=instance, clock=lambda: datetime(2026, 11, 3, 6),
             wait=wait, startup=stop.set)
    plan.assert_not_called()
    wait.assert_not_called()
    assert len(threads) == 1


def radio_probe(monkeypatch, times, configurations):
    from modules.radio import streamer, bulletin_generator
    calls, waits = [], []
    times = iter(times)
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
        def start(self):
            calls.append(self.name)  # Never execute any service/generation target.
        def is_alive(self):
            return False
        def join(self):
            pass
    monkeypatch.setattr(main, 'setup_logging', Mock())
    monkeypatch.setattr(main, 'datetime', Clock)
    monkeypatch.setattr(main.threading, 'Thread', Thread)
    monkeypatch.setattr(main.threading, 'Event', Stop)
    primary, android = Mock(), Mock()
    monkeypatch.setattr(streamer, 'Streamer', Mock(return_value=primary))
    monkeypatch.setattr(streamer, 'AndroidStreamer', Mock(return_value=android))
    builder = Mock()
    monkeypatch.setattr(bulletin_generator, 'BulletinGenerator', builder)
    monkeypatch.setattr(bulletin_generator, 'load_bulletins_config', Mock(side_effect=configurations))
    with pytest.raises(EndProbe):
        main.run_radio({})
    primary.stop.assert_called_once()
    android.stop.assert_called_once()
    primary.run.assert_not_called()
    builder.return_value.build.assert_not_called()
    return calls, waits


def test_main_radio_respects_reloaded_yaml_hours_without_services(monkeypatch):
    calls, waits = radio_probe(monkeypatch,
        [datetime(2026, 11, 3, 6, 55), datetime(2026, 11, 3, 7, 25), datetime(2026, 11, 3, 7, 55)],
        [{'post_hours': [7]}, {'post_hours': [8]}, {'post_hours': [8]}])
    slots = [name for name in calls if name.startswith('bulletin')]
    assert slots == ['bulletin:2026-11-03T07:00', 'bulletin:2026-11-03T08:00']
    assert len(waits) == 3


def test_radio_invalid_calendar_keeps_last_valid_hours(monkeypatch, caplog):
    calls, waits = radio_probe(monkeypatch,
        [datetime(2026, 11, 3, 6, 55), datetime(2026, 11, 3, 7, 25)],
        [{'post_hours': [7]}, {'post_hours': [99]}])
    assert len([name for name in calls if name.startswith('bulletin')]) == 2
    assert 'dernières heures valides conservées' in caplog.text


def cli_args(**overrides):
    values = dict(all=False, news=True, radio=False, web=False, cleanup=False, build=False,
                  fetch=False, edition=None, report=None, debug=False, config='config/config.yaml', port=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_watchdog_restarts_with_latest_config(monkeypatch):
    monkeypatch.setattr(main, 'parse_args', lambda: cli_args())
    monkeypatch.setattr(main, 'setup_logging', Mock())
    monkeypatch.setattr(main.signal, 'signal', Mock())
    old, new = {'service': {'name': 'Old'}}, {'service': {'name': 'New'}}
    loader = Mock(side_effect=[old, new])
    monkeypatch.setattr(main, 'load_config', loader)
    crashed = SimpleNamespace(is_alive=lambda: False, exitcode=1)
    replacement = SimpleNamespace(is_alive=lambda: True)
    start = Mock(side_effect=[crashed, replacement])
    monkeypatch.setattr(main, '_start_process', start)
    waits = []
    def sleep(delay):
        if delay == 15:
            waits.append(delay)
            if len(waits) > 1:
                raise EndProbe
    monkeypatch.setattr(main.time, 'sleep', sleep)
    with pytest.raises(EndProbe):
        main.main()
    assert start.call_count == 2
    assert start.call_args.args == (main.run_news_engine, 'NewsEngine', new, False)


def test_parent_shutdown_requests_all_stops_before_join(monkeypatch):
    monkeypatch.setattr(main, 'parse_args', lambda: cli_args(all=True))
    monkeypatch.setattr(main, 'setup_logging', Mock())
    monkeypatch.setattr(main, 'load_config', Mock(return_value={}))
    handlers, trace = {}, []
    monkeypatch.setattr(main.signal, 'signal', lambda sig, fn: handlers.__setitem__(sig, fn))
    def process(name, cooperative):
        obj = SimpleNamespace(is_alive=lambda: True, terminate=Mock(), join=Mock())
        if cooperative:
            obj.scheduler_stop_event = Mock()
            obj.scheduler_stop_event.set.side_effect = lambda: trace.append('stop:'+name)
            obj.join.side_effect = lambda: trace.append('join:'+name)
        return obj
    news, radio, web = process('news', True), process('radio', True), process('web', False)
    monkeypatch.setattr(main, '_start_process', Mock(side_effect=[news, radio, web]))
    def sleep(delay):
        if delay == 15:
            handlers[main.signal.SIGINT](None, None)
    monkeypatch.setattr(main.time, 'sleep', sleep)
    with pytest.raises(SystemExit) as finished:
        main.main()
    assert finished.value.code == 0
    assert trace.index('stop:radio') < trace.index('join:news')
    news.terminate.assert_not_called()
    radio.terminate.assert_not_called()
    web.terminate.assert_called_once()


@pytest.mark.skipif(not hasattr(main.signal, 'SIGUSR1'), reason='SIGUSR1 unavailable on this OS')
def test_parent_restart_drains_scheduler_and_replaces_config(monkeypatch):
    monkeypatch.setattr(main, 'parse_args', lambda: cli_args())
    monkeypatch.setattr(main, 'setup_logging', Mock())
    old, new = {'service': {'name': 'Old'}}, {'service': {'name': 'New'}}
    monkeypatch.setattr(main, 'load_config', Mock(side_effect=[old, new]))
    handlers = {}
    monkeypatch.setattr(main.signal, 'signal', lambda sig, fn: handlers.__setitem__(sig, fn))
    process = SimpleNamespace(is_alive=lambda: True, scheduler_stop_event=Mock(), join=Mock(), terminate=Mock())
    start = Mock(side_effect=[process, SimpleNamespace(is_alive=lambda: True)])
    monkeypatch.setattr(main, '_start_process', start)
    ticks = []
    def sleep(delay):
        if delay == 15:
            if ticks:
                raise EndProbe
            ticks.append(delay)
            handlers[main.signal.SIGUSR1](None, None)
            assert start.call_count == 1  # Signal handler does not mutate the process list.
    monkeypatch.setattr(main.time, 'sleep', sleep)
    with pytest.raises(EndProbe):
        main.main()
    process.scheduler_stop_event.set.assert_called_once()
    process.join.assert_called_once()
    process.terminate.assert_not_called()
    assert start.call_args.args == (main.run_news_engine, 'NewsEngine', new, False)


def test_single_configured_post_hour_runs_again_next_day(monkeypatch):
    started, _, _ = run_probe(monkeypatch, [datetime(2026, 11, 3, 6, 59),
        datetime(2026, 11, 3, 7, 0), datetime(2026, 11, 4, 7, 0)], post_hours=[7])
    assert sum(name.startswith('posts') for name in started) == 2


def test_reload_watches_configured_data_directory(monkeypatch, tmp_path):
    custom = tmp_path / 'custom-data'
    custom.mkdir()
    flag = custom / '.reload_config'
    flag.touch()
    old = {'paths': {'data_dir': str(custom)}, 'radio': {'post_hours': []}}
    new = {'paths': {'data_dir': str(custom)}, 'radio': {'post_hours': [7]}}
    loader = Mock(return_value=new)
    monkeypatch.setattr(main, 'load_config', loader)
    monkeypatch.setattr(llm_client, 'reload_ollama', Mock())
    started, _, _ = run_probe(monkeypatch, [datetime(2026, 11, 3, 7, 0)]*3, config=old)
    loader.assert_called_once_with('config/config.yaml')
    assert not flag.exists()
    assert sum(name.startswith('posts') for name in started) == 1
