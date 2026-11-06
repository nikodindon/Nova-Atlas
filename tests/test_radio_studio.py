"""Offline durable state, crash points, streams and bridge. No service/model."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import threading
import pytest
from modules.radio.studio import StudioJournal,RadioStudio,StudioError,StudioConflict,calendar,lease
from modules.radio import audio_integrity as ai,transport
from modules.radio.streamer import Streamer,AndroidStreamer
from modules.radio.tts_bridge import TTSBridge,NativeSpeech
from tests.test_radio_integrity import consumers,publish


@pytest.fixture
def journal(tmp_path):return StudioJournal(tmp_path/'data',tmp_path/'queue',clock=lambda:1793971800)


def publication(journal,slot='2026-11-06T14:30'):
    job=journal.admit(slot);journal.start(job['id']);journal.queue.mkdir(exist_ok=True)
    staged=journal.queue/'stage.mp3';staged.write_bytes(b'synthetic validated MP3 fixture')
    path=ai.publish_audio(staged,journal.path_for(job),ai.AudioInfo(3),2)
    return job,path


def attached(journal):
    row,path=publication(journal);journal.attach(row['id'],path);return row,path


def test_calendar_dst_no_nonexistent_or_duplicate_civil_hours():
    paris=ZoneInfo('Europe/Paris')
    spring=calendar([1,2,3],datetime(2026,3,29,0,tzinfo=paris))
    assert not any(row['slot'].startswith('2026-03-29T02') for row in spring)
    target=next(r for r in spring if r['slot'].endswith('03:00'))
    assert target['generation_planned_at'].startswith('2026-03-29T01:55')
    fall=calendar([2],datetime(2026,10,25,0,tzinfo=paris));assert len(fall)==2 and len({r['slot'] for r in fall})==2


def test_slot_keys_stable_and_different_dates(journal):
    first=journal.admit('2026-12-31T23:30');assert journal.admit('2026-12-31T23:30') is None
    second=journal.admit('2027-01-01T23:30');assert first['id']!=second['id']
    restarted=StudioJournal(journal.root.parent,journal.queue)
    assert restarted.admit('2027-01-01T23:30') is None


def test_long_running_job_consumes_next_slot_with_explicit_reason(journal):
    row=journal.admit('2026-11-06T14:30');journal.start(row['id'])
    skipped=journal.admit('2026-11-06T15:00')
    assert skipped['status']=='skipped' and skipped['reason']=='previous_generation_active'
    assert journal.admit('2026-11-06T15:00') is None


def test_generation_lease_exclusive_and_not_deleted(journal):
    with lease(journal.root/'generation.lock'):
        inode=(journal.root/'generation.lock').stat().st_ino
        with pytest.raises(StudioConflict):
            with lease(journal.root/'generation.lock'):pass
    with lease(journal.root/'generation.lock'):assert (journal.root/'generation.lock').stat().st_ino==inode


def test_atomic_write_failure_keeps_original(journal,monkeypatch):
    journal.admit('2026-11-06T14:30');before=journal.path.read_bytes()
    monkeypatch.setattr('modules.radio.studio.atomic_write',Mock(side_effect=OSError('interruption')))
    with pytest.raises(OSError):journal.admit('2026-11-06T15:00')
    assert journal.path.read_bytes()==before

@pytest.mark.parametrize('raw',['{broken','{}','{"version":1,"version":2}'])
def test_corrupt_journal_preserved_no_overwrite(journal,raw):
    journal.init();journal.path.write_text(raw)
    with pytest.raises(StudioError):journal.admit('2026-11-06T14:30')
    assert journal.path.read_text()==raw


def test_unknown_private_fields_rejected(journal):
    row=journal.admit('2026-11-06T14:30');data=json.loads(journal.path.read_text());data['jobs'][row['id']]['prompt']='must not expose this';journal.path.write_text(json.dumps(data))
    with pytest.raises(StudioError):journal.snapshot()


def test_restart_mid_generation_marks_interrupted_without_retry(journal):
    row=journal.admit('2026-11-06T14:30');journal.start(row['id'])
    restarted=StudioJournal(journal.root.parent,journal.queue);restarted.begin_run()
    state=restarted.load()[0]['jobs'][row['id']]
    assert state['status']=='interrupted' and state['attempts']==1
    assert restarted.admit(row['slot']) is None


def test_crash_after_publication_before_notification_recovers_ready(journal):
    row,path=publication(journal)
    # No attach: final MP3/receipt were published, then generator died.
    restarted=StudioJournal(journal.root.parent,journal.queue);restarted.begin_run()
    recovered=restarted.load()[0]['jobs'][row['id']]
    assert recovered['status']=='ready' and recovered['publication']['sha256']==json.loads(ai.receipt_path(path).read_text())['sha256']
    assert recovered['channels']=={} and ai.validated_publication(path)


def test_crash_after_queue_before_play_requires_explicit_recovery(journal):
    row,path=attached(journal);journal.channel_event(path,'primary','queued')
    restarted=StudioJournal(journal.root.parent,journal.queue);restarted.begin_run()
    data,version=restarted.load();assert data['jobs'][row['id']]['status']=='recovery_required'
    with pytest.raises(StudioConflict):restarted.control('resume',version,identity=row['id'])
    restarted.control('resume',version,identity=row['id'],ack=True)
    assert restarted.load()[0]['jobs'][row['id']]['resume_requested']


def test_completed_local_pass_never_automatically_replayed(journal):
    row,path=attached(journal)
    for phase in ('queued','playing','locally_sent'):journal.channel_event(path,'primary',phase,bytes_sent=123)
    restarted=StudioJournal(journal.root.parent,journal.queue);restarted.begin_run()
    assert restarted.load()[0]['jobs'][row['id']]['status']=='completed'
    with pytest.raises(StudioConflict):restarted.control('resume',restarted.load()[1],identity=row['id'],ack=True)
    assert restarted.admit(row['slot']) is None

@pytest.mark.parametrize('corrupt',['missing_receipt','corrupt_mp3','missing_mp3'])
def test_invalid_publication_never_reconciled_as_ready(journal,corrupt):
    row,path=publication(journal)
    if corrupt=='missing_receipt':ai.receipt_path(path).unlink()
    elif corrupt=='corrupt_mp3':path.write_bytes(b'changed')
    else:path.unlink()
    journal.reconcile();assert journal.load()[0]['jobs'][row['id']]['status']=='interrupted'
    with pytest.raises(StudioError):journal.attach(row['id'],path)


def test_known_publication_becomes_corrupt_requires_recovery(journal):
    row,path=attached(journal);path.write_bytes(b'broken');journal.reconcile()
    assert journal.load()[0]['jobs'][row['id']]['status']=='recovery_required'


def test_current_runtime_playback_cannot_be_reset_by_control(journal):
    journal.begin_run();row,path=attached(journal);journal.channel_event(path,'primary','queued')
    journal.reconcile();assert journal.load()[0]['jobs'][row['id']]['status']=='queued'
    with pytest.raises(StudioConflict):journal.control('resume',journal.load()[1],identity=row['id'],ack=True)


def test_both_channels_durable_android_preserved_after_primary_failure(journal):
    primary,android=consumers(journal.queue);primary._stop_event=threading.Event()
    runtime=RadioStudio(journal,primary,android,threading.Event());row,path=attached(journal)
    assert runtime.enqueue(row['id']);assert ai.validated_publication(android._current)
    primary._stream_file=Mock(return_value=False);primary._play_next()
    assert path.exists() and ai.validated_publication(android._current)
    state=journal.load()[0]['jobs'][row['id']]
    assert state['status']=='recovery_required' and state['channels']['android']['status']=='queued'
    assert primary.enqueue_bulletin(path) is False


def test_primary_success_records_local_completion_without_deletion(journal):
    primary,android=consumers(journal.queue);primary._stop_event=threading.Event();runtime=RadioStudio(journal,primary,android,threading.Event())
    row,path=attached(journal);runtime.enqueue(row['id']);primary._stream_file=Mock(return_value=True);primary._play_next()
    assert path.exists() and journal.load()[0]['jobs'][row['id']]['status']=='completed'
    assert journal.snapshot()['metrics']['completed_local_pass']==1


def test_actual_generation_orchestrated_and_deduplicated(journal):
    primary,android=consumers(journal.queue);stop=threading.Event();runtime=RadioStudio(journal,primary,android,stop)
    def build(articles,**kwargs):
        journal.queue.mkdir(exist_ok=True);p=journal.queue/'staged.mp3';p.write_bytes(b'synthetic')
        kwargs['observer']('script_validated',words=800,segments=2)
        return ai.publish_audio(p,journal.queue/kwargs['publication_name'],ai.AudioInfo(3),2)
    builder=SimpleNamespace(bulletins_cfg={'min_articles':1},build=Mock(side_effect=build))
    assert runtime.generate('2026-11-06T14:30',[{'hash':'a'*32}],builder)
    assert runtime.generate('2026-11-06T14:30',[{'hash':'a'*32}],builder) is None
    assert builder.build.call_count==1 and primary._play_queue.qsize()==1


def test_stop_before_generation_no_llm_or_tts(journal):
    primary,android=consumers(journal.queue);stop=threading.Event();stop.set();runtime=RadioStudio(journal,primary,android,stop)
    builder=SimpleNamespace(bulletins_cfg={'min_articles':0},build=Mock())
    assert runtime.generate('2026-11-06T14:30',[],builder) is None
    builder.build.assert_not_called()
    assert next(iter(journal.load()[0]['jobs'].values()))['status']=='interrupted'


def test_disable_and_conflict_no_active_generation_killed(journal):
    journal.admit('2026-11-06T14:30');version=journal.load()[1]
    with pytest.raises(StudioConflict):journal.control('disable','0'*64)
    journal.control('disable',version);assert journal.admit('2026-11-06T15:00') is None
    with lease(journal.root/'generation.lock'),pytest.raises(StudioConflict):journal.control('enable',journal.load()[1])


def test_unknown_voice_piper_unqualified_and_fallback_disabled(tmp_path):
    bridge=TTSBridge({'bulletin':{'configured':{'engine':'piper','voice':'siwis','qualified':True}}})
    for voice in ('unknown','configured'):
        with pytest.raises(ai.AudioIntegrityError):bridge.synthesize('Texte complet.',voice,tmp_path/'segment.mp3')


def test_injected_native_wav_bridge_and_explicit_fallback_preserves_text(tmp_path):
    calls=[]
    class Provider:
        def __init__(self,fail=False):self.fail=fail
        def synthesize(self,text,voice,path,**kwargs):
            calls.append((text,voice))
            if self.fail:raise ValueError('provider internal secret')
            path.write_bytes(b'synthetic WAV for codec mock');return NativeSpeech(path,'wav')
    def convert(native,out,timeout):out.write_bytes(b'converted MP3')
    profiles={'bulletin':{'voice':{'engine':'test-primary','voice':'primary','qualified':True,'fallback_enabled':True,'fallbacks':[{'engine':'test-secondary','voice':'secondary','qualified':True}]}}}
    seen=[];bridge=TTSBridge(profiles,providers={'test-primary':Provider(True),'test-secondary':Provider()},converter=convert,inspector=Mock(),observer=lambda *a,**kw:seen.append(kw))
    text=('Texte français sans perte. '*1000)
    assert bridge.synthesize(text,'voice',tmp_path/'segment.mp3')
    assert calls==[(text,'primary'),(text,'secondary')] and seen[0]['fallback'] is True
    profiles['bulletin']['voice']['fallback_enabled']=False;calls.clear()
    with pytest.raises(ai.AudioIntegrityError):bridge.synthesize(text,'voice',tmp_path/'segment.mp3')
    assert len(calls)==1 and not (tmp_path/'segment.mp3').exists()


def test_fixture_tts_never_published_and_cancel_cleaned(tmp_path):
    class Provider:
        def synthesize(self,text,voice,path,**kwargs):path.write_bytes(b'fixture');return NativeSpeech(path,'wav','synthetic_fixture')
    bridge=TTSBridge({'bulletin':{'voice':{'engine':'mock','voice':'v','qualified':True}}},providers={'mock':Provider()},inspector=Mock())
    with pytest.raises(ai.AudioIntegrityError):bridge.synthesize('Texte.','voice',tmp_path/'seg.mp3')
    assert not (tmp_path/'seg.mp3').exists()
    cancel=threading.Event();cancel.set()
    with pytest.raises(ai.AudioIntegrityError):bridge.synthesize('Texte.','voice',tmp_path/'seg.mp3',cancel=cancel)
    assert not list(tmp_path.glob('.tts-*'))


def test_bridge_real_converter_enforces_local_protocol_and_timeout(tmp_path,monkeypatch):
    import subprocess
    calls=[]
    def run(command,**kwargs):calls.append((command,kwargs));return Mock(returncode=0)
    monkeypatch.setattr(subprocess,'run',run)
    TTSBridge.convert(NativeSpeech(tmp_path/'native','wav'),tmp_path/'seg.mp3',5)
    cmd,options=calls[0];assert cmd[cmd.index('-protocol_whitelist')+1]=='file,pipe' and '-f' in cmd and options['timeout']==5
    monkeypatch.setattr(subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('codec',5)))
    with pytest.raises(ai.AudioIntegrityError):TTSBridge.convert(NativeSpeech(tmp_path/'native','wav'),tmp_path/'seg.mp3',5)


def test_transport_backoff_no_orphan_and_no_false_connected_claim(tmp_path,monkeypatch):
    proc=Mock();proc.poll.return_value=None;proc.stdin.fileno.return_value=99
    owner=SimpleNamespace(_stop_event=threading.Event(),_ffmpeg_proc=None,_lock=threading.Lock(),bitrate='128k',sample_rate=44100,channels=2,icecast_url='icecast://synthetic:private@local.invalid/nova')
    monkeypatch.setattr(transport.os,'set_blocking',lambda *a:None)
    factory=Mock(return_value=proc);monkeypatch.setattr(transport.subprocess,'Popen',factory)
    assert transport.start_source(owner,'Test') is True
    proc.poll.return_value=1
    assert transport.start_source(owner,'Test') is False
    assert factory.call_count==1
    owner._retry_at=0;assert transport.start_source(owner,'Test') is True
    assert owner._transport_failures==1 and owner._retry_at>0
    transport.stop_process(proc);assert proc.stdout.close.called


def test_transport_timeout_kills_owned_decoder_and_source(monkeypatch):
    import subprocess
    proc=Mock();proc.poll.return_value=None;proc.wait.side_effect=[subprocess.TimeoutExpired('owned',2),0]
    transport.stop_process(proc);proc.terminate.assert_called_once();proc.kill.assert_called_once();assert proc.wait.call_count==2


def test_no_music_invalid_music_and_symlink_skipped(tmp_path,monkeypatch):
    primary,_=consumers(tmp_path);primary.music_dir=tmp_path/'music';assert primary._pick_music() is None
    primary.music_dir.mkdir();(primary.music_dir/'bad.mp3').write_bytes(b'bad')
    (primary.music_dir/'link.mp3').symlink_to(primary.music_dir/'bad.mp3')
    inspect=Mock(side_effect=ai.AudioIntegrityError('invalid'))
    monkeypatch.setattr('modules.radio.streamer.inspect_audio',inspect)
    assert primary._pick_music() is None and inspect.call_count==1
    assert primary._pick_music() is None and inspect.call_count==1


def test_observations_are_not_icecast_delivery(journal):
    journal.observe('primary','ffmpeg_started',epoch=1,delay=2);journal.observe('primary','local_bytes',bytes=1024)
    info=journal.snapshot()['channels']['primary'];assert info['bytes_local_pipe']==1024
    assert info['icecast_connection_verified'] is None and info['audience_delivery_verified'] is None


def test_symlink_journal_and_path_escape_refused(tmp_path):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'linked';target.symlink_to(data,target_is_directory=True)
    with pytest.raises(StudioError):StudioJournal(target,tmp_path/'q')


@pytest.mark.parametrize('chunks,code,writer,expected',[([b'abc',b''],0,True,True),([b'abc',b''],1,True,False),([b''],0,True,False),([b'abc'],0,False,False)])
def test_local_playback_requires_bytes_full_decode_and_live_source(monkeypatch,chunks,code,writer,expected):
    decoder=Mock();decoder.stdout.fileno.return_value=41;decoder.wait.return_value=code;decoder.poll.return_value=code
    source=Mock();source.poll.return_value=None
    owner=SimpleNamespace(_start_ffmpeg=lambda:True,_stop_event=threading.Event(),_ffmpeg_proc=source,_source_epoch=1,_write_to_pipe=Mock(return_value=writer),bitrate='128k',sample_rate=44100,channels=2)
    monkeypatch.setattr(transport.subprocess,'Popen',Mock(return_value=decoder))
    monkeypatch.setattr(transport.os,'set_blocking',lambda *a:None)
    monkeypatch.setattr(transport.select,'select',lambda *a:([41],[],[]))
    values=iter(chunks);monkeypatch.setattr(transport.os,'read',lambda *a:next(values))
    assert transport.stream_file(owner,Path('/synthetic.mp3')) is expected
    assert decoder.stdout.close.called


def test_broken_pipe_is_failure_not_success_or_unbounded_restart(monkeypatch):
    source=Mock();source.poll.return_value=None;source.stdin.fileno.return_value=42
    owner=SimpleNamespace(_ffmpeg_proc=source,_pipe_lock=threading.Lock(),_stop_event=threading.Event())
    monkeypatch.setattr(transport.select,'select',lambda *a:([],[42],[]))
    monkeypatch.setattr(transport.os,'write',Mock(side_effect=BrokenPipeError()))
    assert transport.write_pipe(owner,b'audio') is False and owner._source_fault is True


def test_android_recovers_previous_valid_when_latest_cache_corrupt(tmp_path):
    _,android=consumers(tmp_path);old=publish(tmp_path);assert android.enqueue_bulletin(old)
    saved=android._current;new=publish(tmp_path);assert android.enqueue_bulletin(new)
    android._pending.write_bytes(b'corrupt')
    recovered=android._load_last_bulletin(android._config)
    assert recovered==saved and ai.validated_publication(recovered)


def _admit_child(data,queue,pipe):
    journal=StudioJournal(data,queue)
    row=journal.admit('2026-11-06T14:30');pipe.send(row is not None);pipe.close()


def test_two_processes_only_one_slot_admission(tmp_path):
    import multiprocessing
    context=multiprocessing.get_context('fork');pairs=[];children=[]
    for _ in range(2):
        receiver,sender=context.Pipe(duplex=False);process=context.Process(target=_admit_child,args=(tmp_path/'data',tmp_path/'q',sender));process.start();sender.close();pairs.append(receiver);children.append(process)
    try:
        results=[]
        for receiver in pairs:assert receiver.poll(5);results.append(receiver.recv());receiver.close()
        assert sorted(results)==[False,True]
    finally:
        for process in children:
            process.join(5)
            if process.is_alive():process.terminate();process.join(2)
            assert process.exitcode==0


def _hold_lease_child(path,pipe):
    with lease(path):pipe.send('held');pipe.close();threading.Event().wait(20)


def test_dead_process_lease_recovered_without_unlink(journal):
    import multiprocessing
    context=multiprocessing.get_context('fork');receiver,sender=context.Pipe(duplex=False)
    path=journal.root/'generation.lock';process=context.Process(target=_hold_lease_child,args=(path,sender));process.start();sender.close()
    try:
        assert receiver.poll(5) and receiver.recv()=='held';inode=path.stat().st_ino
        process.terminate();process.join(5);assert not process.is_alive()
        with lease(path):assert path.stat().st_ino==inode
    finally:
        receiver.close()
        if process.is_alive():process.kill();process.join(2)


def test_manifest_records_actual_selected_fallback_voice(tmp_path,monkeypatch):
    from modules.radio import bulletin_generator as bg
    from modules.radio.tts_bridge import SpeechResult
    def synth(text,voice,path,**kwargs):path.write_bytes(b'valid fake codec');return SpeechResult('fr-FR-DeniseNeural','edge',True)
    manifest=[]
    paths=bg.synthesize_all_segments([('[VOIX1]','Texte complet.')],{'[VOIX1]':'fr-FR-HenriNeural'},tmp_path,manifest=manifest,synthesizer=synth,inspector=lambda p:ai.AudioInfo(3))
    assert paths and manifest[0].voice=='fr-FR-DeniseNeural' and manifest[0].voice_tag=='[VOIX1]'


def test_android_idle_silence_paced_one_second_without_flooding_pipe():
    android=AndroidStreamer.__new__(AndroidStreamer);android._silence_bytes=b'one-second-frame';android._current=None
    android._stop_event=threading.Event();android._current_done=Mock();android._write_to_pipe=Mock(return_value=True)
    android._current_done.wait.side_effect=lambda **kwargs:android._stop_event.set()
    android._send_silence_loop()
    android._write_to_pipe.assert_called_once_with(b'one-second-frame')
    android._current_done.wait.assert_called_once_with(timeout=1)
