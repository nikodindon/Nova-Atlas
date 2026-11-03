"""Local flock tests only: no model, remote API, RSS, TTS or production files."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.core import llm_lock
from modules.core.llm_lock import LocalLLMLock, LockUnavailable, resource_path
from modules.core.llm_client import OllamaClient
from modules.core.scheduler import DueTask, ThreadRunner

pytestmark = pytest.mark.skipif(llm_lock.fcntl is None, reason='Local exclusive backend requires flock')


def client_at(directory, provider='ollama', **llm):
    config = {'provider': provider, 'cache': {'enabled': False}, **llm}
    return OllamaClient({'llm': config, 'paths': {'data_dir': str(directory)}})


def _worker(path, ready, go, release, output, active, maximum):
    """Spawn/fork-safe target; operates on a temporary lock only."""
    client = client_at(Path(path).parent, lock_resource='process-shared')
    assert client.lock_file == Path(path)
    ready.put(os.getpid())
    if not go.wait(3):
        raise RuntimeError('test synchronization failed')
    def provider(*args):
        with active.get_lock():
            active.value += 1
            maximum.value = max(maximum.value, active.value)
        output.put(('entered', os.getpid()))
        if not release.wait(3):
            raise RuntimeError('test release timeout')
        with active.get_lock():
            active.value -= 1
        return 'synthetic process response'
    client._call_ollama_cli = provider
    if client.call('synthetic prompt', caller='fetch', timeout=1) != 'synthetic process response':
        raise RuntimeError('test generation failed')
    output.put(('released', os.getpid()))


def _holder(path, connection):
    lock = LocalLLMLock(path)
    if not lock.acquire('report', wait_max=0):
        raise RuntimeError('test holder failed')
    connection.send(True)
    connection.recv()  # Parent kills this dedicated test process while the OS lease is held.
    lock.release()


def _inherited_attempt(lock, connection):
    connection.send((lock.release(), lock.acquire('fetch', wait_max=0)))
    connection.close()


def stop_children(processes):
    for process in processes:
        process.join(timeout=4)
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join(timeout=2)


def test_success_owner_metadata_and_stable_inode(tmp_path):
    lock = LocalLLMLock(tmp_path / 'resource.lock')
    assert lock.acquire('fetch', wait_max=0)
    inode = lock.path.stat().st_ino
    info = lock.read_info()
    assert info['pid'] == os.getpid() and info['thread_id'] == threading.get_ident()
    assert info['state'] == 'held' and info['caller'] == 'fetch'
    assert len(info['owner_token']) == 32
    assert not os.get_inheritable(lock._state.lease.fd)
    assert lock.release()
    assert lock.path.exists() and lock.path.stat().st_ino == inode
    assert lock.read_info()['state'] == 'released'
    assert lock.acquire('posts', wait_max=0)
    assert lock.read_info()['owner_token'] != info['owner_token']
    assert lock.release()
    assert lock.path.stat().st_ino == inode


def test_two_processes_never_overlap(tmp_path):
    ctx = multiprocessing.get_context('fork')
    ready, output = ctx.Queue(), ctx.Queue()
    go, release = ctx.Event(), ctx.Event()
    active, maximum = ctx.Value('i', 0), ctx.Value('i', 0)
    path = str(resource_path(tmp_path, 'process-shared'))
    processes = [ctx.Process(target=_worker, args=(path, ready, go, release, output, active, maximum)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=3)
        go.set()
        assert output.get(timeout=3)[0] == 'entered'
        with pytest.raises(queue.Empty):
            output.get(timeout=.05)
        info = LocalLLMLock(path).read_info()
        assert info['state'] == 'held'
        for _ in range(50):
            assert LocalLLMLock(path).read_info() == info
        release.set()
        stop_children(processes)
        assert [process.exitcode for process in processes] == [0, 0]
        assert active.value == 0 and maximum.value == 1
    finally:
        release.set()
        stop_children(processes)
        for channel in (ready, output):
            channel.close()
            channel.join_thread()


def test_killed_holder_releases_os_lock_and_stale_metadata_does_not_block(tmp_path):
    ctx = multiprocessing.get_context('fork')
    parent, child = ctx.Pipe()
    path = tmp_path / 'crash.lock'
    process = ctx.Process(target=_holder, args=(str(path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(3) and parent.recv() is True
        contender = LocalLLMLock(path)
        assert not contender.acquire('fetch', wait_max=0)
        old = contender.read_info()
        assert old['pid'] == process.pid and old['state'] == 'held'
        process.kill()  # Only this test's own child is terminated.
        process.join(timeout=3)
        assert process.exitcode == -signal.SIGKILL
        assert contender.acquire('fetch', wait_max=1)
        assert contender.read_info()['pid'] == os.getpid()
        assert contender.read_info()['owner_token'] != old['owner_token']
        assert contender.release()
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=3)
        parent.close()


def test_fork_child_cannot_unlock_or_inherit_parent_lease(tmp_path):
    ctx = multiprocessing.get_context('fork')
    lock = LocalLLMLock(tmp_path / 'fork.lock')
    assert lock.acquire('report', wait_max=0)
    old = lock.read_info()
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_inherited_attempt, args=(lock, child))
    try:
        process.start()
        child.close()
        assert parent.poll(3)
        assert parent.recv() == (False, False)
        process.join(timeout=3)
        assert process.exitcode == 0
        assert lock.read_info() == old
        assert not LocalLLMLock(lock.path).acquire('fetch', wait_max=0)
    finally:
        lock.release()
        if process.is_alive():
            process.kill()
        process.join(timeout=3)
        parent.close()


def test_multiple_threads_of_same_client_are_exclusive(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    barrier, entered, release = threading.Barrier(6), threading.Event(), threading.Event()
    guard, active, counts, results = threading.Lock(), [0], [], []
    def provider(*args):
        with guard:
            active[0] += 1
            counts.append(active[0])
        entered.set()
        assert release.wait(3)
        with guard:
            active[0] -= 1
        return 'synthetic response'
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    def request():
        barrier.wait(timeout=3)
        results.append(client.call('synthetic prompt', timeout=1))
    threads = [threading.Thread(target=request) for _ in range(5)]
    try:
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        assert entered.wait(3)
        assert counts == [1]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert results == ['synthetic response']*5
    assert counts == [1]*5


def test_other_thread_cannot_release_same_clients_lease(tmp_path):
    client = client_at(tmp_path)
    assert client._acquire_lock('report', wait_max=0)
    info, results = client._read_lock_info(), []
    thread = threading.Thread(target=lambda: results.append(client._release_lock()))
    try:
        thread.start()
        thread.join(timeout=2)
        assert results == [False]
        assert client._read_lock_info() == info
        assert not client_at(tmp_path)._acquire_lock('fetch', wait_max=0)
    finally:
        client._release_lock()


def test_context_manager_releases_on_exception(tmp_path):
    lock = LocalLLMLock(tmp_path / 'context.lock')
    with pytest.raises(RuntimeError):
        with lock.hold('fetch', wait_max=0) as acquired:
            assert acquired
            raise RuntimeError('synthetic exception')
    other = LocalLLMLock(lock.path)
    with other.hold(wait_max=0) as acquired:
        assert acquired


@pytest.mark.parametrize('error', [RuntimeError('synthetic-secret'), subprocess.TimeoutExpired('synthetic', 1)])
def test_generation_failure_releases_lock(tmp_path, monkeypatch, error, caplog):
    client = client_at(tmp_path)
    monkeypatch.setattr(client, '_call_ollama_cli', Mock(side_effect=error))
    assert client.call('synthetic prompt', timeout=1) == ''
    assert 'synthetic-secret' not in caplog.text
    other = client_at(tmp_path)
    assert other._acquire_lock('posts', wait_max=0)
    assert other._release_lock()


def test_base_exception_also_releases_lease(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    monkeypatch.setattr(client, '_call_ollama_cli', Mock(side_effect=SystemExit(1)))
    with pytest.raises(SystemExit):
        client.call('synthetic prompt', timeout=1)
    other = client_at(tmp_path)
    assert other._acquire_lock('fetch', wait_max=0)
    other._release_lock()


def test_wait_timeout_is_monotonic_and_bounded(tmp_path):
    owner = LocalLLMLock(tmp_path / 'timeout.lock')
    assert owner.acquire('report', wait_max=0)
    now, sleeps = [0.], []
    def wait(delay):
        sleeps.append(delay)
        now[0] += delay
    other = LocalLLMLock(owner.path, clock=lambda: now[0], wait=wait)
    try:
        assert not other.acquire('fetch', wait_max=.25)
        assert now[0] == .25
        assert sum(sleeps) == .25 and max(sleeps) <= .1
    finally:
        owner.release()


def test_wait_cancellation_never_enters_provider(tmp_path, monkeypatch):
    owner, waiter = client_at(tmp_path), client_at(tmp_path)
    assert owner._acquire_lock('posts', wait_max=0)
    cancel, waiting = threading.Event(), threading.Event()
    provider = Mock()
    monkeypatch.setattr(waiter, '_call_ollama_cli', provider)
    original_wait = cancel.wait
    def wait(delay):
        waiting.set()
        return original_wait(delay)
    monkeypatch.setattr(cancel, 'wait', wait)
    results = []
    thread = threading.Thread(target=lambda: results.append(waiter.call('synthetic', timeout=1, cancel_event=cancel)))
    try:
        thread.start()
        assert waiting.wait(2)
        cancel.set()
        thread.join(timeout=2)
        assert results == [''] and not thread.is_alive()
        provider.assert_not_called()
        assert owner._read_lock_info()['state'] == 'held'
    finally:
        cancel.set()
        owner._release_lock()
        thread.join(timeout=2)


def test_cancellation_does_not_interrupt_active_generation(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    cancel = threading.Event()
    def provider(*args):
        cancel.set()
        return 'completed response'
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic', timeout=1, cancel_event=cancel) == 'completed response'
    other = client_at(tmp_path)
    assert other._acquire_lock('posts', wait_max=0)
    other._release_lock()


def test_metadata_creation_error_is_fail_closed(tmp_path, monkeypatch):
    lock = LocalLLMLock(tmp_path / 'metadata.lock')
    monkeypatch.setattr(lock, '_write_metadata', Mock(side_effect=OSError('synthetic-secret')))
    assert not lock.acquire(wait_max=0)
    other = LocalLLMLock(lock.path)
    assert other.acquire(wait_max=0)
    other.release()


def test_metadata_release_error_still_releases_os_lease(tmp_path, monkeypatch):
    lock = LocalLLMLock(tmp_path / 'metadata.lock')
    assert lock.acquire(wait_max=0)
    monkeypatch.setattr(lock, '_write_metadata', Mock(side_effect=OSError('synthetic-secret')))
    assert lock.release()
    other = LocalLLMLock(lock.path)
    assert other.acquire(wait_max=0)
    other.release()


def test_corrupt_metadata_never_grants_or_prevents_ownership(tmp_path):
    lock = LocalLLMLock(tmp_path / 'metadata.lock')
    assert lock.acquire('posts', wait_max=0)
    lock.metadata_path.write_text('{bad json')
    assert lock.read_info() == {}
    other = LocalLLMLock(lock.path)
    assert not other.acquire('fetch', wait_max=0)
    lock.release()
    assert other.acquire('fetch', wait_max=0)
    assert other.read_info()['state'] == 'held'
    other.release()


def test_atomic_metadata_readers_never_see_partial_json(tmp_path):
    lock = LocalLLMLock(tmp_path / 'metadata.lock')
    assert lock.acquire(wait_max=0)
    lock.release()
    errors = []
    def writer():
        try:
            for _ in range(15):
                assert lock.acquire(wait_max=1)
                lock.release()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=writer)
    thread.start()
    for _ in range(100):
        info = json.loads(lock.metadata_path.read_text())
        assert info['version'] == 1 and info['state'] in ('held', 'released')
        assert len(info['owner_token']) == 32
    thread.join(timeout=3)
    assert not thread.is_alive() and not errors
    assert not list(tmp_path.glob('*.tmp'))


def test_symlinks_and_hardlinks_are_refused(tmp_path):
    target = tmp_path / 'synthetic-target'
    target.write_text('untouched')
    symbolic = tmp_path / 'symbolic.lock'
    symbolic.symlink_to(target)
    assert not LocalLLMLock(symbolic).acquire(wait_max=0)
    hard = tmp_path / 'hard.lock'
    os.link(target, hard)
    assert not LocalLLMLock(hard).acquire(wait_max=0)
    assert target.read_text() == 'untouched'


def test_flock_failure_never_calls_provider(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    provider = Mock()
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    monkeypatch.setattr(llm_lock.fcntl, 'flock', Mock(side_effect=OSError(errno.EIO, 'synthetic-secret')))
    assert client.call('synthetic prompt', timeout=1) == ''
    provider.assert_not_called()


def test_resource_scoping_and_unsupported_cloud_adapter(tmp_path, monkeypatch):
    first = client_at(tmp_path / 'one', lock_dir=str(tmp_path), lock_resource='gpu-a')
    same = client_at(tmp_path / 'two', lock_dir=str(tmp_path), lock_resource='gpu-a')
    other = client_at(tmp_path / 'three', lock_dir=str(tmp_path), lock_resource='gpu-b')
    assert first._acquire_lock('report', wait_max=0)
    try:
        assert not same._acquire_lock('fetch', wait_max=0)
        assert other._acquire_lock('fetch', wait_max=0)
        other._release_lock()
        unsupported = client_at(tmp_path / 'future', provider='openrouter')
        cli = Mock()
        monkeypatch.setattr(unsupported, '_call_ollama_cli', cli)
        assert unsupported._resource_lock is None
        assert unsupported.call('synthetic') == ''
        cli.assert_not_called()
    finally:
        first._release_lock()


def test_cache_rechecked_after_wait_avoids_duplicate_generation(tmp_path, monkeypatch):
    client = client_at(tmp_path, cache={'enabled': True})
    start = threading.Barrier(2)
    state = threading.local()
    original_get = client._cache.get
    def get(key):
        if not getattr(state, 'initial_read', False):
            state.initial_read = True
            initial = original_get(key)
            start.wait(timeout=3)
            return initial
        return original_get(key)
    monkeypatch.setattr(client._cache, 'get', get)
    provider = Mock(return_value='cached synthetic response')
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    results = []
    threads = [threading.Thread(target=lambda: results.append(client.call('synthetic', cache_key='shared-key'))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert results == ['cached synthetic response']*2
    provider.assert_called_once()


def test_cache_hit_does_not_wait_for_local_resource(tmp_path, monkeypatch):
    owner = client_at(tmp_path)
    cached = client_at(tmp_path, cache={'enabled': True})
    cached._cache.put('known-key', 'cached value')
    assert owner._acquire_lock('report', wait_max=0)
    try:
        monkeypatch.setattr(cached, '_acquire_lock', Mock(side_effect=AssertionError('Cache must not lock')))
        assert cached.call('synthetic', cache_key='known-key') == 'cached value'
    finally:
        owner._release_lock()


def test_cli_dispatch_runs_under_lease_and_releases(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    def run(command, **kwargs):
        assert client._read_lock_info()['state'] == 'held'
        assert not client_at(tmp_path)._acquire_lock('fetch', wait_max=0)
        assert command == ['ollama', 'run', client.model]
        assert kwargs['input'] == 'synthetic prompt'
        return SimpleNamespace(stdout='\x1b[32mSynthetic CLI response\x1b[0m')
    monkeypatch.setattr('subprocess.run', run)
    assert client.call('synthetic prompt', timeout=1) == 'Synthetic CLI response'
    assert client._read_lock_info()['state'] == 'released'


def test_http_dispatch_runs_under_lease_and_preserves_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(OllamaClient, '_detect_model_from_server', Mock(return_value=None))
    client = client_at(tmp_path, provider='llama-server', base_url='http://synthetic-llm:8080')
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({'choices': [{'message': {'content': 'Synthetic HTTP response'}}]}).encode()
    def urlopen(request, **kwargs):
        assert client._read_lock_info()['state'] == 'held'
        assert not client_at(tmp_path)._acquire_lock('fetch', wait_max=0)
        assert json.loads(request.data)['messages'][0]['content'] == 'synthetic prompt'
        return response
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    assert client.call('synthetic prompt', timeout=1) == 'Synthetic HTTP response'
    assert client._read_lock_info()['state'] == 'released'


def test_lock_and_scheduler_families_share_exclusive_resource(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    guard, active, counts = threading.Lock(), [0], []
    def provider(*args):
        with guard:
            active[0] += 1
            counts.append(active[0])
        entered.set()
        assert release.wait(3)
        with guard:
            active[0] -= 1
        return 'synthetic'
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    runner = ThreadRunner()
    try:
        assert runner.submit(DueTask('fetch', 'one'), lambda: client.call('synthetic', caller='fetch'))
        assert entered.wait(3)
        assert runner.submit(DueTask('report', 'one'), lambda: client.call('synthetic', caller='report'))
        assert counts == [1]
    finally:
        release.set()
        runner.close()
    assert counts == [1, 1]
    assert runner.active_count == 0


@pytest.mark.parametrize('budget', [-1, float('inf'), float('nan'), 'invalid'])
def test_invalid_wait_budgets_never_acquire(tmp_path, budget):
    lock = LocalLLMLock(tmp_path / 'invalid.lock')
    assert not lock.acquire(wait_max=budget)
    assert not lock.path.exists()


def test_acquire_cannot_succeed_after_budget_elapsed(tmp_path, monkeypatch):
    clock = [0.]
    lock = LocalLLMLock(tmp_path / 'late.lock', clock=lambda: clock[0])
    original = lock._write_metadata
    def metadata(*args):
        original(*args)
        clock[0] = 2.
    monkeypatch.setattr(lock, '_write_metadata', metadata)
    assert not lock.acquire(wait_max=1)
    other = LocalLLMLock(lock.path)
    assert other.acquire(wait_max=0)
    other.release()


def test_recursive_acquisition_cannot_release_outer_call(tmp_path):
    lock = LocalLLMLock(tmp_path / 'nested.lock')
    assert lock.acquire('fetch', wait_max=0)
    try:
        with pytest.raises(LockUnavailable):
            with lock.hold('report', wait_max=0):
                pytest.fail('Failed nested admission must not enter the section')
        assert not LocalLLMLock(lock.path).acquire('posts', wait_max=0)
    finally:
        lock.release()


def test_cancelled_call_never_creates_lease_or_generates(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    cancel, provider = threading.Event(), Mock()
    cancel.set()
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic', cancel_event=cancel) == ''
    provider.assert_not_called()
    assert not client.lock_file.exists()


def test_lock_logs_do_not_record_unknown_caller_or_prompt(tmp_path, monkeypatch, caplog):
    caplog.set_level('INFO')
    client = client_at(tmp_path)
    monkeypatch.setattr(client, '_call_ollama_cli', Mock(return_value='synthetic response'))
    assert client.call('synthetic-private-prompt', caller='synthetic-private-caller') == 'synthetic response'
    assert 'synthetic-private-prompt' not in caplog.text
    assert 'synthetic-private-caller' not in caplog.text
    assert client._read_lock_info()['caller'] == 'atlas'


def test_http_logs_never_reflect_credentials_or_error_body(tmp_path, monkeypatch, caplog):
    import urllib.error
    caplog.set_level('INFO')
    monkeypatch.setattr(OllamaClient, '_detect_model_from_server', Mock(return_value=None))
    client = client_at(tmp_path, provider='llama-server', base_url='http://u:synthetic-private-credential@llm.invalid')
    error = urllib.error.HTTPError('http://llm.invalid', 401, 'synthetic-private-reason', {}, None)
    error.read = Mock(return_value=b'{"error":"synthetic-private-body"}')
    monkeypatch.setattr('urllib.request.urlopen', Mock(side_effect=error))
    assert client.call('synthetic-private-prompt', timeout=1) == ''
    for secret in ('synthetic-private-credential', 'synthetic-private-reason', 'synthetic-private-body', 'synthetic-private-prompt'):
        assert secret not in caplog.text


def test_cache_exception_after_acquisition_does_not_leak_lease(tmp_path, monkeypatch):
    client = client_at(tmp_path, cache={'enabled': True})
    monkeypatch.setattr(client._cache, 'get', Mock(side_effect=[None, OSError('synthetic cache error')]))
    provider = Mock()
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic', cache_key='key') == ''
    provider.assert_not_called()
    other = client_at(tmp_path)
    assert other._acquire_lock('posts', wait_max=0)
    other._release_lock()


def test_missing_flock_backend_fails_closed(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    monkeypatch.setattr(llm_lock, 'fcntl', None)
    provider = Mock()
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic') == ''
    provider.assert_not_called()


def test_failed_context_admission_never_enters_section(tmp_path):
    owner = LocalLLMLock(tmp_path / 'context.lock')
    other = LocalLLMLock(owner.path)
    assert owner.acquire(wait_max=0)
    try:
        with pytest.raises(LockUnavailable):
            with other.hold(wait_max=0):
                pytest.fail('Unavailable resource must never enter this section')
    finally:
        owner.release()


@pytest.mark.parametrize('timeout', [-1, 0, float('inf'), float('nan'), '5', True])
def test_invalid_generation_budget_never_starts_provider(tmp_path, monkeypatch, timeout):
    client = client_at(tmp_path)
    provider = Mock()
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic prompt', timeout=timeout) == ''
    provider.assert_not_called()
    assert not client.lock_file.exists()
