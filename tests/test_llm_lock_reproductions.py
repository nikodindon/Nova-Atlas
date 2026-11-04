"""Reproductions exercised on the legacy client before its lock was changed."""
import json
from pathlib import Path
import threading
from unittest.mock import Mock

from modules.core.llm_client import OllamaClient


def make_client(tmp_path):
    return OllamaClient({'llm': {'provider': 'ollama', 'cache': {'enabled': False}},
                         'paths': {'data_dir': str(tmp_path)}})


def test_lock_creation_failure_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr('time.sleep', Mock())
    client = make_client(tmp_path)
    client.lock_file.mkdir()
    assert client._acquire_lock('fetch', wait_max=0) is False


def test_non_owner_cannot_release_another_clients_lock(tmp_path):
    owner, other = make_client(tmp_path), make_client(tmp_path)
    assert owner._acquire_lock('fetch', wait_max=0)
    try:
        other._release_lock()
        assert owner.lock_file.exists()
    finally:
        owner._release_lock()


def test_priority_never_preempts_active_request(tmp_path, monkeypatch):
    owner, other = make_client(tmp_path), make_client(tmp_path)
    assert owner._acquire_lock('posts', wait_max=0)
    monkeypatch.setattr('time.sleep', Mock())
    try:
        assert other._acquire_lock('fetch', wait_max=0) is False
    finally:
        other._release_lock()
        owner._release_lock()


def test_old_timestamp_never_expires_an_active_owner(tmp_path):
    owner, other = make_client(tmp_path), make_client(tmp_path)
    assert owner._acquire_lock('fetch', wait_max=0)
    # Legacy diagnostic content cannot grant permission or revoke a live OS lock.
    owner.lock_file.write_text(json.dumps({'caller': 'posts', 'ts': 0}))
    try:
        assert other._acquire_lock('fetch', wait_max=0) is False
    finally:
        other._release_lock()
        owner._release_lock()


def test_failed_lock_never_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setattr('time.sleep', Mock())
    client = make_client(tmp_path)
    client.lock_file.mkdir()
    provider = Mock(return_value='must not generate')
    monkeypatch.setattr(client, '_call_ollama_cli', provider)
    assert client.call('synthetic prompt', timeout=1) == ''
    provider.assert_not_called()


def test_atomic_admission_when_two_existence_checks_race(tmp_path, monkeypatch):
    clients = [make_client(tmp_path), make_client(tmp_path)]
    start, checked = threading.Barrier(3), threading.Barrier(2)
    complete, release = threading.Event(), threading.Event()
    results, errors = [], []
    result_guard = threading.Lock()
    original_exists = Path.exists
    def simultaneous_absence(path):
        exists = original_exists(path)
        if path == clients[0].lock_file and not exists:
            checked.wait(timeout=2)
        return exists
    def attempt(client):
        owned = False
        try:
            start.wait(timeout=2)
            owned = client._acquire_lock('fetch', wait_max=0)
            with result_guard:
                results.append(owned)
                if len(results) == 2:
                    complete.set()
            assert release.wait(2)
        except BaseException as error:
            errors.append(error)
        finally:
            if owned:
                client._release_lock()
    threads = [threading.Thread(target=attempt, args=(client,)) for client in clients]
    try:
        with monkeypatch.context() as context:
            context.setattr(Path, 'exists', simultaneous_absence)
            for thread in threads:
                thread.start()
            start.wait(timeout=2)
            assert complete.wait(2)
            assert sorted(results) == [False, True]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=2)
    assert not errors
    assert all(not thread.is_alive() for thread in threads)
