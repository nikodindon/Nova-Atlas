import copy,json,os,threading
from datetime import datetime,timezone
import pytest
from deploy.qualification import fresh,validate,assess,write_new,read,GATES
from deploy.field_metrics import run,process_sample,sample
from deploy.field_radio import endpoint,collector,sample as radio,bounded,decode_sample

S='1'*40;A='2'*40

def proof(kind='ci',commit=S):return dict(commit=commit,environment='isolated synthetic fixture',kind=kind,protocol='offline regression only',date='2026-11-09T00:00:00+00:00',result='PASS',limits='not a physical test',artifact='private:fixture-report.json',sha256='3'*64)

def test_default_no_go_no_promotion():
    value=fresh(S,A);result=assess(value)
    assert result['public']=='NO_GO' and set(result['unresolved'])==set(GATES) and not result['deployment_authorized']
    assert all(r['status']=='NOT_TESTED' for r in value['gates'].values())

@pytest.mark.parametrize('gate',['installation_a','installation_b','android_device_1','android_device_2','android_device_3','licenses','secrets','public_distribution','android_emulator','radio_24h'])
def test_fixture_cannot_become_physical_or_operator_pass(gate):
    value=fresh(S,A);value['gates'][gate]={'status':'PASS','evidence':[proof('fixture',A if gate.startswith('android_') else S)]}
    with pytest.raises(ValueError):validate(value)

def test_stale_or_empty_pass_refused():
    value=fresh(S,A);value['gates']['code_ci']['status']='PASS'
    with pytest.raises(ValueError):validate(value)
    value['gates']['code_ci']['evidence']=[proof(commit='4'*40)]
    with pytest.raises(ValueError):validate(value)

def test_typed_evidence_and_no_automatic_distribution():
    value=fresh(S,A)
    for gate in GATES:
        kind='physical' if gate in ('installation_a','installation_b') or gate.startswith('android_device') else 'emulator' if gate=='android_emulator' else 'operator'
        value['gates'][gate]={'status':'PASS','evidence':[proof(kind,A if gate.startswith('android_') else S)]}
    result=assess(value);assert result['public']=='READY_FOR_OPERATOR_REVIEW' and not result['deployment_authorized']
    value['gates']['restore']={'status':'FAIL','evidence':[]};assert assess(value)['private']=='BLOCKED_BY_RECORDED_FAILURE'

def test_report_new_only_private_and_symlink_refused(tmp_path):
    path=tmp_path/'report.json';value=fresh(S,A);sha=write_new(path,value);assert read(path)==(value,sha);assert path.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):write_new(path,value)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(ValueError):read(link)
    os.link(path,tmp_path/'hard')
    with pytest.raises(ValueError):read(path)

@pytest.mark.parametrize('duration,authorize,reserved',[(86400,False,False),(61,True,False),(61,False,True),(-1,True,True),(86401,True,True)])
def test_long_runs_never_implicitly_start(tmp_path,duration,authorize,reserved):
    path=tmp_path/'metrics'
    with pytest.raises(ValueError):run(lambda:pytest.fail('collector started'),path,duration=duration,long_authorized=authorize,resources_reserved=reserved)
    assert not path.exists()

class Stop:
    def __init__(self):self.now=0;self.stopped=False
    def is_set(self):return self.stopped
    def wait(self,amount):self.now+=amount

def test_metrics_waits_interruptibly_and_never_claims_endurance(tmp_path):
    stop=Stop();path=tmp_path/'observations.jsonl';count=0
    def collect():
        nonlocal count
        count+=1
        return {'cpu_total_ticks':count*100,'cpu_idle_ticks':count*40,'processes':[{'pid':3,'start_ticks':4 if count==1 else 5}]}
    result=run(collect,path,duration=2,interval=1,clock=lambda:stop.now,stop=stop)
    rows=[json.loads(r) for r in path.read_text().splitlines()]
    assert result['samples']==3 and not result['endurance_qualified']
    assert rows[2]['observation']['host_cpu_busy_fraction']==.6
    assert rows[2]['observation']['processes'][0]['reason']=='pid_reused'
    assert rows[-1]['reason']=='duration_reached'
    with pytest.raises(FileExistsError):run(collect,path)

def test_interrupted_short_report_preserves_samples(tmp_path):
    stop=Stop()
    def collect():stop.stopped=True;return {'status':'observed'}
    path=tmp_path/'short';run(collect,path,duration=10,stop=stop,clock=lambda:stop.now)
    assert json.loads(path.read_text().splitlines()[-1])['reason']=='interrupted'

@pytest.mark.parametrize('url',['http://192.168.1.2/stream','http://localhost/stream','file:///x','https://u:p@example.org/a','https://example.org/a?token=x','https://example.org/a#x','https://example.org/\\x'])
def test_mount_policy_not_a_global_ssrf_exception(url):
    with pytest.raises(ValueError):endpoint(url,True)

def test_loopback_exception_explicit_and_ipv6():
    with pytest.raises(ValueError):endpoint('http://127.0.0.1:8000/a')
    assert endpoint('http://[::1]:8000/a',True)
    assert endpoint('https://test.example.org/stream/nova')

class Response:
    def __init__(self,status=200,headers=None,body=b'x'*8192):self.status_code=status;self.headers=headers or {'Content-Type':'audio/mpeg'};self.body=body
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def iter_content(self,*a):yield self.body
class Session:
    def __init__(self,response):self.response=response
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def get(self,url,**kwargs):assert kwargs['allow_redirects'] is False;assert self.trust_env is False;return self.response

def test_mount_bytes_decode_and_audience_are_distinct():
    value=radio('https://test.example.org/a',session_factory=lambda:Session(Response()),decoder=lambda b:'FAIL')
    assert value['mount']=='PASS' and value['client_bytes']==8192 and value['sample_decode']=='FAIL' and value['audience_heard']=='NOT_TESTED'

@pytest.mark.parametrize('status',[302,404,429,503])
def test_bad_status_not_success_or_redirect(status):
    value=radio('https://test.example.org/a',session_factory=lambda:Session(Response(status,{'Retry-After':'120'})))
    assert value['mount']=='FAIL' and value['client_bytes']==0
    if status==429:assert value['retry_after_seconds']==120

def test_429_embargo_not_shortened_or_retried():
    now=[0];calls=[]
    def probe(*a):calls.append(a);return {'mount':'FAIL','retry_after_seconds':300}
    collect=collector({'android':'https://test.example.org/a'},probe=probe,clock=lambda:now[0]);collect();now[0]=60
    assert collect()['channels']['android']['reason']=='provider_embargo' and len(calls)==1
    now[0]=300;collect();assert len(calls)==2

def test_child_timeout_no_secret_environment():
    import subprocess
    def fake(*args,**kwargs):
        assert kwargs['timeout']==8 and set(kwargs['env'])<= {'PATH','LANG'}
        assert 'test.example.org' not in ' '.join(args[0]);raise subprocess.TimeoutExpired(args[0],8)
    value=bounded('https://test.example.org/a',runner=fake);assert value['mount']=='FAIL'

def test_decoder_no_remote_protocol_and_timeout():
    class Result:returncode=0
    def fake(command,**kwargs):assert command[command.index('-protocol_whitelist')+1]=='file,pipe';assert kwargs['timeout']==3;return Result()
    assert decode_sample(b'x'*8192,runner=fake)=='PASS'
    assert decode_sample(b'')=='FAIL'

def test_inventory_not_tested_is_not_clean(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS",raising=False)
    from deploy.security_inventory import summarize,run as inventory
    assert summarize({'matches':[{'vulnerability':{'severity':'High'}}],'descriptor':{}})['classification']=='potential_pending_applicability_review'
    assert summarize({'matches':[],'descriptor':{}})['status']=='PASS_WITHIN_SCANNER_COVERAGE'
    with pytest.raises(ValueError):summarize({})
    with pytest.raises(ValueError):inventory('runtime','/tmp/never-started-field-inventory')

def test_failure_windows_are_not_uptime_or_exact_outages():
    from deploy.field_report import summarize
    rows=[{'kind':'start','version':1}]
    for when,ok in [(0,True),(60,False),(120,False),(180,True)]:
        rows.append({'kind':'sample','version':1,'elapsed_seconds':when,'observation':{'channels':{'android':{'mount':'PASS' if ok else 'FAIL','client_bytes':8192 if ok else 0,'sample_decode':'PASS' if ok else 'NOT_TESTED'}}}})
    value=summarize(rows)
    assert not value['end_record_present'] and value['radio_24h']=='NOT_TESTED'
    assert value['channels']['android']['observed_failure_windows']==[{'first_failed_sample_seconds':60,'first_successful_sample_seconds':180}]
    assert value['maximum_sample_gap_seconds']==60
    rows[-1]['elapsed_seconds']=10
    with pytest.raises(ValueError):summarize(rows)

def test_corrupt_jsonl_preserved_and_no_tokens_in_summary(tmp_path):
    from deploy.field_report import load
    path=tmp_path/'log';path.write_text('{broken')
    with pytest.raises(ValueError):load(path)
    assert path.read_text()=='{broken'

def test_physical_status_cannot_be_inferred_from_all_ci():
    value=fresh(S,A);value['gates']['code_ci']={'status':'PASS','evidence':[proof()]}
    assert assess(value)['gates']['installation_a']=='NOT_TESTED'
    assert assess(value)['public']=='NO_GO'
