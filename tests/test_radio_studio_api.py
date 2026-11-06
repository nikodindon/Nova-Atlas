import json
import pytest
from tests.test_admin_security import app,password_hash,client,get,post,login,csrf
from tests.test_radio_studio import attached


def test_private_studio_auth_csrf_and_disabled_has_no_writes(client,app):
    journal=app.extensions['atlas_radio_studio']
    assert get(client,'/api/admin/radio').status_code==401
    assert get(client,'/radio-studio').status_code in (303,401)
    assert login(client).status_code==303
    status=get(client,'/api/admin/radio');assert status.status_code==200 and not status.json['configured']
    assert not journal.root.exists()
    assert post(client,'/api/admin/radio/control',json={'action':'disable','version':status.json['version']}).status_code==403
    response=post(client,'/api/admin/radio/control',json={'action':'disable','version':status.json['version']},headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==400
    assert 'synthetic-icecast-credential' not in status.text and 'synthetic-session' not in status.text


def test_admin_dry_run_calendar_does_not_generate_or_write(client,app):
    journal=app.extensions['atlas_radio_studio'];journal.configured=True;login(client)
    version=journal.load()[1]
    response=post(client,'/api/admin/radio/control',json={'action':'dry-run','version':version},headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==200 and response.json['dry_run'] and response.json['generation'] is False
    assert not journal.root.exists()
    assert get(client,'/radio-studio').status_code==200


def test_admin_control_version_reconcile_and_resume(client,app):
    journal=app.extensions['atlas_radio_studio'];journal.configured=True;row,path=attached(journal);login(client);headers={'X-CSRF-Token':csrf(client)}
    response=post(client,'/api/admin/radio/control',json={'action':'disable','version':'0'*64},headers=headers);assert response.status_code==409
    version=journal.load()[1]
    assert post(client,'/api/admin/radio/control',json={'action':'reconcile','version':version},headers=headers).status_code==200
    assert journal.load()[0]['reconcile_requested'] is True
    # Manual resume is scoped to a known, intact publication; no arbitrary path or generation.
    response=post(client,'/api/admin/radio/control',json={'action':'resume','version':journal.load()[1],'id':row['id'],'ack':True},headers=headers)
    assert response.status_code==200
    job=get(client,'/api/admin/radio/jobs/'+row['id']);assert job.json['job']['status']=='ready'
    assert str(journal.root) not in job.text and str(path.parent) not in job.text
    assert 'prompt' not in job.text

@pytest.mark.parametrize('payload',[{'action':'generate','version':'0'*64},{'action':'resume','version':'0'*64,'id':['bad']},{'action':'resume','version':'0'*64,'ack':'yes'},{'action':'resume','version':'0'*64,'path':'/etc/passwd'}])
def test_malformed_control_refused(client,payload):
    login(client)
    assert post(client,'/api/admin/radio/control',json=payload,headers={'X-CSRF-Token':csrf(client)}).status_code==400


def test_journal_corrupt_api_preserves_original(client,app):
    journal=app.extensions['atlas_radio_studio'];journal.configured=True;journal.init();journal.path.write_text('{invalid');login(client)
    response=get(client,'/api/admin/radio');assert response.status_code==400 and journal.path.read_text()=='{invalid'
    assert 'Traceback' not in response.text and str(journal.root) not in response.text


def test_stale_channel_observation_not_healthy(client,app):
    journal=app.extensions['atlas_radio_studio'];journal.configured=True;journal.clock=lambda:1000000000
    journal.observe('primary','ffmpeg_started',epoch=1,delay=1);journal.clock=lambda:1000001000
    login(client);response=get(client,'/api/admin/radio')
    channel=response.json['channels']['primary'];assert channel['fresh'] is False and channel['icecast_connection_verified'] is None
    assert channel['observation_age_seconds']==1000


def test_private_durable_audio_after_flash_process_memory_loss(client,app):
    from modules.radio.audio_integrity import publish_audio,AudioInfo
    journal=app.extensions['atlas_radio_studio'];journal.configured=True
    row=journal.admit('manual:20261106_143000:abcdef012345',kind='flash');journal.start(row['id'])
    staged=journal.queue/'stage.mp3';staged.write_bytes(b'synthetic-valid-audio')
    path=publish_audio(staged,journal.path_for(row),AudioInfo(3),1);journal.attach(row['id'],path)
    url='/api/admin/radio/jobs/'+row['id']+'/audio'
    assert get(client,url).status_code==401
    login(client);response=get(client,url);assert response.status_code==200 and response.mimetype=='audio/mpeg';response.close()
    path.write_bytes(b'changed')
    assert get(client,url).status_code==400


def test_explicit_integrity_verification_logged_without_regeneration(client,app):
    journal=app.extensions['atlas_radio_studio'];journal.configured=True;row,path=attached(journal)
    login(client);headers={'X-CSRF-Token':csrf(client)}
    response=post(client,'/api/admin/radio/control',json={'action':'verify','version':journal.load()[1],'id':row['id']},headers=headers)
    assert response.status_code==200 and journal.load()[0]['jobs'][row['id']]['verification']['valid'] is True
    path.write_bytes(b'corrupt')
    response=post(client,'/api/admin/radio/control',json={'action':'verify','version':journal.load()[1],'id':row['id']},headers=headers)
    assert response.status_code==200 and journal.load()[0]['jobs'][row['id']]['verification']['valid'] is False
    assert journal.load()[0]['controls'][-1]['action']=='verify'
