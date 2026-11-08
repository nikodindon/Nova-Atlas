from contextlib import nullcontext
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import requests
from deploy.radio_diagnostic import observations,probe


def test_distinct_fresh_and_stale_observations():
    now=datetime(2026,11,8,12,tzinfo=timezone.utc)
    result=observations({'primary':{'observed_at':now.isoformat(),'source_starts':1,'bytes_local_pipe':4096}},now=now)
    assert result['primary']['ffmpeg_start_event']['status']=='pass'
    assert result['primary']['icecast_mount']['status']=='not_tested'
    assert result['primary']['audience_delivery']['status']=='not_tested'
    assert observations({'primary':{'observed_at':'2026-11-01T12:00:00+00:00','source_starts':1}},now=now)['primary']['ffmpeg_start_event']['status']=='warning'


def test_received_bytes_not_a_claim_of_decoding_or_listener_delivery():
    response=SimpleNamespace(status_code=200,headers={'Content-Type':'audio/mpeg'},iter_content=lambda **k:iter([b'synthetic']))
    session=SimpleNamespace(get=Mock(return_value=nullcontext(response)),trust_env=True)
    result=probe('http://icecast:8000/nova',session_factory=lambda:nullcontext(session))
    assert result['icecast_mount']['status']=='pass' and result['client_audio_bytes']['bytes']==9
    assert result['audio_decode']['status']==result['audience_delivery']['status']=='not_tested'
    assert session.trust_env is False and session.get.call_args.kwargs['allow_redirects'] is False


@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:credential@localhost/mount','http://icecast/nova?secret=value'])
def test_operator_probe_refuses_credentials_and_nonfixed_url(url):
    with pytest.raises(ValueError):probe(url)


def test_cli_probe_has_global_process_deadline_and_no_credential_environment(monkeypatch):
    import subprocess
    from deploy.radio_diagnostic import bounded_probe
    monkeypatch.setenv('OPENROUTER_API_KEY','synthetic-not-real')
    run=Mock(return_value=SimpleNamespace(returncode=0,stdout='{"status":"pass"}'))
    monkeypatch.setattr('deploy.radio_diagnostic.subprocess.run',run)
    assert bounded_probe('http://icecast:8000/nova')['status']=='pass'
    kwargs=run.call_args.kwargs;assert kwargs['timeout']==6 and 'OPENROUTER_API_KEY' not in kwargs['env']
    run.side_effect=subprocess.TimeoutExpired('synthetic-worker',6)
    assert bounded_probe('http://icecast:8000/nova')['status']=='fail'
