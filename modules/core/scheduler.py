"""In-process scheduling: civil-time slots, bounded admission history and cooperative stop.

No persistence/replay policy, distributed queue or external service dependency.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import logging
import threading

FETCH_HOURS = list(range(24))
REPORT_HOUR, REPORT_MIN = 23, 0
BUILD_HOUR, BUILD_MIN = 23, 30
EDITION_SCHEDULE = [(6, 'matin'), (12, 'midi'), (19, 'soir')]
DEFAULT_POST_HOURS = [7, 9, 11, 13, 15, 17, 19, 21]


@dataclass(frozen=True)
class DueTask:
    group: str
    slot: str
    args: tuple = ()


def validate_hours(hours):
    if not isinstance(hours, (list, tuple)) or any(type(h) is not int or not 0 <= h <= 23 for h in hours):
        raise ValueError('Invalid scheduler hours')
    return tuple(sorted(set(hours)))


def civil_slot(now, minute):
    # One occurrence per civil slot during a repeated DST hour, deliberately ignoring fold/offset.
    return now.replace(minute=minute, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M')


def initial_fetch(now):
    return DueTask('fetch', civil_slot(now, (now.minute // 30) * 30))


def news_tasks(now, post_hours):
    """Historical windows: fetch 2 min, report/build/editions 5 min, posts 3 min."""
    day = now.strftime('%Y%m%d')
    tasks = []
    if now.hour in FETCH_HOURS and now.minute % 30 < 2:
        tasks.append(initial_fetch(now))
    if now.hour == REPORT_HOUR and REPORT_MIN <= now.minute < REPORT_MIN + 5:
        tasks.append(DueTask('report', civil_slot(now, REPORT_MIN), (day,)))
    if now.hour == BUILD_HOUR and BUILD_MIN <= now.minute < BUILD_MIN + 5:
        tasks.append(DueTask('rebuild', civil_slot(now, BUILD_MIN)))
    if now.hour in post_hours and now.minute < 3:
        tasks.append(DueTask('posts', civil_slot(now, 0), (day,)))
    for hour, name in EDITION_SCHEDULE:
        if now.hour == hour and now.minute < 5:
            tasks.append(DueTask('edition', civil_slot(now, 0), (name, day)))
    return tasks


def radio_tasks(now, post_hours):
    if not (25 <= now.minute < 27 or 55 <= now.minute < 57):
        return []
    # Advance on the real timeline, so :55 before a DST boundary targets an existing hour.
    seconds = (30 - now.minute % 30) * 60 - now.second - now.microsecond / 1_000_000
    target = datetime.fromtimestamp(now.timestamp() + seconds, tz=now.tzinfo)
    if target.hour not in post_hours:
        return []
    return [DueTask('bulletin', civil_slot(target, target.minute))]


def wait_seconds(now):
    """At most one idle check/minute, aligned to the next minute and interruptible."""
    return max(0.05, 60 - now.second - now.microsecond / 1_000_000)


class ThreadRunner:
    def __init__(self, *, log=None, thread_factory=None, history_limit=1024):
        if history_limit < 1:
            raise ValueError('Invalid slot history limit')
        self.log = log or logging.getLogger('nova.scheduler')
        self._thread_factory = thread_factory or threading.Thread
        self._lock = threading.RLock()
        self._active = {}
        self._seen = OrderedDict()
        self._history_limit = history_limit
        self._accepting = True

    @property
    def active_count(self):
        with self._lock:
            self._reap()
            return len(self._active)

    @property
    def history_count(self):
        with self._lock:
            return len(self._seen)

    def _reap(self):
        for group, thread in list(self._active.items()):
            if not thread.is_alive():
                del self._active[group]

    def submit(self, task, function):
        with self._lock:
            if not self._accepting:
                return False
            self._reap()
            identity = (task.group, task.slot)
            if identity in self._seen:
                return False
            self._seen[identity] = None
            if len(self._seen) > self._history_limit:
                self._seen.popitem(last=False)
            if task.group in self._active:
                self.log.warning('[SKIP] %s slot=%s : précédente exécution active', task.group, task.slot)
                return False  # An observed busy slot is consumed; no delayed replay.

            def execute():
                try:
                    function(*task.args)
                except Exception:
                    self.log.exception('Échec de tâche %s slot=%s', task.group, task.slot)
                finally:
                    with self._lock:
                        if self._active.get(task.group) is thread:
                            del self._active[task.group]

            thread = self._thread_factory(target=execute, daemon=False, name=f'{task.group}:{task.slot}')
            self._active[task.group] = thread
            try:
                # Registry observation, reservation and start share one lock.
                thread.start()
            except Exception:
                self._active.pop(task.group, None)
                self.log.exception('Démarrage impossible %s slot=%s', task.group, task.slot)
                return False
            self.log.info('[SCHEDULE] %s slot=%s', task.group, task.slot)
            return True

    def join_group(self, group):
        with self._lock:
            thread = self._active.get(group)
        if thread is not None:
            thread.join()
        with self._lock:
            self._reap()

    def close(self):
        with self._lock:
            self._accepting = False
            threads = list(self._active.values())
        # No lock held while joining: worker cleanup acquires it.
        for thread in threads:
            thread.join()
        with self._lock:
            self._reap()


def run_loop(plan, jobs, stop_event, *, runner=None, clock=None, wait=None,
             before_tick=None, startup=None):
    runner = runner or ThreadRunner()
    clock = clock or datetime.now
    wait = wait or stop_event.wait
    try:
        if startup and not stop_event.is_set():
            runner.submit(initial_fetch(clock()), startup)
            # Keep historical startup ordering: editions/posts must see the initial fetch.
            runner.join_group('fetch')
        while not stop_event.is_set():
            now = clock()
            if before_tick:
                before_tick(now)
            if stop_event.is_set():
                break
            for task in plan(now):
                if stop_event.is_set():
                    break
                runner.submit(task, jobs[task.group])
            if wait(wait_seconds(now)):
                break
    finally:
        runner.close()
