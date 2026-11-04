"""Synthetic deployment, no daemon or real service; CLI config is read-only."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET
import yaml
import pytest
from deploy import runtime,entrypoint,healthcheck,ollama_client
from deploy.init import initialize
import main

ROOT=Path(__file__).resolve().parents[1]
_REAL_POPEN=subprocess.Popen


@pytest.fixture
def deployment(tmp_path,monkeypatch):
    previous_umask=os.umask(0o077);os.umask(previous_umask)
    root=tmp_path/'installation'
    initialize(root,'synthetic-admin','synthetic-password-for-tests',llm_url='http://trusted.example.invalid:8080',model='synthetic-model')
    (root/'tmp').mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime,'ROOT',root)
    monkeypatch.setattr(runtime,'SECRETS',root/'secrets')
    monkeypatch.setattr(runtime,'PID_FILE',root/'tmp/supervisor.json')
    monkeypatch.setenv('ATLAS_CONTAINER','1');monkeypatch.setenv('ATLAS_ROLE','web')
    yield root
    for fd in runtime._ROLE_LEASES:
        os.close(fd)
    runtime._ROLE_LEASES.clear()
    os.umask(previous_umask)


def test_initializer_no_plain_password_no_overwrite_and_private_modes(deployment):
    assert 'synthetic-password-for-tests' not in (deployment/'secrets/admin_password_hash').read_text()
    assert (deployment/'secrets/admin_password_hash').stat().st_mode & 0o777 == 0o400
    assert 'password:' not in (deployment/'config/config.yaml').read_text()
    assert (deployment/'config/feeds.yaml').read_text()=='{}\n'
    before=(deployment/'secrets/icecast.xml').read_bytes()
    with pytest.raises(ValueError):initialize(deployment,'other','different-long-password')
    assert (deployment/'secrets/icecast.xml').read_bytes()==before


@pytest.mark.parametrize('role',['web','news','radio'])
def test_entrypoint_selects_one_existing_mode_without_shell(deployment,monkeypatch,role):
    args=entrypoint.prepare(role)
    assert args==[sys.executable,str(deployment/'main.py'),'--'+role,'--config',str(deployment/'config/config.yaml')]
    assert json.loads(runtime.PID_FILE.read_text())=={'pid':os.getpid(),'role':role}
    assert runtime._ROLE_LEASES and os.get_inheritable(runtime._ROLE_LEASES[-1])
    if role=='web':assert os.environ['ATLAS_ADMIN_USERNAME']=='synthetic-admin'


@pytest.mark.parametrize('mutation',['missing_secret','public_secret','bad_config','different_paths','different_lock','default_password','no_llm'])
def test_preflight_fails_closed_without_starting_any_process(deployment,monkeypatch,mutation):
    path=deployment/'config/config.yaml';cfg=yaml.safe_load(path.read_text())
    if mutation=='missing_secret':(deployment/'secrets/session_secret').unlink()
    if mutation=='public_secret':(deployment/'secrets/session_secret').chmod(0o644)
    if mutation=='bad_config':path.write_text('{broken')
    if mutation=='different_paths':cfg['paths']['data_dir']='other'
    if mutation=='different_lock':cfg['llm']['lock_dir']='other'
    if mutation=='default_password':cfg['icecast']['password']='unsafe-example'
    if mutation=='no_llm':cfg['llm']['base_url']=''
    if mutation not in ('bad_config','missing_secret','public_secret'):path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(runtime.DeploymentError):entrypoint.prepare('news' if mutation=='no_llm' else 'web')


def test_web_only_can_read_without_llm_but_workers_refuse(deployment,monkeypatch):
    path=deployment/'config/config.yaml';cfg=yaml.safe_load(path.read_text());cfg['llm']['base_url']='';path.write_text(yaml.safe_dump(cfg))
    entrypoint.prepare('web')
    monkeypatch.setenv('ATLAS_ROLE','radio')
    with pytest.raises(runtime.DeploymentError):runtime.load_container_config(path)


def test_container_loader_never_falls_back_to_defaults(deployment):
    from modules.core.config import load_config
    (deployment/'config/config.yaml').unlink()
    with pytest.raises(runtime.DeploymentError):load_config(deployment/'config/config.yaml')


def test_one_role_per_shared_data_inode(deployment):
    runtime.acquire_role('news')
    inode=(deployment/'data/service-news.lock').stat().st_ino
    with pytest.raises(runtime.DeploymentError):runtime.acquire_role('news')
    assert (deployment/'data/service-news.lock').stat().st_ino==inode
    runtime.acquire_role('radio')


def test_icecast_secrets_match_streamers_and_two_mounts(deployment):
    config=runtime.load_container_config(deployment/'config/config.yaml')
    tree=ET.fromstring((deployment/'secrets/icecast.xml').read_text())
    assert tree.findtext('authentication/source-password')==config['icecast']['password']
    assert len(tree.findtext('authentication/admin-password'))==64
    assert {m.findtext('mount-name') for m in tree.findall('mount')}=={'/nova','/nova-android'}
    assert tree.findtext('logging/accesslog')=='/dev/null'
    assert tree.findtext('paths/logdir')=='/tmp/icecast'
    assert 'chmod 700 /tmp/icecast' in (ROOT/'deploy/icecast-entrypoint.sh').read_text()


def test_configuration_ui_cannot_store_managed_credentials(deployment):
    from modules.web.config_security import update_yaml
    with pytest.raises(ValueError):update_yaml(deployment/'config/config.yaml','icecast','password','some-new-password')


def test_storage_check_leaves_existing_files_untouched(deployment):
    data=deployment/'data/article_processing.json';data.write_text('historical bytes')
    runtime.check_storage('web')
    assert data.read_text()=='historical bytes'
    assert not list(deployment.rglob('.health-*'))


@pytest.mark.parametrize('url',['http://host.docker.internal:8080','http://100.64.1.2:11434','https://llm.example.invalid'])
def test_trusted_service_urls_separate_from_external_rss(url):
    assert runtime.trusted_url(url)


@pytest.mark.parametrize('url',['http://user:password@host/','file:///secret','http://host/?api_key=hidden','http://host/\n'])
def test_no_credentials_or_invalid_trusted_origin(url):
    assert not runtime.trusted_url(url)


def test_remote_ollama_adapter_only_generates_and_never_pulls(monkeypatch):
    response=Mock();response.read.return_value=b'{"response":"Synthetic result"}'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(ollama_client.urllib.request,'build_opener',Mock(return_value=opener))
    assert ollama_client.generate('existing-model','synthetic prompt','http://ollama:11434')=='Synthetic result'
    request=opener.open.call_args.args[0]
    assert request.full_url=='http://ollama:11434/api/generate'
    assert json.loads(request.data)['stream'] is False
    assert '/pull' not in request.full_url


def test_ollama_error_does_not_include_raw_server_content(monkeypatch):
    monkeypatch.setattr(sys,'argv',['ollama','pull','anything'])
    with pytest.raises(SystemExit) as result:ollama_client.main()
    assert result.value.code==1


def test_process_health_is_not_business_success(monkeypatch,deployment):
    monkeypatch.setattr(healthcheck,'process_alive',lambda _:False)
    runtime.PID_FILE.write_text(json.dumps({'pid':1234,'role':'news'}))
    assert healthcheck.check('news') is False


def test_compose_profiles_mounts_uid_ports_and_resolution(deployment,tmp_path):
    env={**os.environ,'DOCKER_CONFIG':str(tmp_path/'empty-docker'),'ATLAS_WEB_PORT':'55055'}
    command=['docker','compose','-f',str(ROOT/'docker-compose.yml'),'--env-file',str(deployment/'compose.env'),'--profile','full','config','--format','json']
    process=_REAL_POPEN(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    output,error=process.communicate(timeout=20)
    assert process.returncode==0,error.decode()
    config=json.loads(output);services=config['services']
    assert set(services)=={'web','news','radio','icecast'}
    assert config['networks']['backend']['internal'] is True
    for role in ('web','news','radio'):
        service=services[role]
        assert service['command']==[role]
        assert service['user']==f'{os.getuid()}:{os.getgid()}'
        assert service['read_only'] is True and service['restart']=='unless-stopped'
        assert service['stop_signal']=='SIGTERM' and service['stop_grace_period']=='45m0s'
        assert any(v['source']==str(deployment/'data') and v['target']=='/app/data' for v in service['volumes'])
        assert 'healthcheck' in service
    assert services['web']['ports'][0]['host_ip']=='127.0.0.1'
    assert services['web']['ports'][0]['published']=='55055'
    assert 'ports' not in services['icecast'] and 'ports' not in services['radio']
    assert services['icecast']['networks']=={'backend':None}
    assert services['radio']['depends_on']['icecast']['condition']=='service_healthy'
    assert services['web']['profiles']==['web','full']
    assert 'latest' not in (ROOT/'Dockerfile').read_text()


def test_image_context_is_allowlisted():
    dockerfile=(ROOT/'Dockerfile').read_text()
    assert 'COPY . ' not in dockerfile and 'COPY config ' not in dockerfile
    assert 'USER 10001:10001' in dockerfile and 'ffmpeg' in dockerfile
    ignore=(ROOT/'.dockerignore').read_text().splitlines()
    assert '**' in ignore and '!deploy/**' not in ignore
    assert '!requirements-runtime.lock' in ignore


def test_web_lifecycle_drains_requests_then_background_jobs(monkeypatch):
    from modules.web import lifecycle
    events=[]
    server=Mock();server.shutdown.side_effect=lambda:events.append('shutdown')
    server.server_close.side_effect=lambda:events.append('requests-drained')
    monkeypatch.setattr(lifecycle,'make_server',Mock(return_value=server))
    worker=Mock();worker.join.side_effect=lambda:events.append('server-joined')
    monkeypatch.setattr(lifecycle.threading,'Thread',Mock(return_value=worker))
    app=Mock();app.extensions={'atlas_background_jobs':[Mock(join=lambda:events.append('flash-drained'))]}
    stop=Mock();stop.wait.side_effect=lambda:events.append('stop')
    lifecycle.serve(app,'127.0.0.1',5055,stop)
    assert events==['stop','shutdown','server-joined','requests-drained','flash-drained']
    assert server.daemon_threads is False


def test_sigterm_during_initial_startup_drains_synthetic_job(tmp_path):
    # Separate Python process; replace NewsEngine BEFORE running main, never a real engine.
    ready=tmp_path/'ready';finished=tmp_path/'finished'
    code='''import main,time
from pathlib import Path
main.setup_logging=lambda *a:None
main.load_config=lambda *a:{}
def synthetic(config,debug=False,*,stop_event=None):
 Path(%r).write_text('ready')
 stop_event.wait()
 time.sleep(.03)
 Path(%r).write_text('complete')
main.run_news_engine=synthetic
main.main()
'''%(str(ready),str(finished))
    process=_REAL_POPEN([sys.executable,'-c',code,'--news'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.005)
        assert ready.exists()
        process.send_signal(signal.SIGTERM)
        _,err=process.communicate(timeout=5)
        assert process.returncode==0,err.decode()
        assert finished.read_text()=='complete'
    finally:
        if process.poll() is None:process.kill();process.wait()


@pytest.mark.parametrize('fstype',['nfs4','cifs','fuse.sshfs','9p','virtiofs'])
def test_remote_mounts_rejected(monkeypatch,fstype):
    monkeypatch.setattr(Path,'read_text',lambda *a,**kw:f'1 0 0:1 / / rw - {fstype} remote rw')
    with pytest.raises(runtime.DeploymentError):runtime.require_local_filesystem('/app/data')


def test_smoke_diagnostics_redact_generated_credentials(monkeypatch,capsys):
    from deploy import smoke
    monkeypatch.setattr(smoke,'_REDACTIONS',['synthetic-session-value','scrypt:synthetic-hash'])
    smoke.diagnostic('failure synthetic-session-value scrypt:synthetic-hash '+ 'a'*64)
    output=capsys.readouterr().out
    assert 'synthetic-session-value' not in output
    assert 'synthetic-hash' not in output
    assert 'a'*64 not in output
    assert 'failure' in output and output.count('[redacted]')==3
