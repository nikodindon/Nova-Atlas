"""Shared-mode safety contracts only; no provider or live instance is contacted."""
from datetime import datetime
import fcntl
import json
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
import pytest
from deploy import editorial_prompt_trial as trial
from deploy import editorial_shared_trial as shared


def test_shared_mode_requires_distinct_explicit_honest_authorization(tmp_path):
    p=tmp_path/'a.json';p.write_text(json.dumps({'schema':1,'mode':'shared','approved':False}))
    with pytest.raises(ValueError):shared.authorize(p)
    old=json.loads((trial.CORPUS.parent/'isolated-ornith-authorization.example.json').read_text());old['endpoint']=shared.SHARED_ENDPOINT
    p.write_text(json.dumps(old))
    with pytest.raises(ValueError):trial.authorization(p)


@pytest.mark.parametrize('hour,minute,protected',[(22,30,False),(6,30,False),(6,40,True),(6,55,True),(7,0,True),(7,15,True),(21,45,True),(22,0,False),(23,30,False)])
def test_shared_calendar_reserves_radio_before_and_after_slots(hour,minute,protected):
    now=datetime(2026,11,10,hour,minute,tzinfo=ZoneInfo('Europe/Paris'))
    assert shared.protected_radio_window(now,list(range(7,22)),240,900) is protected


@pytest.mark.parametrize('fold',[0,1])
def test_repeated_civil_hour_both_folds_are_protected(fold):
    now=datetime(2026,10,25,2,0,fold=fold,tzinfo=ZoneInfo('Europe/Paris'))
    assert shared.protected_radio_window(now,[2],240,900)


def test_shared_lock_uses_existing_inode_without_data_or_metadata_writes(tmp_path):
    path=tmp_path/'ollama.lock';path.write_bytes(b'');path.chmod(0o600)
    meta=tmp_path/'ollama.lock.meta';meta.write_text('unchanged')
    before=path.stat()
    with shared.shared_lease(path):
        fd=path.open('rb')
        with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fd.close()
    with shared.shared_lease(path):pass
    assert (path.stat().st_ino,path.stat().st_mtime_ns)==(before.st_ino,before.st_mtime_ns)
    assert meta.read_text()=='unchanged'


def test_shared_lock_never_creates_a_missing_production_file(tmp_path):
    p=tmp_path/'missing.lock'
    with pytest.raises(FileNotFoundError):
        with shared.shared_lease(p):pass
    assert not p.exists()


def test_shared_lock_rejects_symlinks_and_releases_on_failure(tmp_path):
    target=tmp_path/'real';target.write_text('');link=tmp_path/'linked';link.symlink_to(target)
    with pytest.raises(shared.Deferred):
        with shared.shared_lease(link):pass
    with pytest.raises(RuntimeError):
        with shared.shared_lease(target):raise RuntimeError()
    with shared.shared_lease(target):pass


def test_props_hash_is_order_independent_and_ignores_prompt_content():
    a={'model_alias':'model','total_slots':4,'build_info':'fixture','default_generation_settings':{'n_ctx':16384,'params':{'temperature':0.6,'generation_prompt':'private','top_k':20}}}
    b={'build_info':'fixture','total_slots':4,'model_alias':'model','default_generation_settings':{'n_ctx':16384,'params':{'top_k':20,'generation_prompt':'different private','temperature':0.6}}}
    assert shared.props_digest(a)==shared.props_digest(b)
    assert 'generation_prompt' not in shared.props_identity(a)['params']


def test_result_assessment_keeps_id_and_never_claims_automatic_quality():
    job={'case_id':'test','version':'editorial-v2.1','repetition':1,'prompt_sha256':'a'*64,'expected_status':'ok'}
    receipt={'request_id':'b'*32,'kind':'success','http_status':200}
    row=trial.assess_output(job,'{"status":"ok","summary":"Le conseil vote."}',receipt)
    assert row['request_id']=='b'*32 and row['human_review']['invented_information'] is None


def test_shared_client_does_not_use_private_uncoordinated_lock(tmp_path):
    client=shared.SharedClient.__new__(shared.SharedClient)
    path=tmp_path/'data';path.mkdir();(path/'ollama.lock').write_text('')
    client.authorization={'state_root':str(tmp_path)};client.guard=Mock();client.guard.check.side_effect=shared.Deferred('radio_job_active')
    assert client._acquire_lock() is False
    assert client.interruption=='radio_job_active'
    with shared.shared_lease(path/'ollama.lock'):pass


def test_news_restart_defers_before_any_model_call(tmp_path,monkeypatch):
    cfg=tmp_path/'config';cfg.mkdir();file=cfg/'bulletins.yaml';file.write_text('post_hours: [7, 21]\n')
    a={'state_root':str(tmp_path),'radio_calendar_sha256':shared.sha(file.read_bytes()),'max_call_seconds':240,'radio_guard_seconds':900}
    monkeypatch.setattr(shared,'protected_radio_window',lambda *a:False)
    monkeypatch.setattr(shared,'states',lambda:{'news':{'Running':True}})
    with pytest.raises(shared.Deferred,match='news_running'):shared.Guard(a).check()


def test_radio_imminence_defers_without_docker_or_generation(tmp_path,monkeypatch):
    cfg=tmp_path/'config';cfg.mkdir();file=cfg/'bulletins.yaml';file.write_text('post_hours: [7]\n')
    a={'state_root':str(tmp_path),'radio_calendar_sha256':shared.sha(file.read_bytes()),'max_call_seconds':240,'radio_guard_seconds':900}
    monkeypatch.setattr(shared,'protected_radio_window',lambda *a:True)
    probe=Mock();monkeypatch.setattr(shared,'states',probe)
    with pytest.raises(shared.Deferred,match='radio_window_reserved'):shared.Guard(a).check()
    probe.assert_not_called()


def test_watchdog_interrupts_its_socket_for_radio_without_service_control(monkeypatch):
    import threading
    client=shared.SharedClient.__new__(shared.SharedClient);client.intent=Mock()
    client.guard=Mock()
    def check(*,during=False):
        if during:raise shared.Deferred('radio_worker_busy')
    client.guard.check.side_effect=check
    stopped=threading.Event()
    conn=Mock();conn.sock=Mock();conn.sock.shutdown.side_effect=lambda *a:stopped.set()
    def wait_response():
        assert stopped.wait(5)
        raise ConnectionError('cancelled fixture request')
    conn.getresponse.side_effect=wait_response
    monkeypatch.setattr(shared.http.client,'HTTPConnection',Mock(return_value=conn))
    request=shared.urllib.request.Request(shared.SHARED_ENDPOINT+'/v1/chat/completions',data=b'{}')
    with pytest.raises(ConnectionError):client._open_http(request,timeout=10)
    assert client.request_sent is True and client.interruption=='radio_worker_busy'
    conn.sock.shutdown.assert_called_once();conn.close.assert_called_once();client.intent.assert_called_once()
    assert not any(t.name=='shared-trial-watch' and t.is_alive() for t in threading.enumerate())


def shared_authorization(tmp_path):
    a={'schema':1,'mode':'shared','approved':True,'isolated_from_atlas_lab':False,'endpoint':shared.SHARED_ENDPOINT,
       'family':'Ornith-1.5-35B','model':str(tmp_path/'fixture.gguf'),'quantization':'IQ2_M','weights_sha256':'1'*64,
       'runtime_version':'fixture','hardware':'synthetic-not-real-hardware','reasoning_mode':'off',
       'server_parameters':{'context_size':16384,'threads':10,'gpu_layers':999},'max_total_seconds':3600,'max_call_seconds':240,
       'radio_guard_seconds':900,'cooldown_seconds':2,'min_available_memory_bytes':2*1024**3,'state_root':str(tmp_path/'state'),
       'output_root':str(tmp_path/'results'),'radio_calendar_sha256':'2'*64,'service_started_at':{s:'fixture' for s in shared.SERVICES},
       'radio_worker_pid':1,'radio_idle_threads':5,'server_pid':2,'server_start_ticks':1,'server_props_sha256':'3'*64,
       'weights_stat':{'inode':1,'bytes':100,'mtime_ns':1}}
    path=tmp_path/'authorization.json';path.write_text(json.dumps(a));return path,a


class FixtureSharedClient:
    calls=[]
    def __init__(self,*args):self.request_sent=False;self.interruption=None;self.server_timings={};self.provider_response_id=None;self.exact_output='{"status":"ok","summary":"Fait fictif."}'
    def call(self,prompt,**kwargs):
        self.intent();self.request_sent=True;self.calls.append(kwargs['request_id'])
        kwargs['metrics'].update(request_id=kwargs['request_id'],kind='success',http_status=200,finish_reason='stop')
        return self.exact_output


def test_shared_resume_keeps_completed_calls_and_never_repeats_them(tmp_path,monkeypatch):
    trial.prepare(tmp_path/'plan');auth,a=shared_authorization(tmp_path);FixtureSharedClient.calls=[]
    guard=Mock();guard.check.side_effect=[None,None,None,shared.Deferred('radio_window_reserved')]
    monkeypatch.setattr(shared,'Guard',lambda *a:guard);monkeypatch.setattr(shared,'SharedClient',FixtureSharedClient)
    monkeypatch.setattr(shared.time,'sleep',lambda *a:None)
    first=shared.measure_shared(tmp_path/'plan/plan.json',auth,Path(a['output_root']),execute=True)
    assert first['runs']==3 and first['complete'] is False
    first_ids=list(FixtureSharedClient.calls);guard.check.side_effect=None
    result=shared.measure_shared(tmp_path/'plan/plan.json',auth,Path(a['output_root']),execute=True,resume=True)
    assert result['runs']==39 and len(FixtureSharedClient.calls)==39
    assert FixtureSharedClient.calls[:3]==first_ids
    report=json.loads((Path(result['report_path'])/'measurements.json').read_text())
    assert report['environment_mode']=='shared' and report['model_environment']['isolated_from_atlas_lab'] is False
    assert report['truth_scores'] is None


def test_unknown_interrupted_request_is_not_silently_reissued(tmp_path,monkeypatch):
    trial.prepare(tmp_path/'plan');auth,a=shared_authorization(tmp_path);FixtureSharedClient.calls=[]
    out=Path(a['output_root']);out.mkdir(mode=0o700)
    plan=tmp_path/'plan/plan.json';raw=plan.read_bytes();job=json.loads(raw)['jobs'][0];identity='f'*32
    shared.atomic(out/'checkpoint.json',{'schema':1,'plan_sha256':shared.sha(raw),'authorization_sha256':shared.sha(auth.read_bytes()),'started_at':shared.time.time(),
                  'attempts':{shared.job_key(job):{'state':'in_flight','request_id':identity}},'deferrals':[]})
    guard=Mock();monkeypatch.setattr(shared,'Guard',lambda *a:guard);monkeypatch.setattr(shared,'SharedClient',FixtureSharedClient);monkeypatch.setattr(shared.time,'sleep',lambda *a:None)
    result=shared.measure_shared(plan,auth,out,execute=True,resume=True)
    assert result['runs']==39 and len(FixtureSharedClient.calls)==38
    checkpoint=json.loads((out/'checkpoint.json').read_text())
    assert checkpoint['attempts'][shared.job_key(job)]['result']['status']=='interrupted_outcome_unknown'
    assert checkpoint['attempts'][shared.job_key(job)]['result']['request_id']==identity


def test_shared_authorization_cannot_claim_isolation(tmp_path):
    p,a=shared_authorization(tmp_path);a['isolated_from_atlas_lab']=True;p.write_text(json.dumps(a))
    with pytest.raises(ValueError,match='explicit_shared_authorization_required'):shared.authorize(p)


def test_exact_final_content_and_timings_are_kept_without_reasoning():
    from types import SimpleNamespace
    text='\n{"status":"ok","summary":"Le conseil vote."}\n'
    body={'id':'chatcmpl-fixture','choices':[{'message':{'content':text,'reasoning_content':'private hidden reasoning'}}],
          'timings':{'predicted_n':12,'predicted_ms':300.0,'private_field':'discard'}}
    response=Mock(status=200);response.read.return_value=json.dumps(body).encode()
    client=SimpleNamespace(server_timings={},provider_response_id=None,exact_output=None)
    wrapped=shared.WatchedResponse(response,Mock(),Mock(),Mock(),client)
    wrapped.read(1024)
    assert client.exact_output==text and client.provider_response_id=='chatcmpl-fixture'
    assert client.server_timings=={'predicted_n':12,'predicted_ms':300.0}
    assert 'reasoning' not in repr(client.__dict__)


def test_pause_requires_explicit_true_authorization(tmp_path):
    p,a=shared_authorization(tmp_path)
    for bad in (False,1,'yes',None):
        a['radio_paused']=bad;p.write_text(json.dumps(a))
        with pytest.raises(ValueError,match='explicit_radio_pause_required'):shared.authorize(p)
    a['radio_paused']=True;p.write_text(json.dumps(a))
    assert shared.authorize(p)['radio_paused'] is True


def test_explicit_pause_checks_stopped_radio_instead_of_schedule(tmp_path,monkeypatch):
    cfg=tmp_path/'config';cfg.mkdir();p=cfg/'bulletins.yaml';p.write_text('post_hours: [7]\n')
    a={'state_root':str(tmp_path),'radio_calendar_sha256':shared.sha(p.read_bytes()),'radio_paused':True}
    calendar=Mock(side_effect=AssertionError('paused schedule must not deny admission'))
    monkeypatch.setattr(shared,'protected_radio_window',calendar)
    monkeypatch.setattr(shared,'states',lambda:{'news':{'Running':False},'radio':{'Running':True}})
    with pytest.raises(shared.Deferred,match='paused_radio_restarted'):shared.Guard(a).check()
    calendar.assert_not_called()


def test_pause_does_not_relax_news_guard(tmp_path,monkeypatch):
    cfg=tmp_path/'config';cfg.mkdir();p=cfg/'bulletins.yaml';p.write_text('post_hours: [7]\n')
    a={'state_root':str(tmp_path),'radio_calendar_sha256':shared.sha(p.read_bytes()),'radio_paused':True}
    monkeypatch.setattr(shared,'states',lambda:{'news':{'Running':True},'radio':{'Running':False}})
    with pytest.raises(shared.Deferred,match='news_running'):shared.Guard(a).check()
